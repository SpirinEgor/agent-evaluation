"""Bank60: scoped pending requests and date-applicable SBP tariff."""

import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.data_model.tasks import InitializationData, InitialState, RewardType
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/banking_ru/harden_bank60.py"
OWNER = "fomin_r_9200"
TARGET = "card_9200b"


def test_bank60_migration_is_scoped_idempotent_and_check_is_read_only(tmp_path):
    """Missing scoped receipts or changing another task breaks this migration."""
    data = tmp_path / "banking_ru"
    shutil.copytree(BANKING_RU_DATA_DIR, data)
    before = json.loads((data / "tasks.json").read_text())
    command = [sys.executable, str(SCRIPT), "--data-dir", str(data)]
    applied = subprocess.run(command + ["--apply"], capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    after = json.loads((data / "tasks.json").read_text())
    assert [t for t in before if t["id"] != "Bank 60"] == [
        t for t in after if t["id"] != "Bank 60"
    ]
    task = next(t for t in after if t["id"] == "Bank 60")
    requests = json.loads((data / "db.json").read_text())["limit_requests"]
    own = [r for r in requests.values() if r["customer_id"] == OWNER]
    assert len(own) == 3
    assert any(r["channel"] == "cash" and r["status"] == "pending" for r in own)
    assert any(r["object_id"] == "card_9200a" and r["status"] == "pending" for r in own)
    assert any(
        r["object_id"] == TARGET and r["channel"] == "sbp" and r["status"] == "declined"
        for r in own
    )
    action = next(
        a
        for a in task["evaluation_criteria"]["actions"]
        if a["name"] == "get_limit_request"
    )
    assert action.get("result_contains", []) == []
    snapshots = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    checked = subprocess.run(command + ["--check"], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    repeated = subprocess.run(command + ["--apply"], capture_output=True, text=True)
    assert repeated.returncode == 0, repeated.stderr
    assert all(p.read_bytes() == raw for p, raw in snapshots.items())
    # Shared discovery refresh may move its own receipt to the end. An
    # unchanged Bank60 receipt must keep its position instead of causing stale
    # checks between otherwise compatible generators.
    evidence = task["evaluation_criteria"]["result_evidence"]
    discovery = next(
        e for e in evidence if e["evidence_id"] == "common_discovery_Bank 60"
    )
    evidence.remove(discovery)
    evidence.append(discovery)
    (data / "tasks.json").write_text(
        json.dumps(after, ensure_ascii=False, indent=2) + "\n"
    )
    checked = subprocess.run(command + ["--check"], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


ROUTES = [
    ("reference", 1),
    ("independent_reverse", 1),
    ("birth_date", 1),
    ("repeat_reads", 1),
    ("noop", 0),
    ("future_300000", 0),
    ("maximum_500000", 0),
    ("wrong_card_repaired", 0),
    ("unknown_card_repaired", 1),
    ("over_tariff_repaired", 0),
    ("cash_repaired", 0),
    ("early_repaired", 0),
    ("missing_request", 0),
    ("request_received_late", 0),
    ("tariff_received_late", 0),
    ("matching_pending", 0),
]


def _payload(response):
    assert not response.is_error
    return json.loads(response.content[0].text)


def _session(route):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 60"))
    if route == "matching_pending":
        # An addressed synthetic fixture, never another permanent bank task.
        task.initial_state = InitialState(
            initialization_data=InitializationData(
                agent_data={
                    "limit_requests": {
                        "lrq_test_matching": {
                            "id": "lrq_test_matching",
                            "customer_id": OWNER,
                            "requested_limit": 450000.0,
                            "status": "pending",
                            "decided_at": "",
                            "public_reason": "",
                            "request_type": "spending_limit",
                            "object_type": "card",
                            "object_id": TARGET,
                            "channel": "sbp",
                            "period": "per_operation",
                            "requires_decision": True,
                        }
                    }
                }
            )
        )
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=40)
    correct = {"card_id": TARGET, "limit_type": "sbp", "amount": 400000.0}
    if route == "noop":
        return session
    if route == "early_repaired":
        session.call("set_limit", correct)
    for path in ["/policies/general.md", "/policies/identity.md"]:
        session.call("file_read", {"path": path})
    session.call("find_customer", {"phone": "89099200488"})
    answer = _payload(
        session.call(
            "ask_client",
            {
                "customer_id": OWNER,
                "request": {
                    "type": "identity",
                    "method": "birth_date" if route == "birth_date" else "code_word",
                },
            },
        )
    )
    session.call(
        "verify_identity",
        {
            "customer_id": OWNER,
            "credential": answer["data"]["credential"],
            "answer_id": answer["answer_id"],
        },
    )
    for path in ["/policies/cards.md", "/knowledge/kb_200.md"]:
        session.call("file_read", {"path": path})
    session.call("get_cards", {"customer_id": OWNER})
    reads = [
        ("get_tariff", {"customer_id": OWNER}),
        ("get_limits", {"card_id": TARGET}),
        ("get_limit_request", {"customer_id": OWNER}),
    ]
    if route == "independent_reverse":
        reads.reverse()
    delayed = []
    for name, args in reads:
        if route == "missing_request" and name == "get_limit_request":
            continue
        if (route == "request_received_late" and name == "get_limit_request") or (
            route == "tariff_received_late" and name == "get_tariff"
        ):
            attempt = session.begin_call(name, args, None)
            delayed.append((name, args, attempt))
            continue
        session.call(name, args)
        if route == "repeat_reads":
            session.call(name, args)
    if route == "wrong_card_repaired":
        session.call("set_limit", {**correct, "card_id": "card_9200a"})
    if route == "unknown_card_repaired":
        session.call("set_limit", {**correct, "card_id": "card_missing"})
    if route == "over_tariff_repaired":
        session.call("set_limit", {**correct, "amount": 600000})
    if route == "cash_repaired":
        session.call(
            "set_limit",
            {**correct, "limit_type": "daily_cash_withdrawal", "amount": 100000},
        )
    if route in {"future_300000", "maximum_500000"}:
        correct["amount"] = 300000 if route == "future_300000" else 500000
    session.call("set_limit", correct)
    for name, args, attempt in delayed:
        session.call(name, args, attempt=attempt)
        session.result_sent(attempt)
    return session


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize("route,expected", ROUTES, ids=[r[0] for r in ROUTES])
def test_bank60_scoped_pending_and_tariff_routes(route, expected, mode):
    session = _session(route)
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if dest := os.environ.get("BANK60_HARDENING_EVIDENCE"):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": "Bank 60",
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": reward.model_dump(mode="json"),
                    "errors": session.errors,
                    "mcp_events": session.mcp_events,
                    "messages": [
                        m.model_dump(mode="json") for m in session.trajectory()
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info
    if route == "matching_pending":
        assert reward.reward_breakdown[RewardType.DB] == 1
        assert reward.reward_breakdown[RewardType.ACTION] == 1
        assert reward.reward_breakdown[RewardType.ENV_ASSERTION] == 0
    if expected:
        limits = session.env.tools.db.card_limits
        assert limits[TARGET].sbp_limit == 400000
        assert limits[TARGET].daily_cash_withdrawal == 80000
        assert limits[TARGET].internet_operations_enabled
        assert limits["card_9200a"].sbp_limit == 200000


def test_bank60_tariff_receipt_discloses_current_and_future_applicability():
    """Future ceiling cannot erase the applicable August revision."""
    session = _session("reference")
    tariff = _payload(session.call("get_tariff", {"customer_id": OWNER}))
    assert tariff["max_sbp_limit"] == 500000
    assert tariff["revision_id"] == "premium-2026-08-28"
    assert tariff["effective_from"] == "2026-08-28"
    assert tariff["effective_to"] == "2026-08-31"
    assert tariff["scheduled_revisions"] == [
        {
            "revision_id": "premium-2026-09-01",
            "effective_from": "2026-09-01",
            "effective_to": None,
            "max_sbp_limit": 300000.0,
        }
    ]
    requests = _payload(session.call("get_limit_request", {"customer_id": OWNER}))
    assert len(requests) == 3
    assert all(r["requires_decision"] for r in requests)
    assert not any(
        r["status"] == "pending"
        and r["object_id"] == TARGET
        and r["channel"] == "sbp"
        and r["period"] == "per_operation"
        for r in requests
    )
