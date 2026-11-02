"""Bank 2 completion and forbidden successful manual payout controls."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.helpers import execute, task


def test_bank2_completion_only_alternative():
    actions = [
        deepcopy(a)
        for a in task(2).evaluation_criteria.actions
        if a.name
        not in {
            "reply_to_ticket",
            "get_transaction_details",
            "calculate",
            "file_search",
        }
    ]
    assert execute(2, actions).grade().final_reward == 1


def test_bank2_successful_manual_grant_is_rejected_by_action():
    session = PlatformSession(task(2), get_environment)
    # Reach authenticated current data before review registration freezes claims.
    for action in session.task.evaluation_criteria.actions:
        result = session.call(action.name, action.arguments)
        assert not result.is_error, (action.name, result.content)
        if action.name == "verify_identity":
            break
    transaction = session.env.tools.db.transactions["txn_804219"]
    account = session.env.tools.db.accounts[transaction.account_id]
    balance = account.balance
    reply = session.call(
        "grant_cashback", {"transaction_id": transaction.id, "amount": 60.0}
    )
    assert not reply.is_error, reply.content
    assert account.balance == pytest.approx(balance + 60)
    assert transaction.cashback_granted
    assert not session.call("done", {}).is_error
    grade = session.grade()
    assert grade.reward_breakdown["ACTION"] == 0
    assert "forbid_manual_cashback_Bank 2" in grade.info["action"]["forbidden_actions"]
