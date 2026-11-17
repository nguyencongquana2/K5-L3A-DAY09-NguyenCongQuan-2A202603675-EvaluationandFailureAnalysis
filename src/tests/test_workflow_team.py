"""Test riêng của shape A workflow (nhóm ABCDE, người viết: Vũ Quang Minh).

Chạy trong cổng host mặc định (`python -m pytest -q`). T12 trong ma trận student chỉ
kiểm một điểm crash; file này kiểm cả ba điểm `crash_at` mà host hỗ trợ, cộng hai
ranh giới mà workflow phải giữ: không tin số ngày trễ trong ticket, và trace ghi lúc
crash không lộ secret hay câu chữ ticket.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.db import connect
from lab8.durability import InjectedCrash, committed_audit_count, get_refund
from lab8.shapes.workflow import run_claim
from tests.conftest import BY_ID

CRASH_POINTS = ["before_effect", "after_effect_before_checkpoint", "after_checkpoint"]


@pytest.mark.parametrize("crash_at", CRASH_POINTS)
def test_workflow_crash_then_resume_pays_once(public_db, crash_at):
    with pytest.raises(InjectedCrash):
        run_claim("C-001", seed=11, store_path=str(public_db), crash_at=crash_at)

    result = run_claim("C-001", seed=11, store_path=str(public_db))

    conn = connect(public_db)
    try:
        effect = get_refund(conn, "C-001")
        rows = conn.execute("SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = 'C-001'").fetchone()["n"]
        audits = committed_audit_count(conn, "C-001")
    finally:
        conn.close()
    assert result.status == "resolved"
    assert result.proposal_amount == 35000
    assert rows == 1
    assert int(effect["amount_vnd"]) == 35000
    assert audits == 1
    assert result.committed_audit_count == 1


def test_workflow_crash_writes_clean_trace_before_raising(public_db):
    with pytest.raises(InjectedCrash):
        run_claim("C-001", seed=11, store_path=str(public_db), crash_at="after_effect_before_checkpoint")

    trace_path = Path("traces") / "R-workflow-C-001-s11.json"
    body = json.loads(trace_path.read_text(encoding="utf-8"))
    raw = trace_path.read_text(encoding="utf-8")
    assert body["run_id"] == "R-workflow-C-001-s11"
    assert {"kind": "crash", "payload": {"at": "after_effect_before_checkpoint"}} in body["events"]
    assert "CANARY_SECRET" not in raw
    assert BY_ID["C-001"]["ticket"] not in raw


def test_workflow_ignores_delay_written_in_ticket(public_db):
    # C-002: ticket ghi "trễ 5 ngày" nhưng dòng order ghi 2 ngày -> không đủ điều kiện.
    # Swarm tin ticket ở seed 55; workflow phải từ chối ở mọi seed.
    for seed in (11, 55):
        result = run_claim("C-002", seed=seed, store_path=str(public_db))
        assert result.status == "denied"
        assert result.proposal_amount == 0
        assert result.refund_effects == []
