"""Immutable utility execution accounts determine the eligible claim group."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

ROUTES = [
    ("canonical", 1),
    ("details_without_history", 1),
    ("list_only_anchor", 1),
    ("noop", 0),
    ("old_group", 0),
    ("old_group_then_correct", 0),
    ("deduct_processing", 0),
    ("full_original_amount", 0),
    ("include_correct_payment", 0),
    ("wrong_anchor", 0),
    ("wrong_accounts", 0),
    ("missing_accounts", 0),
    ("late_accounts", 0),
    ("missing_execution", 0),
    ("late_execution", 0),
    ("duplicate_case", 0),
    ("foreign_read", 0),
]


def make_session(route):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 26"))
    actions = deepcopy(task.evaluation_criteria.actions)
    case = next(a for a in actions if a.name == "create_case")
    case.arguments.update(
        amount=4180.0, transaction_id="txn_889231", transaction_ids=["txn_889231"]
    )
    clarification = next(a for a in actions if a.action_id == "clarification_Bank 26")
    reads = [a for a in actions if a.name == "get_transactions"]
    actions = [a for a in actions if a.name not in {"get_transactions", "calculate"}]
    target = actions.index(case)
    if route != "missing_execution":
        if route == "details_without_history":
            from tau2.data_model.tasks import Action

            reads = [
                Action(
                    action_id=f"details_{id}",
                    name="get_transaction_details",
                    arguments={"transaction_id": id},
                )
                for id in ("txn_889231", "txn_889232", "txn_889233", "txn_889234")
            ]
        actions[target:target] = reads
    if route == "missing_accounts":
        actions.remove(clarification)
    if route == "late_accounts":
        actions.remove(clarification)
        actions.append(clarification)
    if route == "late_execution":
        actions = [a for a in actions if a not in reads] + reads
    changes = {
        "old_group": {
            "amount": 9120.0,
            "transaction_ids": ["txn_889231", "txn_889232"],
        },
        "deduct_processing": {"amount": 3880.0},
        "full_original_amount": {"amount": 4780.0},
        "include_correct_payment": {"transaction_ids": ["txn_889231", "txn_889240"]},
        "wrong_anchor": {"transaction_id": "txn_889232"},
        "wrong_accounts": {
            "utility_wrong_account": "40817810900005521",
            "utility_correct_account": "40817810900001187",
        },
    }
    if route == "list_only_anchor":
        case.arguments["transaction_id"] = None
    case.arguments.update(changes.get(route, {}))
    session = PlatformSession(task, get_environment, max_calls=200, max_errors=30)
    if route != "noop":
        for a in actions:
            if a is case and route == "old_group_then_correct":
                session.call(
                    a.name,
                    {
                        **a.arguments,
                        "amount": 9120.0,
                        "transaction_ids": ["txn_889231", "txn_889232"],
                    },
                )
            session.call(a.name, a.arguments)
    if route == "duplicate_case":
        session.call("create_case", case.arguments)
    if route == "foreign_read":
        session.call("get_transaction_details", {"transaction_id": "txn_088215"})
    return session


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize("route,expected", ROUTES, ids=[r[0] for r in ROUTES])
def test_bank26_routes(route, expected, mode):
    session = make_session(route)
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if dest := os.environ.get("BANK26_EVIDENCE"):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": "Bank 26",
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": reward.model_dump(mode="json"),
                    "errors": session.errors,
                    "mcp_events": session.mcp_events,
                    "trajectory": [
                        message.model_dump(mode="json")
                        for message in session.trajectory()
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info


def test_bank26_execution_receipts_are_historical():
    session = make_session("canonical")
    rows = next(
        json.loads(result.content)["items"]
        for call, result in session.log
        if call.name == "get_transactions"
    )
    by_id = {r["id"]: r for r in rows}
    first, second = by_id["txn_889231"], by_id["txn_889232"]
    assert first["execution_payee"] == second["execution_payee"] == "Мосэнергосбыт"
    assert first["execution_account"] == "40817810900001187"
    assert second["execution_account"] == "40817810900005521"
    assert first["template_id"] == second["template_id"] == "utility_electricity_2208"
    assert first["template_revision"] == "1"
    assert second["template_revision"] == "2"
    assert second["template_effective_at"] == "2026-08-21"
    assert second["template_history"][-1]["account"] == second["execution_account"]
    assert first["execution_account"] != first["template_history"][-1]["account"]
    assert by_id["txn_889233"]["original_transaction_id"] == "txn_889231"
    assert by_id["txn_889234"]["original_transaction_id"] == "txn_889231"
    assert by_id["txn_889234"]["status"] == "processing"


def test_bank26_migration_is_addressed_and_idempotent():
    from scripts.banking_ru.harden_bank26 import DATA, with_contract, with_documents

    tasks = json.loads((DATA / "tasks.json").read_text())
    db = json.loads((DATA / "db.json").read_text())
    changed_tasks, changed_db = with_contract(tasks, db)
    assert [t for t in changed_tasks if t["id"] != "Bank 26"] == [
        t for t in tasks if t["id"] != "Bank 26"
    ]
    changed_ids = {
        "txn_889231",
        "txn_889232",
        "txn_889150",
        "txn_889233",
        "txn_889234",
        "txn_889240",
        "txn_889241",
    }
    for collection in db:
        if collection != "transactions":
            assert changed_db[collection] == db[collection]
    assert {
        k: v for k, v in changed_db["transactions"].items() if k not in changed_ids
    } == {k: v for k, v in db["transactions"].items() if k not in changed_ids}
    assert with_contract(changed_tasks, changed_db) == (changed_tasks, changed_db)
    docs = {
        p: (DATA / "corpus" / p.lstrip("/")).read_text()
        for p in ("/knowledge/kb_180.md", "/policies/transfers_fraud.md")
    }
    new_docs = with_documents(docs)
    assert with_documents(new_docs) == new_docs
