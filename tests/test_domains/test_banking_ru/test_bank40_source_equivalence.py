"""Minimal full-grader reproducers for the two confirmed Bank40 false zeros."""

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize(
    "route", ["special_sources", "early_state", "missing_special_source"]
)
def test_bank40_equivalent_sources_and_early_state(route):
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    session = PlatformSession(task, get_environment, max_calls=120)
    for action in task.evaluation_criteria.actions:
        if (
            action.name == "file_read"
            and action.arguments["path"] == "/policies/fees_rewards.md"
        ):
            continue
        if (
            route == "missing_special_source"
            and action.name == "file_read"
            and action.arguments["path"] == "/knowledge/kb_fee_refund_reconciliation.md"
        ):
            continue
        if route == "early_state" and action.action_id == "bank40:consent":
            session.call("get_fee_refund_state", {"transaction_id": "txn_088219"})
        session.call(action.name, action.arguments)
    session.call("done", {})
    expected = 0 if route == "missing_special_source" else 1
    assert session.grade().reward == expected
    assert grade_full_session(session).reward_info.reward == expected
