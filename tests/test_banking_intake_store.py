"""Canonical answers have one source; published legacy snapshots remain separate."""

import json

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
from tau2.platform.session import PlatformSession


def test_canonical_client_answers_have_one_authoritative_source():
    root = BANKING_RU_DATA_DIR
    db = json.loads((root / "db.json").read_bytes())
    answers = json.loads((root / "intake_answers.json").read_bytes())
    assert "client_answers" not in db
    for task in get_tasks():
        initial = task.initial_state
        if initial and initial.initialization_data:
            assert "client_answers" not in (
                initial.initialization_data.agent_data or {}
            )
    for rows in answers.values():
        for row in rows:
            assert "keywords" not in row
            assert set(row) - {"answer", "task_id"}, "Unreachable free-text answer"


@pytest.mark.parametrize(
    "task_id, field",
    [
        ("Bank 33", "account_closure_signature"),
        ("Bank 35", "loan_repayment_confirmation"),
        ("Bank 42", "tariff_change_confirmation"),
        ("Bank 47", "device_answers"),
    ],
)
def test_scenario_answers_are_available_without_initial_state_overlay(task_id, field):
    task = next(task for task in get_tasks() if task.id == task_id)
    customer = next(
        a.arguments["customer_id"]
        for a in task.evaluation_criteria.actions
        if a.name == "verify_identity"
    )
    session = PlatformSession(task, get_environment)
    rows = session.env.tools.db.client_answers[customer]
    assert sum(getattr(row, field) is not None for row in rows) == 1
    assert {row.identity_method for row in rows if row.identity_method} == {
        "code_word",
        "birth_date",
    }


@pytest.mark.parametrize("method", ["code_word", "birth_date"])
def test_identity_request_uses_stored_answer_without_keywords(method):
    root = BANKING_RU_DATA_DIR
    answers = json.loads((root / "intake_answers.json").read_bytes())
    customer = next(iter(answers))
    expected = next(
        row for row in answers[customer] if row.get("identity_method") == method
    )
    env = get_environment()
    response = env.tools.ask_client(
        customer, request={"type": "identity", "method": method}
    ).root
    assert response["status"] == "answered"
    assert response["data"]["credential"] == expected["credential"]
    assert response["text"] == expected["answer"]
