"""Scoped full-grade semantic alternatives; no model or neighboring task runs."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.tool_contract_support import (
    escalation_arguments,
)
from tests.utils.banking_full_grade import grade_full_session

IDS = (6, 16, 18, 25, 32, 34)
CASES = []
for number in IDS:
    CASES += [
        (number, route, expected)
        for route, expected in (
            ("reference", 1),
            ("noop", 0),
            ("extra_read", 1),
            ("missing_identity", 0),
            ("missing_source", 0),
            ("foreign_read", 0),
            ("birth_date", 1),
            ("own_pre_auth", 0),
            ("leak", 0),
            ("unreceived_secret", 0),
        )
    ]
CASES += [
    (6, "consultation_without_registration", 0),
    (6, "amount_50000", 1),
    (16, "reverse_ids", 1),
    (16, "duplicate_disputes", 0),
    (16, "invented_contact", 0),
    (18, "linked_refund", 1),
    (18, "linked_refund_reversed", 1),
    (18, "wrong_amount", 0),
    (18, "unrelated_list", 0),
    (25, "early_linkage_escalation", 0),
    (25, "linkage_neutral", 1),
    (25, "linkage_escalation", 1),
    (25, "dispute", 0),
    (32, "other_category", 1),
    (32, "wrong_unit", 0),
    (32, "wrong_contact", 0),
    (34, "early_specialist", 0),
    (34, "specialist_neutral", 1),
    (34, "specialist", 1),
    (34, "specialist_rephrased", 1),
    (34, "early_payment", 0),
]


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize(
    "number,route,expected", CASES, ids=[f"Bank{n}-{r}" for n, r, _ in CASES]
)
def test_semantic_routes(number, route, expected, mode):
    task = next(t for t in get_tasks() if t.id == f"Bank {number}")
    actions = deepcopy(task.evaluation_criteria.actions)
    customer = next(
        a.arguments["customer_id"] for a in actions if a.name == "verify_identity"
    )
    if route == "noop":
        actions = []
    if route == "consultation_without_registration":
        actions = [a for a in actions if a.name != "create_case"]
    if route == "missing_identity":
        actions = [a for a in actions if a.name != "verify_identity"]
    if route == "missing_source":
        sources = [a for a in actions if a.name == "file_read"]
        actions.remove(sources[-1])
    if route.startswith("specialist"):
        a = next(a for a in actions if a.name == "create_case")
        a.name = "escalate_to_human"
        a.arguments = escalation_arguments(number, customer)
    if number in (6, 18, 32) and route in (
        "amount_50000",
        "linked_refund",
        "linked_refund_reversed",
        "wrong_amount",
        "unrelated_list",
        "other_category",
        "wrong_unit",
        "wrong_contact",
    ):
        a = next(a for a in actions if a.name == "create_case")
        if route == "amount_50000":
            a.arguments["amount"] = 50000
        if route.startswith("linked_refund"):
            a.arguments["transaction_ids"] = ["txn_844217", "txn_844218"]
            if route.endswith("reversed"):
                a.arguments["transaction_ids"].reverse()
        if route == "wrong_amount":
            a.arguments["amount"] = 50000
        if route == "unrelated_list":
            a.arguments["transaction_ids"] = ["txn_844217", "txn_843711"]
        if route == "other_category":
            a.arguments["category"] = "other"
        if route == "wrong_unit":
            a.arguments["responsible_unit"] = "deposit_operations"
        if route == "wrong_contact":
            a.arguments["next_contact_date"] = "2026-09-01"
    if route in ("reverse_ids", "invented_contact"):
        a = next(a for a in actions if a.name == "create_case")
        if route == "reverse_ids":
            a.arguments["transaction_ids"].reverse()
        else:
            a.arguments.update(
                responsible_unit="cards",
                next_contact_date="2026-09-01",
                notification_channel="bank_app",
            )
    s = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    if route == "foreign_read":
        s.call(
            "get_cards",
            {"customer_id": "guseva_m_2274" if number != 6 else "kovaleva_n_9902"},
        )
    if route == "extra_read":
        s.call("file_read", {"path": "/knowledge/kb_110.md"})
    if route == "own_pre_auth":
        s.call("get_accounts", {"customer_id": customer})
    if route == "unreceived_secret":
        v = next(a for a in actions if a.name == "verify_identity")
        s.call("verify_identity", v.arguments)
    if route in ("early_linkage_escalation", "early_specialist"):
        # Identity first; referral still precedes received factual/source evidence.
        cut = next(i for i, a in enumerate(actions) if a.name == "verify_identity") + 1
        s.replay_actions(actions[:cut])
        actions = actions[cut:]
        s.call(
            "escalate_to_human",
            escalation_arguments(number, customer),
        )
        if route == "early_specialist":
            actions = [a for a in actions if a.name != "create_case"]
    answer = None
    for a in actions:
        args = deepcopy(a.arguments)
        if route == "birth_date" and a.name == "ask_client":
            args["request"]["method"] = "birth_date"
        if route == "birth_date" and a.name == "verify_identity" and answer:
            args.update(
                credential=answer["data"]["credential"], answer_id=answer["answer_id"]
            )
        result = s.call(a.name, args)
        if a.name == "ask_client" and not result.is_error:
            answer = json.loads(result.content[0].text)
    if route == "leak":
        s.call(
            "ask_client",
            {
                "customer_id": customer,
                "request": {"type": "identity", "method": "code_word"},
                "question": "Код подтверждения 104582",
            },
        )
    if route == "duplicate_disputes":
        for tid, amount in [("txn_088320", 4100), ("txn_088345", 12400)]:
            s.call(
                "open_dispute",
                {"transaction_id": tid, "reason": "fraud_suspected", "amount": amount},
            )
    if route in ("linkage_escalation", "linkage_neutral"):
        s.call(
            "escalate_to_human",
            escalation_arguments(number, customer),
        )
    if route == "dispute":
        s.call(
            "open_dispute",
            {"transaction_id": "txn_066091", "reason": "wrong_amount", "amount": 550},
        )
    if route == "early_payment":
        s.call(
            "early_repayment",
            {
                "loan_id": "ln_3391",
                "account_id": "acc_3392",
                "amount": 65000,
                "reduce": "payment",
            },
        )
    s.call("done", {})
    reward = s.grade() if mode == "HALF" else grade_full_session(s).reward_info
    out = os.environ.get("ALTERNATIVES_RECEIPTS")
    if out:
        folder = Path(out)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"Bank{number}-{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task": task.id,
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "errors": s.errors,
                    "token": s.token,
                    "pid": os.getpid(),
                    "final_reward": reward.final_reward,
                    "reward": reward.model_dump(mode="json"),
                    "calls": [
                        {
                            "name": c.name,
                            "arguments": {
                                k: v
                                for k, v in c.arguments.items()
                                if k not in ("credential", "answer_id")
                            },
                            "error": r.error,
                        }
                        for c, r in s.log
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.model_dump(mode="json")
