import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def task():
    return next(t for t in get_tasks() if t.id == "Bank 3")


def test_bank3_combined_scenario():
    t = task()
    s = PlatformSession(t, get_environment)
    old = s.env.tools.db.transactions["txn_665040"]
    pending = s.env.tools.db.limit_requests["lrq_6650"]
    assert old.merchant == "Ozon" and old.amount == 3700
    assert old.date == "2026-08-27" and old.order_id == "OZ-5583-0827"
    assert pending.channel == "cash" and pending.object_id == "card_5583"
    assert pending.requires_decision and pending.status == "pending"
    assert "Интернет-операции запрещены" not in t.ticket
    assert "OZ-5583-0828" in t.ticket


CASES = [
    ("reference", 1),
    ("repeat", 1),
    ("noop", 0),
    ("reordered_reads", 1),
    ("direct_history", 1),
    ("extra_own_read", 1),
    ("missing_history", 0),
    ("wrong_history_window", 0),
    ("missing_pending", 0),
    ("late_history", 0),
    ("late_pending", 0),
    ("missing_policy", 0),
    ("wrong_card", 0),
    ("cash_change", 0),
    ("old_unblock_failed", 0),
    ("old_refund_failed", 0),
    ("early_private_read", 0),
    ("identity_skipped", 0),
    ("foreign_read", 0),
    ("missing_done", 0),
]


@pytest.mark.parametrize("case,expected", CASES, ids=[c[0] for c in CASES])
def test_bank3_combined_gate(case, expected, tmp_path):
    t = task()
    s = PlatformSession(t, get_environment, max_calls=60)
    actions = deepcopy(t.evaluation_criteria.actions)
    if case == "noop":
        actions = []
    if case == "reordered_reads":
        actions[:3] = list(reversed(actions[:3]))
    if case == "direct_history":
        a = next(a for a in actions if a.action_id == "bank3_history")
        a.name = "get_transaction_details"
        a.arguments = {"transaction_id": "txn_665040"}
    if case in ["missing_history", "missing_pending"]:
        target = "bank3_history" if case == "missing_history" else "bank3_pending"
        actions = [a for a in actions if a.action_id != target]
    if case == "wrong_history_window":
        next(a for a in actions if a.action_id == "bank3_history").arguments[
            "date_from"
        ] = "2026-08-28"
    if case in ["late_history", "late_pending"]:
        target = "bank3_history" if case == "late_history" else "bank3_pending"
        a = next(a for a in actions if a.action_id == target)
        actions.remove(a)
        actions.append(a)
    if case == "missing_policy":
        actions = [
            a for a in actions if a.arguments.get("path") != "/policies/cards.md"
        ]
    if case == "wrong_card":
        next(a for a in actions if a.name == "set_limit").arguments["card_id"] = (
            "card_9917"
        )
    if case == "cash_change":
        next(a for a in actions if a.name == "set_limit").arguments = {
            "card_id": "card_5583",
            "limit_type": "daily_cash_withdrawal",
            "amount": 150000,
        }
    if case == "early_private_read":
        s.call("get_cards", {"customer_id": "fedorova_m_6650"})
    if case == "identity_skipped":
        actions = [
            a for a in actions if a.name not in ["verify_identity", "ask_client"]
        ]
    for a in actions:
        s.call(a.name, dict(a.arguments))
        if a.name == "verify_identity":
            if case == "extra_own_read":
                s.call("get_customer_profile", {"customer_id": "fedorova_m_6650"})
            if case == "foreign_read":
                s.call("get_cards", {"customer_id": "belova_n_2201"})
            if case == "old_unblock_failed":
                s.call(
                    "unblock_operation",
                    {
                        "transaction_id": "txn_665040",
                        "confirmation_id": "not-a-receipt",
                    },
                )
            if case == "old_refund_failed":
                s.call(
                    "refund_transaction",
                    {"transaction_id": "txn_665040", "amount": 3700},
                )
    if case != "missing_done":
        s.call("done", {})
    half = s.grade()
    full = None if case == "missing_done" else grade_full_session(s).reward_info
    out = Path(os.environ.get("BANK3_EVIDENCE_DIR", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    (out / (case + ".json")).write_text(
        json.dumps(
            {
                "expected": expected,
                "half": half.model_dump(mode="json"),
                "full": full.model_dump(mode="json") if full else None,
                "simulation": s.to_simulation_run().model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    assert half.final_reward == expected
    if full:
        assert full.final_reward == expected
    if expected == 1:
        assert s.errors == 0
