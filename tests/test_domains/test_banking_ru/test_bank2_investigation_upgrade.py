"""Addressed full DB × ENV × ACTION checks for the upgraded Bank2."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session, receipt_observer

CUSTOMER = "gromov_a_1187"
TARGET = "txn_552290"
ARGS = {
    "customer_id": CUSTOMER,
    "program": "standard_cashback",
    "amount": 30.0,
    "items": [{"transaction_id": TARGET, "amount": 30.0}],
}


def task2():
    return next(t for t in get_tasks() if t.id == "Bank 2")


@pytest.mark.parametrize(
    "route,expected",
    [
        ("reference", 1),
        ("noop", 0),
        ("details", 1),
        ("shuffled_readings", 1),
        ("current_rate", 0),
        ("subtract_processing", 0),
        ("wrong_purchase", 0),
        ("ignore_cap", 0),
        ("ignore_paid", 0),
        ("double_subtract_paid", 0),
        ("before_selection", 0),
        ("wrong_attempt_repaired", 0),
        ("grant_money", 0),
        ("duplicate", 0),
        ("missing_return_receipt", 0),
        ("missing_paid_receipt", 0),
    ],
)
def test_routes(route, expected):
    task = task2()
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=100)
    actions = list(task.evaluation_criteria.actions)
    if route == "shuffled_readings":
        indices = [
            i
            for i, a in enumerate(actions)
            if a.name
            in {"file_read", "get_transactions", "get_cashback_rules", "get_bank_date"}
            and a.action_id
            not in {"read_process_identity_Bank 2", "read_bank_policy_Bank 2"}
        ]
        # Move independent reads after identification, preserving only protected dependencies.
        extracted = [actions[i] for i in indices]
        remaining = [a for i, a in enumerate(actions) if i not in indices]
        at = next(
            i
            for i, a in enumerate(remaining)
            if a.name == "create_cashback_investigation"
        )
        actions = remaining[:at] + list(reversed(extracted)) + remaining[at:]
    if route != "noop":
        for action in actions:
            if (
                action.name == "ask_client"
                and action.arguments.get("request", {}).get("type")
                == "transaction_selection"
                and route == "before_selection"
            ):
                session.call("create_cashback_investigation", deepcopy(ARGS))
            if action.name == "get_transactions" and route in {
                "details",
                "missing_return_receipt",
                "missing_paid_receipt",
            }:
                for ident in [TARGET, "txn_552292", "txn_552293", "txn_552296"]:
                    if (
                        route == "missing_return_receipt" and ident == "txn_552292"
                    ) or (route == "missing_paid_receipt" and ident == "txn_552296"):
                        continue
                    session.call("get_transaction_details", {"transaction_id": ident})
                continue
            if action.name != "create_cashback_investigation":
                session.call(action.name, dict(action.arguments))
                continue
            args = deepcopy(ARGS)
            amounts = {
                "current_rate": 5.0,
                "subtract_processing": 15.0,
                "ignore_cap": 45.0,
                "ignore_paid": 35.0,
                "double_subtract_paid": 25.0,
            }
            if route in amounts:
                args["amount"] = amounts[route]
                args["items"][0]["amount"] = amounts[route]
            if route == "wrong_purchase":
                args["items"][0]["transaction_id"] = "txn_552291"
            if route == "wrong_attempt_repaired":
                bad = deepcopy(ARGS)
                bad["amount"] = 35.0
                bad["items"][0]["amount"] = 35.0
                session.call(action.name, bad)
            if route == "grant_money":
                session.call(
                    "grant_cashback", {"transaction_id": TARGET, "amount": 30.0}
                )
            session.call(action.name, args)
            if route == "duplicate":
                session.call(action.name, args)
    session.call("done", {})
    result = grade_full_session(session).reward_info
    assert result.reward == expected, result.model_dump(mode="json")


@pytest.mark.parametrize("submitted,expected", [(15.0, 1), (30.0, 0)])
def test_processing_becoming_posted_changes_due_amount(submitted, expected):
    """Change one premise in a temporary task/DB, never the shared dataset."""
    from tau2.data_model.tasks import InitializationData, InitialState

    task = task2().model_copy(deep=True)
    task.initial_state = InitialState(
        initialization_data=InitializationData(
            agent_data={"transactions": {"txn_552293": {"status": "posted"}}}
        )
    )
    e = task.evaluation_criteria
    for action in e.actions:
        if action.name == "create_cashback_investigation":
            action.arguments["amount"] = 15.0
            action.arguments["items"][0]["amount"] = 15.0
    for rule in e.env_assertions:
        if rule.func_name in {
            "assert_cashback_investigation",
            "assert_cashback_investigation_attempts",
        }:
            rule.arguments["amount"] = 15.0
            rule.arguments["items"][0]["amount"] = 15.0
    for evidence in e.result_evidence:
        if evidence.record_id == "txn_552293":
            evidence.expected_fields["status"] = "posted"
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=100)
    for action in e.actions:
        args = deepcopy(action.arguments)
        if action.name == "create_cashback_investigation":
            args["amount"] = submitted
            args["items"][0]["amount"] = submitted
        session.call(action.name, args)
    session.call("done", {})
    result = grade_full_session(session).reward_info
    assert result.reward == expected, result.model_dump(mode="json")


@pytest.fixture(autouse=True)
def save_grade_receipt(request):
    """Optional compact evidence sink, without raw identity secrets."""
    destination = os.environ.get("BANK2_EVIDENCE_DIR")
    if not destination:
        yield
        return
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)

    def record(session, graded):
        calls = []
        for call, result in session.log:
            args = deepcopy(call.arguments)
            if call.name == "verify_identity":
                args["credential"] = "[MASKED identity credential]"
            calls.append(
                {
                    "name": call.name,
                    "arguments": args,
                    "result_received": True,
                    "error": result.error,
                }
            )
        filename = request.node.name.replace("[", "_").replace("]", "") + ".json"
        (folder / filename).write_text(
            json.dumps(
                {
                    "case": request.node.name,
                    "task_id": session.task.id,
                    "reward": graded.reward_info.model_dump(mode="json"),
                    "invocation": graded.invocation,
                    "calls": calls,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    token = receipt_observer.set(record)
    try:
        yield
    finally:
        receipt_observer.reset(token)


def test_bank2_source_data_relations_and_observable_selection():
    """Reconcile source semantics against live ledger and fixed client receipt."""
    from decimal import Decimal

    from tau2.domains.banking_ru.utils import BANKING_RU_DB_PATH

    data_dir = Path(BANKING_RU_DB_PATH).parent
    db = json.loads(Path(BANKING_RU_DB_PATH).read_text())
    purchase = db["transactions"][TARGET]
    linked = [
        row
        for row in db["transactions"].values()
        if row.get("original_transaction_id") == TARGET
    ]
    refunds = [
        row for row in linked if row["kind"] == "refund" and row["status"] == "posted"
    ]
    payments = [
        row for row in linked if row["kind"] == "cashback" and row["status"] == "posted"
    ]
    assert refunds and payments
    for row in refunds + payments:
        assert all(
            row.get(field) == purchase[field]
            for field in ["customer_id", "account_id", "card_id"]
        )
    assert any(
        row["kind"] == "refund" and row["status"] == "processing" for row in linked
    )
    assert any(
        row["kind"] == "refund"
        and row.get("original_transaction_id") != TARGET
        and row["customer_id"] == CUSTOMER
        for row in db["transactions"].values()
    )
    rules = db["cashback_rules"][CUSTOMER]
    histories = [
        row
        for row in rules["categories_history"]
        if row["effective_from"] <= purchase["date"]
        and (row["effective_to"] is None or purchase["date"] <= row["effective_to"])
    ]
    assert len(histories) == 1
    rate = next(
        category["rate"]
        for category in histories[0]["categories"]
        if purchase["mcc"] in category["mcc_codes"]
    )
    current_rate = next(
        category["rate"]
        for category in rules["categories"]
        if purchase["mcc"] in category["mcc_codes"]
    )
    assert rate != current_rate

    def number(value):
        return Decimal(str(value))

    net = number(purchase["amount"]) - sum(number(row["amount"]) for row in refunds)
    cap_before = number(rules["cap_remaining_before_transaction"][TARGET])
    paid = sum(number(row["amount"]) for row in payments)
    due = min(net * number(rate), cap_before) - paid
    action = next(
        a
        for a in task2().evaluation_criteria.actions
        if a.name == "create_cashback_investigation"
    )
    assert due == number(action.arguments["amount"])
    assert due != min(net * number(rate), cap_before - paid) - paid
    source = (data_dir / "corpus/knowledge/kb_reward_reconciliation.md").read_text()
    assert "совпадающие по владельцу" in source and "счёту и карте" in source
    assert "до покупки" in source
    ids = {receipt.record_id for receipt in task2().evaluation_criteria.result_evidence}
    assert {row["id"] for row in refunds + payments} <= ids
    session = PlatformSession(task2(), get_environment, max_calls=100, max_errors=100)
    for step in task2().evaluation_criteria.actions:
        if step.name == "create_cashback_investigation":
            break
        response = session.call(step.name, dict(step.arguments))
        if (
            step.name == "ask_client"
            and step.arguments["request"]["type"] == "transaction_selection"
        ):
            selection = json.loads(response.content[0].text)["data"]
            assert selection["id"] == TARGET and selection["date"] == purchase["date"]
            assert selection["merchant"] == purchase["merchant"]
            assert selection["amount"] == purchase["amount"]


# Explicitly retain the historical scenario version; composite tests live separately.
pytestmark = pytest.mark.usefixtures("fees_rewards_parent_scenarios")
