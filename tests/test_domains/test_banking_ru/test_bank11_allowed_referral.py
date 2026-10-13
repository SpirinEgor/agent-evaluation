"""Bank11 permits one sourced, authorized nonmonetary linkage review only."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def test_bank11_refund_linkage_review_is_an_allowed_nonmonetary_alternative():
    task = next(t for t in get_tasks() if t.id == "Bank 11")
    session = PlatformSession(task, get_environment)
    session.replay_actions(deepcopy(task.evaluation_criteria.actions))
    # kb111 permits specialist linkage review; RO11 supplies the referral process.
    assert not session.call(
        "file_read", {"path": "/policies/security_escalation.md"}
    ).is_error
    assert not session.call(
        "escalate_to_human",
        {
            "customer_id": "smirnov_d_5502",
            "request": {"type": "refund_linkage_review"},
            "transaction_id": "txn_771204",
        },
    ).is_error
    assert session.errors == 0
    assert not session.call("done", {}).is_error
    _check(session, "allowed", 1.0)


REFERRAL = {
    "customer_id": "smirnov_d_5502",
    "request": {"type": "refund_linkage_review"},
    "transaction_id": "txn_771204",
}


def _check(session, route, expected):
    rewards = {"half": session.grade(), "full": grade_full_session(session).reward_info}
    evidence = os.environ.get("BANK11_EVIDENCE")
    if evidence:
        root = Path(evidence)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / (route.replace("/", "_") + ".json")
        path.write_text(
            json.dumps(
                {
                    "route": route,
                    "expected": expected,
                    "session_id": session.token,
                    "rewards": {
                        k: v.model_dump(mode="json") for k, v in rewards.items()
                    },
                    "events": session.mcp_events,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        path.chmod(0o600)
    for reward in rewards.values():
        assert reward.final_reward == expected, reward.model_dump(mode="json")
    assert rewards["half"].reward_breakdown == rewards["full"].reward_breakdown


@pytest.mark.parametrize(
    "route, expected",
    [
        ("reference", 1.0),
        ("noop", 0.0),
        ("explicit_support", 1.0),
        ("wrong_type", 0.0),
        ("wrong_transaction", 0.0),
        ("foreign_transaction", 0.0),
        ("wrong_customer", 0.0),
        ("missing_request", 0.0),
        ("missing_transaction", 0.0),
        ("wrong_unit_then_retry", 0.0),
        ("extra_card", 0.0),
        ("duplicate_referral", 0.0),
        ("open_dispute", 0.0),
        ("refund_transaction", 0.0),
        ("before_identity_then_retry", 0.0),
        ("late_security", 0.0),
        ("unreceived_security", 0.0),
        ("late_/knowledge/kb_111.md", 0.0),
        ("late_/knowledge/kb_110.md", 0.0),
        ("late_/policies/disputes.md", 0.0),
        ("late_purchase", 0.0),
        ("late_return", 0.0),
    ],
)
def test_bank11_referral_boundaries(route, expected):
    task = next(t for t in get_tasks() if t.id == "Bank 11")
    session = PlatformSession(task, get_environment)
    actions = deepcopy(task.evaluation_criteria.actions)
    late = []
    if route.startswith("late_/"):
        path = route.removeprefix("late_")
        late = [a for a in actions if a.arguments.get("path") == path]
        actions = [a for a in actions if a not in late]
    if route in {"late_purchase", "late_return"}:
        actions = [a for a in actions if a.name != "get_transactions"]
    if route == "before_identity_then_retry":
        assert session.call("escalate_to_human", deepcopy(REFERRAL)).is_error
    if route != "noop":
        session.replay_actions(actions)
        assert session.errors == (1 if route == "before_identity_then_retry" else 0)
    if route in {"late_purchase", "late_return"}:
        early_id = "txn_771260" if route == "late_purchase" else "txn_771204"
        assert not session.call(
            "get_transaction_details", {"transaction_id": early_id}
        ).is_error
    if route not in {"reference", "noop"}:
        if route not in {"late_security", "unreceived_security"}:
            assert not session.call(
                "file_read", {"path": "/policies/security_escalation.md"}
            ).is_error
        pending = None
        if route == "unreceived_security":
            pending = session.begin_call(
                "file_read", {"path": "/policies/security_escalation.md"}, None
            )
            assert not session.call(
                "file_read",
                {"path": "/policies/security_escalation.md"},
                attempt=pending,
            ).is_error
        args = deepcopy(REFERRAL)
        tool = "escalate_to_human"
        if route == "explicit_support":
            args["responsible_unit"] = "support"
        if route == "wrong_type":
            args["request"] = {"type": "service_claim_review"}
        if route == "wrong_transaction":
            args["transaction_id"] = "txn_771260"
        if route == "foreign_transaction":
            args["transaction_id"] = "txn_066091"
        if route == "wrong_customer":
            args["customer_id"] = "egorov_m_1145"
        if route == "missing_request":
            args.pop("request")
        if route == "missing_transaction":
            args.pop("transaction_id")
        if route == "wrong_unit_then_retry":
            args["responsible_unit"] = "investment_products"
        if route == "extra_card":
            args["card_id"] = "card_1145"
        if route == "open_dispute":
            tool, args = (
                "open_dispute",
                {
                    "transaction_id": "txn_771204",
                    "reason": "service_not_provided",
                    "amount": 15490,
                },
            )
        if route == "refund_transaction":
            tool, args = (
                "refund_transaction",
                {
                    "transaction_id": "txn_771204",
                    "request": {"type": "purchase_refund"},
                },
            )
        result = session.call(tool, args)
        if expected == 1.0:
            assert not result.is_error
        if pending:
            session.result_sent(pending)
        if route == "late_security":
            assert not session.call(
                "file_read", {"path": "/policies/security_escalation.md"}
            ).is_error
        session.replay_actions(late)
        if route in {"late_purchase", "late_return"}:
            late_id = "txn_771204" if route == "late_purchase" else "txn_771260"
            assert not session.call(
                "get_transaction_details", {"transaction_id": late_id}
            ).is_error
        if route in {
            "wrong_unit_then_retry",
            "duplicate_referral",
            "before_identity_then_retry",
        }:
            assert not session.call("escalate_to_human", deepcopy(REFERRAL)).is_error
    assert not session.call("done", {}).is_error
    _check(session, route, expected)


def test_bank11_referral_migration_is_scoped_and_repeatable(tmp_path, monkeypatch):
    import subprocess
    import sys

    from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR

    data = Path(BANKING_RU_DATA_DIR)
    original = json.loads((data / "tasks.json").read_text())
    legacy = deepcopy(original)
    task = next(t for t in legacy if t["id"] == "Bank 11")
    c = task["evaluation_criteria"]
    c.pop("db_alternative_action_additions", None)
    c["forbidden_actions"].append(
        {
            "action_id": "forbid_escalate_to_human_Bank 11",
            "name": "escalate_to_human",
            "arguments": {},
            "compare_args": [],
        }
    )
    (tmp_path / "tasks.json").write_text(json.dumps(legacy, ensure_ascii=False))
    import shutil

    shutil.copytree(data / "corpus", tmp_path / "corpus")
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts/banking_ru/build_bank11_referral_contract.py"
    )
    command = [sys.executable, str(script), "--data-dir", str(tmp_path)]
    assert subprocess.run([*command, "--check"], capture_output=True).returncode == 1
    subprocess.run([*command, "--apply"], check=True, capture_output=True)
    migrated = json.loads((tmp_path / "tasks.json").read_text())
    assert [t for t in migrated if t["id"] != "Bank 11"] == [
        t for t in legacy if t["id"] != "Bank 11"
    ]
    assert migrated == original
    before = (tmp_path / "tasks.json").read_bytes()
    subprocess.run([*command, "--apply"], check=True, capture_output=True)
    subprocess.run([*command, "--check"], check=True, capture_output=True)
    assert (tmp_path / "tasks.json").read_bytes() == before

    monkeypatch.syspath_prepend(str(script.parent))
    from scripts.banking_ru.build_discovery_contract import with_contract as discovery
    from scripts.banking_ru.build_escalation_contract import with_contract as escalation

    assert escalation(migrated) == migrated
    # A shared builder must not erase the optional referral witnesses.
    refreshed = deepcopy(migrated)
    discovery(refreshed, json.loads((data / "db.json").read_text()))
    assert refreshed == migrated
