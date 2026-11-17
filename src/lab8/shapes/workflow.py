"""Shape A — deterministic workflow. Student work.

Signature (do not change):

    run_claim(claim_id, *, seed, store_path, crash_at=None) -> RunResult

Suggested steps. Money stays in code (`lab8.tools.compute_compensation`).

1. Open the store. Read the claim through `get_claim_view` (not SELECT *).
2. Missing order_id -> status `needs_info`. Do not write the ledger.
3. `lookup_order`. If not visible -> `escalated`. Do not copy phone, address, item, internal note.
4. Quote from the order row. Ignore delay numbers written in the ticket.
5. Quote amount 0 -> `denied`. Do not call issue_refund.
6. Quote amount > 0 -> `durability.issue_refund(conn, claim_id, <full quote>)`.
   Pass the FULL quote. The host checks the claim's own approval and subtracts
   what earlier claims already got for the same order (T13, T14). Do not
   subtract it yourself or the host subtracts twice.
   Auth failure or order already refunded -> `denied` (the host writes `policy_denied`).
   `proposal_amount` stays the full quote. Amount conflict -> `failed`.
7. Success -> `resolved`. One ledger row, one `refund_committed` audit.
8. Use MockLLM only to draft the reply text, if you want a model call at all.

The model must not choose the refund amount and must not commit the refund.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import compute_compensation, get_claim_view, lookup_order, tool_cost
from lab8.trace import Trace

SHAPE = "workflow"


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


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal_amount: int, error_reason: str | None = None) -> RunResult:
        # Bước 8: model chỉ soạn câu trả lời từ status và số tiền đã chốt.
        # Prompt không chứa ticket, model không quyết số tiền và không ghi ledger.
        complete(budget, seed=seed, purpose="draft", prompt=f"status={status} proposal={proposal_amount}")
        trace.add("decision", {"status": status, "proposal_amount": proposal_amount, "reason": error_reason})
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

    # Bước 1: đọc claim qua allowlist (không SELECT *, không đụng parent_secret / raw headers).
    budget.add_tool(tool_cost())
    claim = get_claim_view(conn, claim_id)
    if not claim["found"]:
        trace.add("claim", {"found": False})
        return done("failed", 0, "unknown_claim")
    order_id = claim["order_id"]
    # Ticket là dữ liệu người dùng: không ghi vào trace, không dùng để quyết định.
    trace.add("claim", {"found": True, "order_id": order_id})

    # Bước 2: thiếu order_id -> needs_info. Không lấy mã đơn từ câu chữ trong ticket.
    if not order_id:
        return done("needs_info", 0, "missing_order_id")

    # Bước 3: lookup_order chỉ trả field allowlist (không phone, address, item, internal note).
    budget.add_tool(tool_cost())
    order_view = lookup_order(conn, order_id, claim["principal_id"])
    if not order_view["visible"]:
        trace.add("lookup_order", {"visible": False, "reason": order_view["reason"]})
        return done("escalated", 0, order_view["reason"])
    trace.add("lookup_order", order_view)

    # Bước 4: quote từ dòng order + policy hiện hành. Bỏ qua số ngày trễ ghi trong ticket.
    budget.add_tool(tool_cost())
    quote = compute_compensation(conn, order_view["order"])
    trace.add("quote", quote)
    proposal = int(quote["amount_vnd"])

    # Bước 5: quote = 0 -> denied, không gọi issue_refund.
    if proposal <= 0:
        return done("denied", 0, quote["reason"])

    # Bước 6: truyền QUOTE ĐẦY ĐỦ. Host tự kiểm approval, trần theo đơn, idempotency
    # và tự trừ phần các claim trước đã nhận (T13, T14). Không tự trừ ở đây.
    # CP3: crash_at đi thẳng vào host. InjectedCrash phải lan ra ngoài để mô phỏng
    # crash thật; chỉ ghi trace trước khi raise lại, không nuốt exception.
    # Lần chạy lại sau crash an toàn nhờ idempotency key refund:{claim_id} trong ledger.
    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise
    trace.add(
        "issue_refund",
        {"ok": outcome["ok"], "reason": outcome["reason"], "conflict": outcome["conflict"]},
    )
    if not outcome["ok"]:
        # Conflict số tiền -> failed. Host từ chối (đã ghi policy_denied) -> denied.
        return done("failed" if outcome["conflict"] else "denied", proposal, outcome["reason"])

    # Bước 7: một dòng ledger, một audit refund_committed.
    return done("resolved", proposal)
