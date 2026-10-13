"""Authorized optional confirmations must not disable identity or case guards."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

ROUTES = [
    ("reference", 1),
    ("noop", 0),
    ("authenticated_question", 1),
    ("repeated_authenticated_question", 1),
    ("question_before_identity_then_retry", 0),
    ("foreign_question", 0),
    ("free_question", 0),
    ("secret_question", 0),
    ("missing_identity", 0),
    ("missing_general", 0),
    ("missing_product_source", 0),
]
CASES = [(n, route, expected) for n in (19, 26) for route, expected in ROUTES]
CASES += [
    (19, "pending_limit_change", 0),
    (26, "missing_utility_answer", 0),
    (26, "wrong_amount", 0),
    (26, "wrong_contact_date", 0),
    (26, "unrelated_transaction", 0),
]


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize(
    "number,route,expected",
    CASES,
    ids=[f"Bank{n}-{route}" for n, route, _ in CASES],
)
def test_optional_question_whole_grade(number, route, expected, mode):
    task = next(t for t in get_tasks() if t.id == f"Bank {number}")
    actions = deepcopy(task.evaluation_criteria.actions)
    owner = next(
        a.arguments["customer_id"] for a in actions if a.name == "verify_identity"
    )
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    question = {"customer_id": owner, "request": {"type": "confirm_operations"}}
    if route == "noop":
        actions = []
    if route == "missing_identity":
        actions = [a for a in actions if a.name != "verify_identity"]
    if route in ("missing_general", "missing_product_source"):
        path = (
            "/policies/general.md"
            if route == "missing_general"
            else ("/knowledge/kb_200.md" if number == 19 else "/knowledge/kb_180.md")
        )
        actions = [
            a
            for a in actions
            if not (a.name == "file_read" and a.arguments.get("path") == path)
        ]
    if route == "missing_utility_answer":
        actions = [
            a
            for a in actions
            if not (
                a.name == "ask_client"
                and a.arguments.get("request", {}).get("type")
                == "utility_payment_accounts"
            )
        ]
    if route in ("wrong_amount", "wrong_contact_date", "unrelated_transaction"):
        case = next(a for a in actions if a.name == "create_case")
        if route == "wrong_amount":
            case.arguments["amount"] = 9420
        elif route == "wrong_contact_date":
            case.arguments["next_contact_date"] = "2026-10-13"
        else:
            case.arguments["transaction_ids"] = ["txn_889231", "txn_889240"]
    if route == "question_before_identity_then_retry":
        assert session.call("ask_client", question).is_error
    added_results = []
    for action in actions:
        session.call(action.name, action.arguments)
        if action.name == "verify_identity" and route in (
            "authenticated_question",
            "repeated_authenticated_question",
            "question_before_identity_then_retry",
            "foreign_question",
            "free_question",
            "secret_question",
            "missing_utility_answer",
        ):
            args = deepcopy(question)
            if route == "foreign_question":
                args["customer_id"] = "belova_n_2201"
            if route == "free_question":
                args["question"] = "Подтвердите операции обращения."
            if route == "secret_question":
                args["question"] = "Код подтверждения 104582"
            count = 2 if route == "repeated_authenticated_question" else 1
            for _ in range(count):
                result = session.call("ask_client", args)
                added_results.append(result.model_dump(mode="json"))
                if route in (
                    "authenticated_question",
                    "repeated_authenticated_question",
                    "question_before_identity_then_retry",
                    "missing_utility_answer",
                ):
                    assert not result.is_error
                    assert json.loads(result.content[0].text)["status"] == "unknown"
    if route == "pending_limit_change":
        session.call(
            "set_limit", {"card_id": "card_5581", "limit_type": "sbp", "amount": 150000}
        )
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if folder := os.environ.get("OPTIONAL_QUESTION_RECEIPTS"):
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"Bank{number}-{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "route": route,
                    "expected": expected,
                    "mode": mode,
                    "pid": os.getpid(),
                    "token": session.token,
                    "reward": reward.model_dump(mode="json"),
                    "extra_results": added_results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.model_dump(mode="json")


@pytest.mark.parametrize("number", [19, 26], ids=["Bank19", "Bank26"])
@pytest.mark.parametrize("mode", ["HALF", "FULL"])
def test_question_requires_received_identity_result(number, mode):
    task = next(t for t in get_tasks() if t.id == f"Bank {number}")
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    for action in task.evaluation_criteria.actions:
        if action.name != "verify_identity":
            session.call(action.name, action.arguments)
            continue
        pending = session.begin_call(action.name, action.arguments, None)
        assert not session.call(action.name, action.arguments, attempt=pending).is_error
        assert not session.call(
            "ask_client",
            {
                "customer_id": action.arguments["customer_id"],
                "request": {"type": "confirm_operations"},
            },
        ).is_error
        session.result_sent(pending)
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if folder := os.environ.get("OPTIONAL_QUESTION_RECEIPTS"):
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"Bank{number}-unreceived_identity-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "route": "unreceived_identity",
                    "expected": 0,
                    "mode": mode,
                    "pid": os.getpid(),
                    "token": session.token,
                    "reward": reward.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == 0, reward.model_dump(mode="json")
