"""Bank18 recipient/route grouping and received refund provenance."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR as DATA
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

ROUTES = [
    (r, 1)
    for r in (
        "canonical",
        "details",
        "reversed",
        "no_anchor",
        "second_anchor",
        "metadata_retry",
    )
] + [
    (r, 0)
    for r in (
        "noop",
        "pending_deducted",
        "old_amount",
        "unrelated_refund",
        "missing_second",
        "missing_group",
        "missing_group_failed_then_correct",
        "missing_amount_failed_then_correct",
        "group_then_correct",
        "early_then_repaired",
        "namesake",
        "merchant_qr",
        "refund_in_group",
        "wrong_then_correct",
        "duplicate",
        "missing_history",
        "late_history",
        "missing_policy",
        "late_policy",
        "cancel_completed",
        "direct_refund",
    )
]


def make_session(route):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 18"))
    actions = deepcopy(task.evaluation_criteria.actions)
    case = next(a for a in actions if a.name == "create_case")
    reads = [a for a in actions if a.name == "get_transactions"]
    actions = [a for a in actions if a.name != "calculate"]
    if route == "details":
        from tau2.data_model.tasks import Action

        actions = [a for a in actions if a not in reads]
        actions[actions.index(case) : actions.index(case)] = [
            Action(
                action_id=f"detail_{id}",
                name="get_transaction_details",
                arguments={"transaction_id": id},
            )
            for id in (
                "txn_844217",
                "txn_844218",
                "txn_844219",
                "txn_844220",
            )
        ]
    if route in ("missing_history", "late_history", "early_then_repaired"):
        actions = [a for a in actions if a not in reads]
        if route in ("late_history", "early_then_repaired"):
            actions.extend(reads)
    policy = [
        a
        for a in actions
        if a.name == "file_read"
        and a.arguments["path"] == "/policies/transfers_fraud.md"
    ]
    if route in ("missing_policy", "late_policy"):
        actions = [a for a in actions if a not in policy]
        if route == "late_policy":
            actions.extend(policy)
    changes = {
        "pending_deducted": {"amount": 43000.0},
        "old_amount": {"amount": 38000.0},
        "unrelated_refund": {"amount": 38000.0},
        "missing_second": {"transaction_ids": ["txn_844217"]},
        "missing_group": {"transaction_ids": None},
        "namesake": {"transaction_ids": ["txn_844217", "txn_844221"]},
        "merchant_qr": {"transaction_ids": ["txn_844217", "txn_844222"]},
        "refund_in_group": {
            "transaction_ids": ["txn_844217", "txn_844218", "txn_844219"]
        },
        "reversed": {"transaction_ids": ["txn_844219", "txn_844217"]},
        "no_anchor": {"transaction_id": None},
        "second_anchor": {"transaction_id": "txn_844219"},
    }
    case.arguments.update(changes.get(route, {}))
    session = PlatformSession(task, get_environment, max_calls=200, max_errors=30)
    if route != "noop":
        for action in actions:
            if action is case and route == "wrong_then_correct":
                session.call(case.name, {**case.arguments, "amount": 43000.0})
            if action is case and route == "group_then_correct":
                session.call(
                    case.name,
                    {**case.arguments, "transaction_ids": ["txn_844217", "txn_844221"]},
                )
            if action is case and route in (
                "missing_group_failed_then_correct",
                "missing_amount_failed_then_correct",
            ):
                omitted = (
                    "transaction_ids"
                    if route == "missing_group_failed_then_correct"
                    else "amount"
                )
                bad = {**case.arguments, "responsible_unit": "payments"}
                bad.pop(omitted)
                session.call(case.name, bad)
            if action is case and route == "metadata_retry":
                session.call(
                    case.name, {**case.arguments, "responsible_unit": "payments"}
                )
            session.call(action.name, action.arguments)
    if route == "early_then_repaired":
        session.call(case.name, case.arguments)
    if route == "duplicate":
        session.call(case.name, case.arguments)
    if route == "cancel_completed":
        session.call("release_hold", {"transaction_id": "txn_844217"})
    if route == "direct_refund":
        session.call(
            "refund_transaction", {"transaction_id": "txn_844217", "amount": 46000.0}
        )
    return session


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize("route,expected", ROUTES, ids=[r[0] for r in ROUTES])
def test_bank18_routes(route, expected, mode):
    session = make_session(route)
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if dest := os.environ.get("BANK18_EVIDENCE"):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": "Bank 18",
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": reward.model_dump(mode="json"),
                    "errors": session.errors,
                    "trajectory": [
                        m.model_dump(mode="json") for m in session.trajectory()
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info


def test_bank18_migration_is_addressed_and_idempotent():
    module = Path(__file__).resolve().parents[3] / "scripts/banking_ru/harden_bank18.py"
    assert module.exists(), "Bank18 addressed migration is missing"
    from scripts.banking_ru.harden_bank18 import with_contract, with_documents

    tasks = json.loads((DATA / "tasks.json").read_text())
    db = json.loads((DATA / "db.json").read_text())
    changed_tasks, changed_db = with_contract(tasks, db)
    assert [t for t in changed_tasks if t["id"] != "Bank 18"] == [
        t for t in tasks if t["id"] != "Bank 18"
    ]
    for collection in db:
        if collection != "transactions":
            assert changed_db[collection] == db[collection]
    allowed = {
        "txn_844217",
        "txn_844218",
        "txn_844219",
        "txn_844220",
        "txn_844221",
        "txn_844222",
        "txn_844223",
        "txn_844100",
    }
    assert {
        k: v for k, v in changed_db["transactions"].items() if k not in allowed
    } == {k: v for k, v in db["transactions"].items() if k not in allowed}
    assert with_contract(changed_tasks, changed_db) == (changed_tasks, changed_db)
    docs = {
        p: (DATA / "corpus" / p.lstrip("/")).read_text()
        for p in ("/knowledge/kb_180.md", "/policies/transfers_fraud.md")
    }
    revised = with_documents(docs)
    assert with_documents(revised) == revised
    assert (
        changed_db["transactions"]["txn_844217"]["recipient_id"]
        == changed_db["transactions"]["txn_844219"]["recipient_id"]
    )
    assert (
        changed_db["transactions"]["txn_844221"]["recipient_id"]
        != changed_db["transactions"]["txn_844219"]["recipient_id"]
    )


def test_bank18_cli_applies_only_to_explicit_copy(tmp_path):
    import subprocess
    import sys

    tasks = json.loads((DATA / "tasks.json").read_text())
    target = next(t for t in tasks if t["id"] == "Bank 18")
    target["ticket"] = "old ticket to migrate"
    (tmp_path / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False))
    (tmp_path / "db.json").write_bytes((DATA / "db.json").read_bytes())
    for source in ("knowledge/kb_180.md", "policies/transfers_fraud.md"):
        dest = tmp_path / "corpus" / source
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((DATA / "corpus" / source).read_bytes())
    script = Path(__file__).resolve().parents[3] / "scripts/banking_ru/harden_bank18.py"
    cmd = [sys.executable, str(script), "--data-dir", str(tmp_path)]
    stale = subprocess.run(cmd + ["--check"], capture_output=True, text=True)
    assert stale.returncode != 0 and "stale" in stale.stderr
    applied = subprocess.run(cmd + ["--apply"], capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    checked = subprocess.run(cmd + ["--check"], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    reapplied = subprocess.run(cmd + ["--apply"], capture_output=True, text=True)
    assert reapplied.returncode == 0, reapplied.stderr
    assert before == {
        str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
