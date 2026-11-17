"""CP2 Integration Tests: Verify host policy enforcement across all three student shapes.

Tests written by Tran Viet Thinh (2A202603661) for ABCDE team.
Verifies that all three shapes (workflow, single, orchestrator) handle:
1. Expired approval: Host rejects and shape terminates with status='denied'.
2. Borrowed approval: Host rejects approval belonging to another claim and shape returns 'denied'.
3. Order already fully refunded: Host rejects further payout and shape returns 'denied'.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from lab8.db import connect
from lab8.durability import get_refund
from lab8.shapes import SHAPES

STUDENT_SHAPES = ["workflow", "single", "orchestrator"]


def _init_test_case(public_db: Path, claim_id: str, *, approval_id: str | None, order_id: str = "O-107"):
    conn = connect(public_db)
    conn.execute(
        """
        INSERT INTO claims (claim_id, principal_id, order_id, ticket, parent_secret, raw_headers_json, approval_id)
        VALUES (?, 'P-100', ?, 'integration test ticket', 'CANARY_SECRET_INTEG', '{}', ?)
        """,
        (claim_id, order_id, approval_id),
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
def test_expired_approval_denied_across_shapes(public_db, shape):
    """When approval is expired, all shapes must return status='denied' with no ledger effect."""
    conn = connect(public_db)
    conn.execute(
        "INSERT INTO approvals (approval_id, claim_id, approved_vnd, status, expires_at) "
        "VALUES ('A-EXP-CP2', 'C-EXP-CP2', 35000, 'valid', '2020-01-01')"
    )
    conn.commit()
    conn.close()
    _init_test_case(public_db, "C-EXP-CP2", approval_id="A-EXP-CP2")

    result = SHAPES[shape]("C-EXP-CP2", seed=11, store_path=str(public_db))
    assert result.status == "denied"

    conn = connect(public_db)
    effect = get_refund(conn, "C-EXP-CP2")
    denied_audits = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_events WHERE claim_id = 'C-EXP-CP2' AND event_type = 'policy_denied'"
    ).fetchone()["n"]
    conn.close()

    assert effect is None
    assert denied_audits >= 1


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
def test_borrowed_approval_denied_across_shapes(public_db, shape):
    """When claim points to approval of a different claim, all shapes must be denied."""
    # A-001 belongs to C-001 in public dataset
    _init_test_case(public_db, "C-BORROW-CP2", approval_id="A-001")

    result = SHAPES[shape]("C-BORROW-CP2", seed=11, store_path=str(public_db))
    assert result.status == "denied"

    conn = connect(public_db)
    effect = get_refund(conn, "C-BORROW-CP2")
    denied_audits = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_events WHERE claim_id = 'C-BORROW-CP2' AND event_type = 'policy_denied'"
    ).fetchone()["n"]
    conn.close()

    assert effect is None
    assert denied_audits >= 1


@pytest.mark.parametrize("shape", STUDENT_SHAPES)
def test_already_refunded_order_denied_across_shapes(public_db, shape):
    """When order was already refunded to capacity, all shapes must be denied."""
    # Seed an approval for C-ALREADY-CP2
    conn = connect(public_db)
    conn.execute(
        "INSERT INTO approvals (approval_id, claim_id, approved_vnd, status, expires_at) "
        "VALUES ('A-ALREADY-CP2', 'C-ALREADY-CP2', 35000, 'valid', '2027-12-31')"
    )
    # Simulate prior committed refund on O-107 for 35,000 VND (full shipping fee)
    conn.execute(
        "INSERT INTO refund_ledger (claim_id, order_id, idempotency_key, amount_vnd, status) "
        "VALUES ('C-PRIOR', 'O-107', 'refund:C-PRIOR', 35000, 'committed')"
    )
    conn.commit()
    conn.close()

    _init_test_case(public_db, "C-ALREADY-CP2", approval_id="A-ALREADY-CP2", order_id="O-107")

    result = SHAPES[shape]("C-ALREADY-CP2", seed=11, store_path=str(public_db))
    assert result.status == "denied"

    conn = connect(public_db)
    effect = get_refund(conn, "C-ALREADY-CP2")
    denied_audits = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_events WHERE claim_id = 'C-ALREADY-CP2' AND event_type = 'policy_denied'"
    ).fetchone()["n"]
    conn.close()

    assert effect is None
    assert denied_audits >= 1
