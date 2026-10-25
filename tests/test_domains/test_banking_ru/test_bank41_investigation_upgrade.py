"""Addressed Bank41 investigation contract; no full catalog matrix."""

import json
import os
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

TARGET = "bank41:investigation"
CUSTOMER = "egorova_n_3358"
ROUTES = [
    ("reference", 1),
    ("reference_repeat", 1),
    ("date_from_ticket", 1),
    ("correct_without_calendar", 1),
    ("noop", 0),
    ("independent_order", 1),
    ("details", 1),
    ("bundle", 1),
    ("missing_second", 0),
    ("full_payment", 0),
    ("partial_excluded", 0),
    ("fully_compensated", 0),
    ("processing_refund", 0),
    ("group_cap", 0),
    ("skip_refund", 0),
    ("skip_payout", 0),
    ("wrong_candidate", 0),
    ("early_sources", 0),
    ("manual_grant", 0),
    ("duplicate_changed", 0),
    ("duplicate_exact", 0),
    ("before_due", 0),
    ("refund_status_changed", 0),
    ("refund_status_changed_correct", 1),
]


def prepared_task(route):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 41"))
    actions = deepcopy(task.evaluation_criteria.actions)
    target = next(a for a in actions if a.action_id == TARGET)
    if route == "correct_without_calendar":
        actions = [
            a
            for a in actions
            if not (
                a.name == "file_read"
                and a.arguments.get("path") == "/knowledge/processing_calendar.md"
            )
        ]
    if route == "date_from_ticket":
        actions = [a for a in actions if a.name != "get_bank_date"]
    if route == "independent_order":
        docs = [a for a in actions if a.name == "file_read"]
        actions = [a for a in actions if a.name != "file_read"]
        actions[0:0] = list(reversed(docs))
        target.arguments["items"].reverse()
    if route == "bundle":
        actions = [
            a
            for a in actions
            if not (
                a.name == "file_read"
                and "/knowledge/kb_3" in a.arguments.get("path", "")
            )
        ]
        read = deepcopy(next(a for a in actions if a.name == "file_read"))
        read.required_for_reward = False
        folder = (
            Path(__file__).resolve().parents[3]
            / "data/tau2/domains/banking_ru/corpus/knowledge"
        )
        for index, path in enumerate(sorted(folder.glob("kb_final_41_part_*.md"))):
            item = deepcopy(read)
            item.action_id = "bundle_full:" + str(index)
            item.arguments = {"path": "/knowledge/" + path.name}
            actions.insert(index, item)
    if route == "details":
        index = next(i for i, a in enumerate(actions) if a.name == "get_transactions")
        template = actions.pop(index)
        txn_ids = {
            e.record_id
            for e in task.evaluation_criteria.result_evidence
            if "get_transactions" in e.tool_names
        }
        for txn_id in sorted(txn_ids):
            item = deepcopy(template)
            item.name = "get_transaction_details"
            item.action_id = "detail:" + txn_id
            item.arguments = {"transaction_id": txn_id}
            actions.insert(index, item)
            index += 1
    changes = {
        "missing_second": (70, [{"transaction_id": "txn_077031", "amount": 70}]),
        "full_payment": (
            228,
            [
                {"transaction_id": "txn_077031", "amount": 120},
                {"transaction_id": "txn_077040", "amount": 108},
            ],
        ),
        "partial_excluded": (108, [{"transaction_id": "txn_077040", "amount": 108}]),
        "fully_compensated": (
            286,
            [
                {"transaction_id": "txn_077031", "amount": 70},
                {"transaction_id": "txn_077040", "amount": 108},
                {"transaction_id": "txn_077041", "amount": 108},
            ],
        ),
        "processing_refund": (
            172,
            [
                {"transaction_id": "txn_077031", "amount": 64},
                {"transaction_id": "txn_077040", "amount": 108},
            ],
        ),
        "group_cap": (
            80,
            [
                {"transaction_id": "txn_077031", "amount": 30},
                {"transaction_id": "txn_077040", "amount": 50},
            ],
        ),
        "skip_refund": (
            188,
            [
                {"transaction_id": "txn_077031", "amount": 80},
                {"transaction_id": "txn_077040", "amount": 108},
            ],
        ),
        "skip_payout": (
            228,
            [
                {"transaction_id": "txn_077031", "amount": 120},
                {"transaction_id": "txn_077040", "amount": 108},
            ],
        ),
        "wrong_candidate": (
            178,
            [
                {"transaction_id": "txn_077032", "amount": 70},
                {"transaction_id": "txn_077040", "amount": 108},
            ],
        ),
    }
    if route in changes:
        target.arguments["amount"], target.arguments["items"] = changes[route]
    if route == "early_sources":
        actions.remove(target)
        actions.insert(
            next(i for i, a in enumerate(actions) if a.name == "verify_identity") + 1,
            target,
        )
    if route in [
        "before_due",
        "refund_status_changed",
        "refund_status_changed_correct",
    ]:
        data = task.initial_state.initialization_data.agent_data
        if route == "before_due":
            data["today"] = "2026-08-31"
        else:
            data["transactions"] = {"txn_077042": {"status": "processing"}}
        if route == "refund_status_changed_correct":
            target.arguments["amount"] = 188
            target.arguments["items"][0]["amount"] = 80
            canonical = next(
                a for a in task.evaluation_criteria.actions if a.action_id == TARGET
            )
            canonical.arguments = deepcopy(target.arguments)
            for assertion in task.evaluation_criteria.env_assertions:
                if assertion.func_name in [
                    "assert_cashback_investigation",
                    "assert_cashback_investigation_attempts",
                ]:
                    assertion.arguments["amount"] = 188
                    assertion.arguments["items"] = deepcopy(target.arguments["items"])
            for evidence in task.evaluation_criteria.result_evidence:
                if evidence.record_id == "txn_077042":
                    evidence.expected_fields["status"] = "processing"
    return task, actions


@pytest.mark.parametrize("route,expected", ROUTES, ids=[r for r, _ in ROUTES])
def test_bank41_investigation_upgrade(route, expected):
    task, actions = prepared_task(route)
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    if route != "noop":
        session.replay_actions(actions)
    if route == "manual_grant":
        session.call("grant_cashback", {"transaction_id": "txn_077040", "amount": 108})
    if route == "duplicate_exact":
        target = next(
            a for a in task.evaluation_criteria.actions if a.action_id == TARGET
        )
        session.call("create_cashback_investigation", deepcopy(target.arguments))
    if route == "duplicate_changed":
        session.call(
            "create_cashback_investigation",
            {
                "customer_id": CUSTOMER,
                "program": "N40",
                "amount": 70,
                "items": [{"transaction_id": "txn_077031", "amount": 70}],
            },
        )
    session.call("done", {})
    result = grade_full_session(session)
    if dest := os.environ.get("BANK41_EVIDENCE"):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        payload = {
            "route": route,
            "expected": expected,
            "reward": result.reward_info.model_dump(mode="json"),
            "errors": session.errors,
            "invocation": result.invocation,
            "calls": [
                {"call": c.model_dump(mode="json"), "result": r.model_dump(mode="json")}
                for c, r in session.log
            ],
        }
        (path / f"{route}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2).replace(
                "мускат", "[MASKED]"
            )
        )
    assert result.reward_info.final_reward == expected, result.reward_info.info


def test_bank41_fixture_matches_authoritative_reward_linkage():
    """Published owner/account/card condition must hold before intended 178 is valid."""
    data = Path(__file__).resolve().parents[3] / "data/tau2/domains/banking_ru"
    db = json.loads((data / "db.json").read_text())
    source = (data / "corpus/knowledge/kb_reward_reconciliation.md").read_text()
    assert "совпадающие по владельцу" in source
    assert "счёту и карте" in source
    transactions = db["transactions"]
    for ledger_id, purchase_id in {
        "txn_077021": "txn_077020",
        "txn_077044": "txn_077039",
        "txn_077042": "txn_077031",
        "txn_077043": "txn_077031",
        "txn_077045": "txn_077031",
        "txn_077046": "txn_077041",
    }.items():
        ledger, purchase = transactions[ledger_id], transactions[purchase_id]
        assert ledger["original_transaction_id"] == purchase["id"]
        for selector in ["customer_id", "account_id", "card_id"]:
            assert ledger[selector] == purchase[selector], (ledger_id, selector)

    # Independently apply the full source filters and Decimal calculation.
    unpaid = {}
    base_rate = next(
        category["rate"]
        for category in db["cashback_rules"][CUSTOMER]["categories"]
        if "5812" in category["mcc_codes"]
    )
    assert db["customers"][CUSTOMER]["tariff_id"] == "tariff_classic"
    rate = Decimal(str(base_rate)) + Decimal("0.01")
    for purchase_id, purchase in transactions.items():
        if not (
            purchase["customer_id"] == CUSTOMER
            and "2026-08-18" <= purchase["date"] <= "2026-08-22"
            and purchase["status"] == "posted"
            and purchase["kind"] == "purchase"
            and purchase["mcc"] == "5812"
            and purchase["channel"] == "pos"
            and purchase["country"] == "RU"
            and purchase["amount"] >= 1000
        ):
            continue
        related = [
            row
            for row in transactions.values()
            if row.get("original_transaction_id") == purchase_id
            and row["status"] == "posted"
            and all(
                row[field] == purchase[field]
                for field in ["customer_id", "account_id", "card_id"]
            )
        ]
        returned = sum(
            (Decimal(str(row["amount"])) for row in related if row["kind"] == "refund"),
            Decimal(0),
        )
        paid = sum(
            (
                Decimal(str(row["amount"]))
                for row in related
                if row["kind"] == "cashback"
            ),
            Decimal(0),
        )
        remainder = max(
            Decimal(0),
            min(
                max(Decimal(0), Decimal(str(purchase["amount"])) - returned) * rate,
                Decimal(130),
            )
            - paid,
        ).quantize(Decimal("0.01"))
        if remainder:
            unpaid[purchase_id] = remainder
    assert unpaid == {"txn_077031": Decimal(70), "txn_077040": Decimal(108)}
    assert sum(unpaid.values()) == Decimal(178)


# Explicitly retain the historical scenario version; composite tests live separately.
pytestmark = pytest.mark.usefixtures("fees_rewards_parent_scenarios")
