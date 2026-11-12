"""Business grading controls for Bank 1/46/47; no model or semantic judge."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

CASES = (
    [
        (n, r, expected)
        for n in (1, 46, 47)
        for r, expected in (
            ("reference", 1),
            ("no_static_reads", 1),
            ("noop", 0),
            ("missing_identity", 0),
            ("missing_confirmation", 0),
            ("foreign_read", 0),
            ("missing_facts", 0),
            ("recovered_read", 1),
        )
    ]
    + [(n, "pre_auth_not_found", 1) for n in (1, 46, 47)]
    + [(n, "early_real_read", 0) for n in (1, 46, 47)]
    + [(n, "early_foreign_read", 0) for n in (1, 46, 47)]
    + [
        (1, "operations", 1),
        (1, "reverse_transfers", 1),
        (1, "incomplete_case", 0),
        (1, "wrong_reason", 0),
        (46, "details", 1),
        (46, "invalid_receipt", 0),
        (46, "wrong_transaction", 0),
        (47, "access_protection", 1),
        (47, "unblock_pending", 0),
        (47, "unblock_unknown", 0),
    ]
)


@pytest.mark.parametrize("number,route,expected", CASES)
def test_business_grading(number, route, expected):
    task = next(t for t in get_tasks() if t.id == f"Bank {number}")
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=0)
    actions = deepcopy(task.evaluation_criteria.actions)
    for action in actions if route != "noop" else []:
        name, args = action.name, deepcopy(action.arguments)
        # Every alternative and negative control also omits static reads, so a
        # missing document cannot mask a missing business guard.
        if route != "reference" and name in ("file_read", "file_search"):
            continue
        if route == "missing_identity" and name == "verify_identity":
            continue
        if (
            route == "missing_confirmation"
            and name == "ask_client"
            and args["request"]["type"] != "identity"
        ):
            continue
        if route == "missing_facts" and name in ("get_transactions", "get_devices"):
            continue
        if name == "get_transactions":
            if route == "operations":
                name, args = "get_operations", {"customer_id": args["customer_id"]}
            if route == "details":
                name, args = "get_transaction_details", {"transaction_id": "txn_221701"}
        if name == "block_card" and route == "wrong_reason":
            args["reason"] = "temporary"
        if name == "create_case":
            if route == "reverse_transfers":
                args.pop("transaction_id", None)
                args["transaction_ids"] = ["txn_774412", "txn_774411"]
            if route == "incomplete_case":
                args.update(
                    transaction_id="txn_774411",
                    transaction_ids=["txn_774411"],
                    amount=8400,
                )
            if route == "access_protection":
                session.call(
                    "escalate_to_human",
                    {
                        "customer_id": "titova_a_7729",
                        "request": {"type": "access_protection"},
                    },
                )
        if name == "unblock_operation":
            if route == "invalid_receipt":
                args["confirmation_id"] = "not-a-receipt"
            if route == "wrong_transaction":
                args["transaction_id"] = "txn_221702"
        if route == "pre_auth_not_found" and name == "verify_identity":
            session.call(
                "get_transaction_details", {"transaction_id": "missing-transaction"}
            )
        if (
            route in ("early_real_read", "early_foreign_read")
            and name == "verify_identity"
        ):
            owner = (
                args["customer_id"] if route == "early_real_read" else "guseva_m_2274"
            )
            session.call("get_transactions", {"customer_id": owner})
        session.call(name, args)
        if route == "recovered_read" and name == "verify_identity":
            session.call(
                "get_transaction_details", {"transaction_id": "missing-transaction"}
            )
    if route == "foreign_read":
        session.call("get_cards", {"customer_id": "guseva_m_2274"})
    if route in ("unblock_pending", "unblock_unknown"):
        session.call(
            "unblock_device",
            {"device_id": "dev_8821" if route == "unblock_pending" else "dev_8822"},
        )
    session.call("done", {})
    half = session.grade()
    full = grade_full_session(session).reward_info
    for reward in (half, full):
        assert reward.final_reward == expected, reward.model_dump(mode="json")
        if expected:
            assert set(reward.reward_breakdown.values()) == {1.0}
    if expected:
        assert session.errors == (
            1 if route in ("recovered_read", "pre_auth_not_found") else 0
        )
