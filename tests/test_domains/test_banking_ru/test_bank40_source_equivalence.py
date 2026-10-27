"""Lawful source and early-state alternatives retain the current business grade."""

import json
from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("route", ["special_sources", "early_state"])
def test_bank40_equivalent_sources_and_early_state(route):
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    session = PlatformSession(task, get_environment, max_calls=120)
    for action in task.evaluation_criteria.actions:
        if (
            action.name == "file_read"
            and action.arguments["path"] == "/policies/fees_rewards.md"
        ):
            continue
        if route == "early_state" and action.action_id == "bank40:consent":
            session.call("get_fee_refund_state", {"transaction_id": "txn_088219"})
        session.call(action.name, action.arguments)
    session.call("done", {})
    assert session.grade().reward == 1
    assert grade_full_session(session).reward_info.reward == 1


@pytest.mark.parametrize("route", ["reordered_consent", "initial_versioned_subset"])
def test_fee_consent_is_a_set_and_initial_versions_do_not_suppress_settlement(route):
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    s = PlatformSession(task, get_environment, max_calls=150, max_errors=20)
    for a in task.evaluation_criteria.actions:
        args = deepcopy(a.arguments)
        if a.action_id == "bank40:consent":
            args["request"]["refunds"] = list(reversed(args["request"]["refunds"]))
            if route == "initial_versioned_subset":
                args["request"]["refunds"] = [
                    {"transaction_id": "txn_088220", "amount": 129},
                    {"transaction_id": "txn_088219", "amount": 169},
                ]
                args["request"]["total_amount"] = 298
                versions = {}
                for tx in ["txn_088219", "txn_088220"]:
                    result = s.call("get_fee_refund_state", {"transaction_id": tx})
                    assert not result.is_error
                    versions[tx] = json.loads(result.content[0].text)["state_version"]
                args["request"]["state_versions"] = versions
        result = s.call(a.name, args)
        assert not result.is_error, result.content[0].text
    s.call("done", {})
    assert s.env.tools.db.accounts["acc_7734"].balance == 4080
    assert s.grade().reward == 1
    assert grade_full_session(s).reward_info.reward == 1
