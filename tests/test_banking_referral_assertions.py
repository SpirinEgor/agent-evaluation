"""Bank12 business-state alternatives through the active whole evaluator."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

OWNER = "kuznecova_o_3391"
TRANSACTION = "txn_540117"


def _session(*, category="merchant_investigation", linked=True):
    task = next(task for task in get_tasks() if task.id == "Bank 12")
    session = PlatformSession(task, get_environment)
    actions = deepcopy(task.evaluation_criteria.actions)
    for action in actions:
        if action.name == "create_case":
            action.arguments["category"] = category
            if not linked:
                action.arguments.pop("transaction_id")
        assert not session.call(action.name, action.arguments).is_error
    return session


def _handoff(session):
    assert not session.call(
        "escalate_to_human",
        {
            "customer_id": OWNER,
            "request": {"type": "card_claim_referral"},
            "case_id": next(
                c.id
                for c in session.env.tools.db.cases.values()
                if c.customer_id == OWNER and c.amount == 7450
            ),
            "transaction_id": TRANSACTION,
        },
    ).is_error


def _grade(session, expected):
    assert not session.call("done", {}).is_error
    # Exercise the evaluator's reconstruction as well as live-session grading.
    for reward in (session.grade(), grade_full_session(session).reward_info):
        assert reward.final_reward == expected, reward.model_dump(mode="json")


@pytest.mark.parametrize(
    "category,handoff",
    [
        ("merchant_investigation", False),
        ("other", False),
        ("escalation", False),
        ("merchant_investigation", True),
    ],
)
def test_bank12_accepts_registered_statement_alternatives(category, handoff):
    session = _session(category=category)
    if handoff:
        _handoff(session)
    _grade(session, 1.0)


@pytest.mark.parametrize("route", ["noop", "missing_purchase_link", "extra_case"])
def test_bank12_rejects_missing_or_extra_business_state(route):
    if route == "noop":
        task = next(task for task in get_tasks() if task.id == "Bank 12")
        session = PlatformSession(task, get_environment)
    else:
        session = _session(linked=route != "missing_purchase_link")
        if route == "extra_case":
            assert not session.call(
                "create_case",
                {
                    "customer_id": OWNER,
                    "category": "other",
                    "transaction_id": TRANSACTION,
                    "amount": 7450,
                },
            ).is_error
    _grade(session, 0.0)
