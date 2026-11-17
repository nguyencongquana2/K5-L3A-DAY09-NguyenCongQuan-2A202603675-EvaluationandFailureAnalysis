"""Shape C — orchestrator plus read-only investigators. Student work."""

from __future__ import annotations

import sqlite3
from lab8.schemas import RunResult, make_run_id, Budget
from lab8.shapes.common import finish
from lab8.trace import Trace
from lab8.handoff import make_handoff
from lab8.runtime import run_readonly_investigators
from lab8 import durability
from lab8 import tools

def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    run_id = make_run_id("orchestrator", claim_id, seed)
    trace = Trace(run_id)
    budget = Budget(model_calls=0, tool_calls=0, input_tokens=0, output_tokens=0, cost_estimate=0.0)

    conn = sqlite3.connect(store_path)
    conn.row_factory = sqlite3.Row
    
    try:
        raw_claim = tools.get_claim_view(conn, claim_id)
        # Nếu claim_id không tồn tại trong hệ thống -> status = failed
        if not raw_claim or not raw_claim.get("found"):
            return finish(
                conn, shape="orchestrator", claim_id=claim_id, seed=seed,
                status="failed", proposal_amount=0, budget=budget, trace=trace
            )
            
        principal_id = raw_claim.get("principal_id")
        allowed_tools = ["lookup_order", "lookup_policy", "lookup_refund_history", "get_clause"]
        
        safe_handoff = make_handoff(
            request_id=run_id,
            claim_id=claim_id,
            task="Investigate order and policy facts to determine refund eligibility.",
            facts=dict(raw_claim),
            allowed_tools=allowed_tools
        )
        
        trace.add("handoff_created", safe_handoff)

        investigation_report = run_readonly_investigators(
            conn=conn, 
            budget=budget, 
            handoff=safe_handoff, 
            principal_id=principal_id
        )
        trace.add("investigation_done", {"report": investigation_report})

        # Xử lý các luồng Needs Info & Escalated
        lookup_res = investigation_report.get("tools", {}).get("lookup_order", {})
        if not lookup_res.get("visible"):
            reason = lookup_res.get("reason")
            if reason == "missing_order_id":
                status = "needs_info"
            else:
                status = "escalated"
            return finish(
                conn, shape="orchestrator", claim_id=claim_id, seed=seed,
                status=status, proposal_amount=0, budget=budget, trace=trace
            )

        order = lookup_res.get("order", {})

        # Tính tiền bồi thường
        quote_result = tools.compute_compensation(conn, order)
        quote = quote_result.get("amount_vnd", 0)

        # Xét kết quả hoàn tiền (Resolved, Denied, Failed)
        if quote > 0:
            refund_res = durability.issue_refund(
                conn=conn, 
                claim_id=claim_id, 
                proposed_vnd=quote, 
                crash_at=crash_at
            )
            # Dựa vào câu trả lời của Host để quyết định status cuối cùng
            if refund_res.get("ok"):
                status = "resolved"
            elif refund_res.get("conflict"):
                status = "failed"
            else:
                status = "denied"
        else:
            status = "denied"
        
        return finish(
            conn, shape="orchestrator", claim_id=claim_id, seed=seed,
            status=status, proposal_amount=quote, budget=budget, trace=trace
        )
    finally:
        # Đóng kết nối nhưng thả cho InjectedCrash bay ra ngoài tự nhiên
        conn.close()