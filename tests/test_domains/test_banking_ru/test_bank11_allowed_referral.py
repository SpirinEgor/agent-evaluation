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
