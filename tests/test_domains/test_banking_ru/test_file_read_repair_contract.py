"""Addressed whole-file repair: valid retries pass; missing received rules do not."""

import json
from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

NUMBERS = [2, 5, 7, 8, 9, 10, 11, 13, 19, 23]
ROUTES = {
    "reference": 1,
    "noop": 0,
    "duplicate_read": 1,
    "reordered_reads": 1,
    "failed_range_retry": 1,
    "failed_range_only": 0,
    "missing_general": 0,
    "delayed_general": 0,
    "missing_identity_answer": 0,
    "wrong_identity": 0,
    "birth_identity": 1,
    "preauth_read": 0,
    "foreign_read": 0,
    "extra_own_read": 1,
}


def payload(result):
    return json.loads(result.content[0].text) if not result.is_error else None


@pytest.mark.parametrize("number", NUMBERS, ids=lambda n: f"Bank-{n}")
@pytest.mark.parametrize("route", list(ROUTES))
@pytest.mark.parametrize("mode", ["HALF", "FULL"])
def test_whole_file_repair_grade(number, route, mode):
    task = next(t for t in get_tasks() if t.id == f"Bank {number}")
    actions = deepcopy(task.evaluation_criteria.actions)
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    owner = next(
        a.arguments["customer_id"] for a in actions if a.name == "verify_identity"
    )
    foreign = next(cid for cid in session.env.tools.db.customers if cid != owner)
    if route == "noop":
        actions = []
    if route == "reordered_reads":
        actions = list(reversed([a for a in actions if a.name == "file_read"])) + [
            a for a in actions if a.name != "file_read"
        ]
    if route == "preauth_read":
        session.call("get_customer_profile", {"customer_id": owner})
    answer = None
    delayed = None
    for action in actions:
        args = deepcopy(action.arguments)
        is_general = (
            action.name == "file_read" and args.get("path") == "/policies/general.md"
        )
        if is_general:
            if route == "missing_general":
                continue
            if route in ["failed_range_retry", "failed_range_only"]:
                assert session.call("file_read", {**args, "end_line": 12}).is_error
                if route == "failed_range_only":
                    continue
            if route == "delayed_general":
                delayed = session.begin_call("file_read", args, None)
                assert not session.call("file_read", args, attempt=delayed).is_error
                continue
        identity_question = (
            action.name == "ask_client"
            and args.get("request", {}).get("type") == "identity"
        )
        if identity_question:
            if route == "missing_identity_answer":
                continue
            if route == "birth_identity":
                args["request"]["method"] = "birth_date"
        if action.name == "verify_identity":
            if answer:
                args.update(
                    credential=answer["data"]["credential"],
                    answer_id=answer["answer_id"],
                )
            if route == "wrong_identity":
                args["credential"] = "invalid-test-credential"
        result = session.call(action.name, args)
        if identity_question:
            answer = payload(result)
        if is_general and route == "duplicate_read":
            assert not session.call("file_read", args).is_error
        if action.name == "verify_identity":
            if route in ["foreign_read", "extra_own_read"]:
                session.call(
                    "get_customer_profile",
                    {"customer_id": foreign if route == "foreign_read" else owner},
                )
    if delayed:
        session.result_sent(delayed)
    assert not session.call("done", {}).is_error
    grade = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    assert grade.final_reward == ROUTES[route], {
        "task": task.id,
        "route": route,
        "mode": mode,
        "components": grade.reward_breakdown,
    }
