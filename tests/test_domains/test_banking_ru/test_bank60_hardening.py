"""Bank60: scoped pending requests and date-applicable SBP tariff."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.data_model.tasks import InitializationData, InitialState, RewardType
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/banking_ru/harden_bank60.py"
OWNER = "fomin_r_9200"
TARGET = "card_9200b"


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


@pytest.mark.parametrize("mode", ["HALF"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("future_300000", 0),
        ("unknown_card_repaired", 1),
        ("missing_request", 0),
        ("matching_pending", 0),
    ],
)
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
