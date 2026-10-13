"""Bank 2 completion-only alternatives; mode adapters are checked synthetically."""

from copy import deepcopy

import pytest

from tau2.data_model.tasks import Action
from tests.test_domains.test_banking_ru.helpers import execute, task


@pytest.mark.parametrize(
    "variant,expected",
    [("alternative", 1), ("manual_grant", 0)],
    ids=["alternative", "manual_grant"],
)
def test_bank2_completion_only_whole_grade(variant, expected):
    actions = [
        deepcopy(a)
        for a in task(2).evaluation_criteria.actions
        if a.name != "reply_to_ticket"
    ]
    if variant == "alternative":
        actions = [
            a
            for a in actions
            if a.name not in {"get_transaction_details", "calculate", "file_search"}
        ]
    elif variant == "noop":
        actions = []
    elif variant == "missing_identity":
        actions = [a for a in actions if a.name != "verify_identity"]
    elif variant == "missing_purchase":
        actions = [
            a
            for a in actions
            if a.name not in {"get_transaction_details", "get_transactions"}
        ]
    elif variant == "missing_rules":
        actions = [a for a in actions if a.name != "get_cashback_rules"]
    elif variant == "missing_article":
        actions = [a for a in actions if a.action_id != "read_cashback_policy_Bank 2"]
    elif variant == "manual_grant":
        actions.append(
            Action(
                action_id="bad",
                name="grant_cashback",
                arguments={"transaction_id": "txn_552290", "amount": 60.0},
            )
        )
    elif variant == "final_reply":
        actions.append(
            Action(
                action_id="bad",
                name="reply_to_ticket",
                arguments={"customer_id": "gromov_a_1187", "text": "Готово"},
            )
        )
    elif variant == "ask_final_reply":
        actions.append(
            Action(
                action_id="bad",
                name="ask_client",
                arguments={
                    "customer_id": "gromov_a_1187",
                    "request": {"type": "clarification"},
                    "question": "Ваш кешбэк составит 60 рублей. Что-нибудь ещё?",
                },
            )
        )
    session = execute(2, actions)
    assert session.grade().final_reward == expected
