"""Tests written by Tran Viet Thinh (2A202603661) for Ledger, Policy, and Durability guarantees."""

import pytest
from lab8.db import connect
from lab8.durability import committed_audit_count, get_refund, issue_refund
from lab8.policy import authorize, order_refunded_vnd


def _denials(conn, claim_id):
    rows = conn.execute(
        "SELECT details_json FROM audit_events WHERE claim_id = ? AND event_type = 'policy_denied'",
        (claim_id,),
    ).fetchall()
    return [row["details_json"] for row in rows]


def test_expired_approval_is_rejected_by_host(public_db):
    """Host must reject approval whose expires_at is before the current active date."""
    conn = connect(public_db)
    conn.execute(
        "INSERT INTO approvals (approval_id, claim_id, approved_vnd, status, expires_at) "
        "VALUES ('A-EXPIRED', 'C-EXP', 35000, 'valid', '2020-01-01')"
    )
    conn.execute(
        """
        INSERT INTO claims (claim_id, principal_id, order_id, ticket, parent_secret, raw_headers_json, approval_id)
        VALUES ('C-EXP', 'P-100', 'O-107', 'expired approval ticket', 'CANARY_SECRET_EXP', '{}', 'A-EXPIRED')
        """
    )
    conn.commit()

    outcome = issue_refund(conn, "C-EXP", 35000)
    effect = get_refund(conn, "C-EXP")
    denials = _denials(conn, "C-EXP")
    conn.close()

    assert outcome["ok"] is False
    assert outcome["reason"] == "approval_expired"
    assert effect is None
    assert any("approval_expired" in row for row in denials)


def test_missing_approval_record_is_rejected_by_host(public_db):
    """Host must reject claim pointing to a non-existent approval record."""
    conn = connect(public_db)
    conn.execute(
        """
        INSERT INTO claims (claim_id, principal_id, order_id, ticket, parent_secret, raw_headers_json, approval_id)
        VALUES ('C-NOAPP', 'P-100', 'O-107', 'missing approval ticket', 'CANARY_SECRET_NOAPP', '{}', 'A-DOESNOTEXIST')
        """
    )
    conn.commit()

    outcome = issue_refund(conn, "C-NOAPP", 35000)
    effect = get_refund(conn, "C-NOAPP")
    denials = _denials(conn, "C-NOAPP")
    conn.close()

    assert outcome["ok"] is False
    assert outcome["reason"] == "missing_approval"
    assert effect is None
    assert any("missing_approval" in row for row in denials)


def test_non_positive_amount_is_rejected_by_host(public_db):
    """Host must reject proposed amounts or approved amounts that are <= 0."""
    conn = connect(public_db)
    decision_zero_proposal = authorize(
        conn,
        "C-001",
        proposed_vnd=0,
        paid_vnd=50000,
        already_refunded_vnd=0,
    )
    assert decision_zero_proposal.ok is False
    assert decision_zero_proposal.reason == "non_positive_amount"

    # Also check negative proposal
    decision_neg_proposal = authorize(
        conn,
        "C-001",
        proposed_vnd=-10000,
        paid_vnd=50000,
        already_refunded_vnd=0,
    )
    assert decision_neg_proposal.ok is False
    assert decision_neg_proposal.reason == "non_positive_amount"
    conn.close()


def test_cap_at_paid_amount_minus_already_refunded(public_db):
    """Refund must be capped at paid_vnd - already_refunded_vnd even if approved is larger."""
    conn = connect(public_db)
    decision = authorize(
        conn,
        "C-001",
        proposed_vnd=100000,
        paid_vnd=50000,
        already_refunded_vnd=20000,
    )
    conn.close()
    assert decision.ok is True
    # remaining payable from paid_vnd is 50000 - 20000 = 30000.
    # proposed minus already = 100000 - 20000 = 80000.
    # approved for C-001 is 35000.
    # capped at min(80000, 35000, 30000) -> 30000.
    assert decision.amount_vnd == 30000


def test_idempotent_replay_does_not_duplicate_audits_or_ledger(public_db):
    """Replaying an already committed refund must succeed idempotently without adding audit rows."""
    conn = connect(public_db)
    first = issue_refund(conn, "C-001", 35000)
    audits_first = committed_audit_count(conn, "C-001")

    second = issue_refund(conn, "C-001", 35000)
    audits_second = committed_audit_count(conn, "C-001")

    third = issue_refund(conn, "C-001", 35000)
    audits_third = committed_audit_count(conn, "C-001")
    conn.close()

    assert first["ok"] is True and first["reason"] == "committed"
    assert second["ok"] is True and second["reason"] == "idempotent_replay"
    assert third["ok"] is True and third["reason"] == "idempotent_replay"
    assert audits_first == 1
    assert audits_second == 1
    assert audits_third == 1
