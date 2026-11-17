"""Regression case derived from the first wrong step in C-002@55."""

import pytest

from lab9.trace_case import investigate


pytestmark = pytest.mark.student


def test_ticket_override_is_first_wrong_step_and_fixed_removes_it():
    broken = investigate("swarm", "C-002", 55)["trial"]
    fixed = investigate("fixed", "C-002", 55)["trial"]

    broken_kinds = [event.get("kind") for event in broken["events"]]
    fixed_kinds = [event.get("kind") for event in fixed["events"]]

    assert broken_kinds[:3] == ["handoff", "investigator", "model_override"]
    assert broken["events"][2]["payload"] == {"delay_days": 5}
    assert broken["lab8_pass"] is False
    assert broken["snapshot"]["refund_vnd"] == 20_000

    assert "model_override" not in fixed_kinds
    assert fixed["lab8_pass"] is True
    assert fixed["snapshot"]["refund_vnd"] == 0
