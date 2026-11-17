"""CP3 tests for Shape B (team ABCDE, author: Nguyen Cong Quan).

These tests run in the default host gate. They verify that the single-agent
shape forwards every supported crash point and that retries remain idempotent.
"""

from __future__ import annotations

import pytest

from lab8.db import connect
from lab8.durability import InjectedCrash, committed_audit_count, get_refund
from lab8.shapes.single import run_claim

CRASH_POINTS = ["before_effect", "after_effect_before_checkpoint", "after_checkpoint"]


def _assert_one_refund(store_path, claim_id: str, expected_amount: int) -> None:
    conn = connect(store_path)
    try:
        effect = get_refund(conn, claim_id)
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM refund_ledger WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()["n"]
        audits = committed_audit_count(conn, claim_id)
    finally:
        conn.close()

    assert effect is not None
    assert effect["idempotency_key"] == f"refund:{claim_id}"
    assert int(effect["amount_vnd"]) == expected_amount
    assert rows == 1
    assert audits == 1


def test_single_retry_keeps_one_refund(public_db):
    first = run_claim("C-011", seed=11, store_path=str(public_db))
    second = run_claim("C-011", seed=11, store_path=str(public_db))

    assert first.status == "resolved"
    assert second.status == "resolved"
    assert first.proposal_amount == 35000
    assert second.proposal_amount == 35000
    assert second.committed_audit_count == 1
    _assert_one_refund(public_db, "C-011", 35000)


@pytest.mark.parametrize("crash_at", CRASH_POINTS)
def test_single_crash_then_resume_keeps_one_refund(public_db, crash_at):
    with pytest.raises(InjectedCrash):
        run_claim("C-012", seed=11, store_path=str(public_db), crash_at=crash_at)

    resumed = run_claim("C-012", seed=11, store_path=str(public_db))

    assert resumed.status == "resolved"
    assert resumed.proposal_amount == 35000
    assert resumed.committed_audit_count == 1
    _assert_one_refund(public_db, "C-012", 35000)
