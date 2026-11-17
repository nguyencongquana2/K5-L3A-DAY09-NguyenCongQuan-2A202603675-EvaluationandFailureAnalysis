"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import issue_refund
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.trace import Trace

SHAPE = "single"


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal_amount: int, error_reason: str | None = None) -> RunResult:
        # The model drafts text only after code/host has fixed the outcome.
        complete(
            budget,
            seed=seed,
            purpose="draft",
            prompt=f"status={status} proposal={proposal_amount}",
        )
        trace.add(
            "decision",
            {
                "status": status,
                "proposal_amount": proposal_amount,
                "reason": error_reason,
            },
        )
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal_amount,
            budget=budget,
            trace=trace,
            error_reason=error_reason,
        )

    # State belongs to the shape and never trusts instructions or delay values
    # from the ticket. The ReAct actor can select read tools only.
    state: dict[str, Any] = {"phase": "claim"}

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        phase = state["phase"]
        last_tool = observation.get("last_tool")
        last_result = observation.get("last_result") or {}

        # Count every agent decision as a seeded model call. The prompt contains
        # control state only, never a raw ticket or database row.
        complete(
            budget,
            seed=seed,
            purpose="decide",
            prompt=f"phase={phase} last_tool={last_tool or 'none'}",
        )

        if phase == "claim":
            state["phase"] = "order"
            trace.add("agent_action", {"tool": "get_claim_view"})
            return {"tool": "get_claim_view", "args": {"claim_id": claim_id}}

        if phase == "order":
            found = bool(last_result.get("found"))
            order_id = last_result.get("order_id")
            trace.add(
                "agent_observation",
                {"tool": "get_claim_view", "found": found, "has_order_id": bool(order_id)},
            )
            if not found:
                return {
                    "final": {
                        "status": "failed",
                        "proposal_amount": 0,
                        "error_reason": "unknown_claim",
                    }
                }
            if not order_id:
                return {
                    "final": {
                        "status": "needs_info",
                        "proposal_amount": 0,
                        "error_reason": "missing_order_id",
                    }
                }

            state["phase"] = "quote"
            trace.add("agent_action", {"tool": "lookup_order"})
            return {
                "tool": "lookup_order",
                "args": {
                    "order_id": order_id,
                    "principal_id": last_result["principal_id"],
                },
            }

        if phase == "quote":
            visible = bool(last_result.get("visible"))
            trace.add(
                "agent_observation",
                {
                    "tool": "lookup_order",
                    "visible": visible,
                    "reason": last_result.get("reason"),
                },
            )
            if not visible:
                return {
                    "final": {
                        "status": "escalated",
                        "proposal_amount": 0,
                        "error_reason": last_result.get("reason"),
                    }
                }

            state["phase"] = "final"
            trace.add("agent_action", {"tool": "compute_compensation"})
            return {
                "tool": "compute_compensation",
                "args": {"order": last_result["order"]},
            }

        # Money comes only from compute_compensation over the allowlisted order
        # row. Ticket claims and model output never influence this value.
        proposal = int(last_result.get("amount_vnd", 0))
        reason = last_result.get("reason")
        trace.add(
            "agent_observation",
            {"tool": "compute_compensation", "amount_vnd": proposal, "reason": reason},
        )
        return {
            "final": {
                "status": "ready" if proposal > 0 else "denied",
                "proposal_amount": proposal if proposal > 0 else 0,
                "error_reason": reason,
            }
        }

    react = run_react(
        conn,
        budget,
        observation={"claim": {"claim_id": claim_id}},
        decide=decide,
        max_steps=8,
        worker=True,
    )
    final = react["final"]
    trace.add("react_stop", {"stopped": react["stopped"], "status": final["status"]})

    status = final["status"]
    proposal = int(final.get("proposal_amount", 0))
    reason = final.get("error_reason")
    if status != "ready":
        return done(status, proposal, reason)

    # The ReAct agent never receives a write tool. Only deterministic shape
    # code asks the host to commit the FULL quote and enforce approval/caps.
    outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    trace.add(
        "issue_refund",
        {
            "ok": outcome["ok"],
            "reason": outcome["reason"],
            "conflict": outcome["conflict"],
        },
    )
    if not outcome["ok"]:
        return done(
            "failed" if outcome["conflict"] else "denied",
            proposal,
            outcome["reason"],
        )
    return done("resolved", proposal)
