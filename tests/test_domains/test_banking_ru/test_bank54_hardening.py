"""Bank54: linked partial refunds and independent cancellation facts."""

import json
import os
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
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


@pytest.mark.parametrize("mode", ["HALF"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("case_list", 1),
        ("other_reason", 1),
        ("wrong_1700", 0),
        ("default_null_after", 0),
        ("repeat_cancel", 0),
        ("historical_only", 0),
        ("no_refund_receipts", 0),
        ("known_wrong_target_failed", 0),
    ],
)
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
