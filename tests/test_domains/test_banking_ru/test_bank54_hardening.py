"""Bank54: linked partial refunds and independent cancellation facts."""

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

OWNER = "kuznetsov_i_3140"
TARGET = "txn_314101"
REFUNDS = ["txn_314104", "txn_314105", "txn_314106"]
PROOF = "doc_3140_fitness_cancel"


def _call(session, name, args):
    result = session.call(name, args)
    assert not result.is_error, result
    try:
        return json.loads(result.content[0].text)
    except json.JSONDecodeError:
        return result.content[0].text


def test_bank54_new_data_are_discoverable():
    session = PlatformSession(
        next(t for t in get_tasks() if t.id == "Bank 54"), get_environment
    )
    db = session.env.tools.db
    assert db.transactions[REFUNDS[0]].original_transaction_id == TARGET
    assert db.subscriptions["sub_3141"].cancelled_at == "2026-07-22"
    assert "2026-07-20" in db.documents[PROOF].body


CASES = [
    (s, 1)
    for s in [
        "reference",
        "case",
        "case_list",
        "details",
        "details_selected",
        "birth_date",
        "other_reason",
        "unknown_default_retry",
        "unknown_list_retry",
    ]
] + [
    (s, 0)
    for s in [
        "noop",
        "wrong_2900",
        "wrong_1700",
        "wrong_1500",
        "duplicate",
        "duplicate_case",
        "default_omitted_after",
        "default_null_after",
        "cancel_yandex",
        "repeat_cancel",
        "direct_refund",
        "no_kb",
        "archived_kb",
        "no_ro06",
        "historical_only",
        "no_refund_receipts",
        "early_repaired",
        "kb_result_late",
        "proof_result_late",
        "refund_result_late",
        "wrong_attempt_repaired",
        "known_wrong_target_failed",
        "known_extra_target_failed",
    ]
]


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize("route,expected", CASES, ids=[c[0] for c in CASES])
def test_bank54_routes(route, expected, mode):
    task = next(t for t in get_tasks() if t.id == "Bank 54")
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=50)
    claim = {
        "transaction_id": TARGET,
        "reason": "subscription_after_cancel",
        "amount": 2200,
    }
    if route == "early_repaired":
        session.call("open_dispute", claim)
    delayed = None
    if route != "noop":
        for path in ["/policies/general.md", "/policies/identity.md"]:
            _call(session, "file_read", {"path": path})
        _call(session, "find_customer", {"phone": "89213140058"})
        answer = _call(
            session,
            "ask_client",
            {
                "customer_id": OWNER,
                "request": {
                    "type": "identity",
                    "method": "birth_date" if route == "birth_date" else "code_word",
                },
            },
        )
        _call(
            session,
            "verify_identity",
            {
                "customer_id": OWNER,
                "credential": answer["data"]["credential"],
                "answer_id": answer["answer_id"],
            },
        )
        for path in ["/policies/subscriptions.md", "/knowledge/kb_111.md"]:
            if (
                route in {"no_kb", "archived_kb"}
                and "kb_111" in path
                or route == "no_ro06"
                and "subscriptions" in path
            ):
                continue
            if route == "kb_result_late" and "kb_111" in path:
                delayed = session.begin_call("file_read", {"path": path}, None)
                session.call("file_read", {"path": path}, attempt=delayed)
            else:
                _call(session, "file_read", {"path": path})
        if route == "archived_kb":
            _call(session, "file_read", {"path": "/knowledge/kb_101.md"})
        _call(session, "get_subscriptions", {"customer_id": OWNER})
        if route != "historical_only":
            if route == "proof_result_late":
                delayed = session.begin_call(
                    "get_documents", {"customer_id": OWNER}, None
                )
                session.call("get_documents", {"customer_id": OWNER}, attempt=delayed)
            else:
                _call(session, "get_documents", {"customer_id": OWNER})
        ids = [TARGET, "txn_314107"] + (
            [] if route == "no_refund_receipts" else REFUNDS
        )
        if route in {"details", "details_selected"} or route in {
            "no_refund_receipts",
            "refund_result_late",
        }:
            for transaction_id in ids:
                if route == "details_selected" and transaction_id == "txn_314107":
                    continue
                if route == "refund_result_late" and transaction_id == REFUNDS[0]:
                    delayed = session.begin_call(
                        "get_transaction_details",
                        {"transaction_id": transaction_id},
                        None,
                    )
                    session.call(
                        "get_transaction_details",
                        {"transaction_id": transaction_id},
                        attempt=delayed,
                    )
                else:
                    _call(
                        session,
                        "get_transaction_details",
                        {"transaction_id": transaction_id},
                    )
        else:
            _call(session, "get_transactions", {"customer_id": OWNER})
        if route in {"cancel_yandex", "repeat_cancel"}:
            session.call(
                "cancel_subscription",
                {
                    "subscription_id": "sub_3142"
                    if route == "cancel_yandex"
                    else "sub_3141"
                },
            )
        if route == "direct_refund":
            session.call(
                "refund_transaction",
                {"transaction_id": TARGET, "request": {"type": "purchase_refund"}},
            )
        if route == "known_extra_target_failed":
            assert session.call(
                "create_case",
                {
                    "customer_id": OWNER,
                    "category": "merchant_investigation",
                    "transaction_id": TARGET,
                    "transaction_ids": [TARGET, "txn_314107"],
                    "amount": 2200,
                    "responsible_unit": "support",
                },
            ).is_error
        if route == "known_wrong_target_failed":
            assert session.call(
                "create_case",
                {
                    "customer_id": OWNER,
                    "category": "merchant_investigation",
                    "transaction_id": "txn_314107",
                    "amount": 2200,
                    "responsible_unit": "support",
                },
            ).is_error
        if route == "unknown_list_retry":
            assert session.call(
                "create_case",
                {
                    "customer_id": OWNER,
                    "category": "merchant_investigation",
                    "transaction_ids": ["txn_unknown_bank54"],
                    "amount": 2200,
                },
            ).is_error
        if route == "unknown_default_retry":
            assert session.call(
                "open_dispute",
                {
                    "transaction_id": "txn_unknown_bank54",
                    "reason": "subscription_after_cancel",
                },
            ).is_error
        if route == "wrong_attempt_repaired":
            session.call("open_dispute", {**claim, "amount": 2900})
            # Successful wrong dispute prevents another dispute; repaired case is still forbidden.
            session.call(
                "create_case",
                {
                    "customer_id": OWNER,
                    "category": "merchant_investigation",
                    "transaction_id": TARGET,
                    "amount": 2200,
                },
            )
        else:
            amount = int(route[6:]) if route.startswith("wrong_") else 2200
            if route in {"case", "case_list", "duplicate_case"}:
                args = {
                    "customer_id": OWNER,
                    "category": "merchant_investigation",
                    "amount": amount,
                }
                args[
                    "transaction_ids" if route == "case_list" else "transaction_id"
                ] = [TARGET] if route == "case_list" else TARGET
                _call(session, "create_case", args)
            else:
                _call(
                    session,
                    "open_dispute",
                    {
                        **claim,
                        "amount": amount,
                        "reason": "other"
                        if route == "other_reason"
                        else claim["reason"],
                    },
                )
            if route in {"default_omitted_after", "default_null_after"}:
                invalid = {k: v for k, v in claim.items() if k != "amount"}
                if route == "default_null_after":
                    invalid["amount"] = None
                assert session.call("open_dispute", invalid).is_error
            if route in {"duplicate", "duplicate_case"}:
                _call(
                    session,
                    "create_case",
                    {
                        "customer_id": OWNER,
                        "category": "merchant_investigation",
                        "transaction_id": TARGET,
                        "amount": 2200,
                    },
                )
        if delayed:
            session.result_sent(delayed)
    _call(session, "done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if destination := os.environ.get("BANK54_HARDENING_EVIDENCE"):
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": reward.model_dump(mode="json"),
                    "messages": [
                        m.model_dump(mode="json") for m in session.trajectory()
                    ],
                    "mcp_events": session.mcp_events,
                    "errors": session.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info


def test_bank54_migration_is_scoped_idempotent(tmp_path):
    from scripts.banking_ru.harden_bank54 import migrate, with_contract

    base = Path(BANKING_RU_DATA_DIR)
    tasks = json.loads((base / "tasks.json").read_text())
    db = json.loads((base / "db.json").read_text())
    before = deepcopy((tasks, db))
    updated = with_contract(tasks, db)
    assert (tasks, db) == before
    assert [t for t in updated[0] if t["id"] != "Bank 54"] == [
        t for t in tasks if t["id"] != "Bank 54"
    ]
    assert with_contract(*updated) == updated
    for key in db:
        if key not in {"transactions", "subscriptions", "documents"}:
            assert db[key] == updated[1][key]
    for key in {"transactions", "subscriptions", "documents"}:
        for id, row in db[key].items():
            if row.get("customer_id") != OWNER:
                assert row == updated[1][key][id]
    shutil.copytree(base, tmp_path / "data")
    migrate(tmp_path / "data")
    migrate(tmp_path / "data", check=True)
    saved = {p: p.read_bytes() for p in (tmp_path / "data").rglob("*") if p.is_file()}
    migrate(tmp_path / "data")
    assert all(p.read_bytes() == raw for p, raw in saved.items())


def test_bank54_migration_refuses_record_collision():
    from scripts.banking_ru.harden_bank54 import with_contract

    base = Path(BANKING_RU_DATA_DIR)
    tasks = json.loads((base / "tasks.json").read_text())
    db = json.loads((base / "db.json").read_text())
    db["transactions"]["txn_314104"] = {
        "id": "txn_314104",
        "customer_id": "someone_else",
    }
    with pytest.raises(ValueError, match="owner"):
        with_contract(tasks, db)


def test_bank54_migration_preserves_refreshed_evidence_order():
    from scripts.banking_ru.harden_bank54 import with_contract

    base = Path(BANKING_RU_DATA_DIR)
    tasks = json.loads((base / "tasks.json").read_text())
    db = json.loads((base / "db.json").read_text())
    tasks, db = with_contract(tasks, db)
    task = next(t for t in tasks if t["id"] == "Bank 54")
    evidence = task["evaluation_criteria"]["result_evidence"]
    discovery = next(
        e for e in evidence if e["evidence_id"] == "common_discovery_Bank 54"
    )
    evidence.remove(discovery)
    evidence.append(discovery)
    assert with_contract(tasks, db) == (tasks, db)
