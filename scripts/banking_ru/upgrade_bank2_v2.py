"""Bank2-only posting-v2 migration and independent integer-money author oracle.

Apply only to a temporary data copy; publish reviewed outputs separately.
"""

import argparse
import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from upgrade_composite_rewards import action, assertion, evidence, items

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/tau2/domains/banking_ru"
VERSION = "2026-10-14-bank2-v2"
C = "gromov_a_1187"
A, B, D, BG = "txn_552290", "txn_552291", "txn_b2_similar", "txn_b2_background"
CASE = "case-standard-july"
PERIOD = "2026-07"
SOURCE = "/knowledge/kb_standard_cashback_v2.md"
TICKET = (
    "Сегодня 28 августа 2026 года. Здравствуйте! Я Андрей Громов, телефон +7 926 118-77-30. "
    "Недоплатили кешбэк за две покупки в «Кофемании»: первая — 25 июля, чек CF-2507-1187; "
    "вторая — около 26 июля на 1 400 ₽, чек сейчас не помню, но могу уточнить. "
    "По первой покупке я уже открывал обращение. Срок выплаты прошёл. "
    "Разрешаю исправить подтверждённую недоплату в пределах банковских полномочий "
    "и дополнить существующее расследование оставшейся суммой. Тариф менять не поручаю."
)


def kopecks(value):
    return int(Decimal(str(value)) * 100)


def independent_oracle(db, selected=B):
    """Author calculation: integer numerator/denominator, independent of standard_gaps."""
    cycle = db["cashback_rules"][C]["cycles"][0]
    txs = db["transactions"]
    eligible = sorted(
        (
            t
            for t in txs.values()
            if t["customer_id"] == C
            and t["kind"] == "purchase"
            and t["status"] == "posted"
            and t.get("reward_program") == cycle["program"]
            and t.get("card_id") in cycle["card_ids"]
            and cycle["posting_from"]
            <= (t.get("posted_at") or "")[:10]
            <= cycle["posting_to"]
        ),
        key=lambda t: (t["posted_at"], t["id"]),
    )
    remaining = kopecks(cycle["limit"])
    rows = {}
    for t in eligible:
        linked = [
            x
            for x in txs.values()
            if x.get("original_transaction_id") == t["id"]
            and x["status"] == "posted"
            and (x.get("posted_at") or x["date"])[:10] <= cycle["settlement_as_of"]
            and all(
                x.get(f) == t.get(f) for f in ["customer_id", "account_id", "card_id"]
            )
        ]
        net = max(
            0,
            kopecks(t["amount"])
            - sum(kopecks(x["amount"]) for x in linked if x["kind"] == "refund"),
        )
        history = next(
            h
            for h in db["cashback_rules"][C]["categories_history"]
            if h["effective_from"] <= t["date"]
            and (not h["effective_to"] or t["date"] <= h["effective_to"])
        )
        rate = next(
            (
                Decimal(str(cat["rate"]))
                for cat in history["categories"]
                if t.get("mcc") in cat["mcc_codes"]
            ),
            Decimal(0),
        )
        # Fraction arithmetic with a positive half-up integer rounding, not production Decimal multiplication.
        numerator, denominator = rate.as_integer_ratio()
        before = (2 * net * numerator + denominator) // (2 * denominator)
        entitlement = min(before, remaining)
        remaining -= entitlement
        paid = sum(kopecks(x["amount"]) for x in linked if x["kind"] == "cashback")
        rows[t["id"]] = {
            "net": net,
            "before_cap": before,
            "after_cap": entitlement,
            "paid": paid,
            "gap": max(0, entitlement - paid),
        }
    gapA, gapB = rows[A]["gap"], rows[selected]["gap"]
    permission = db["cashback_adjustment_permissions"].get("adjustment-A")
    prior = db["cases"][CASE]
    claimedA = sum(
        kopecks(x["amount"]) for x in prior["reward_items"] if x["transaction_id"] == A
    )
    prior_total = sum(kopecks(x["amount"]) for x in prior["reward_items"])
    authorized = bool(
        permission
        and permission["status"] == "approved"
        and permission["customer_id"] == C
        and permission["transaction_id"] == A
        and permission["program"] == "standard_cashback"
        and permission["period"] == PERIOD
        and permission["valid_from"] <= db["today"] <= permission["valid_to"]
        and not permission["used"]
    )
    payout = (
        min(
            kopecks(permission["max_amount"]),
            kopecks(permission["remaining_amount"]),
            max(0, gapA - claimedA),
        )
        if authorized
        else 0
    )
    return {
        "rows": rows,
        "payout": payout,
        "claim_items": items(
            (A, (gapA - payout) / 100), *([(selected, gapB / 100)] if gapB else [])
        ),
        "claim": (gapA + gapB - payout) / 100,
        "increase": (gapA + gapB - payout - prior_total) / 100,
    }


def initialize(db, answers):
    txs = db["transactions"]
    base = deepcopy(txs[A])

    def tx(ident, **values):
        row = {
            **base,
            "id": ident,
            "order_id": None,
            "original_transaction_id": None,
            "posted_at": None,
            "cashback_granted": False,
            **values,
        }
        txs[ident] = row

    txs[A].update(order_id="CF-2507-1187", reward_program="standard_cashback")
    txs[B].update(order_id="CF-2607-1188", reward_program="standard_cashback")
    tx(
        BG,
        date="2026-06-30",
        posted_at="2026-07-01T09:00:00+03:00",
        amount=300,
        reward_program="standard_cashback",
        order_id="BG-0630",
    )
    tx(
        "txn_b2_background_paid",
        date="2026-08-05",
        amount=30,
        kind="cashback",
        original_transaction_id=BG,
        reward_program="standard_cashback",
    )
    tx(
        D,
        date="2026-07-26",
        posted_at="2026-07-27T12:00:00+03:00",
        amount=1400,
        reward_program="standard_cashback",
        order_id="CF-2607-1199",
    )
    tx(
        "txn_b2_next_cycle",
        date="2026-07-31",
        posted_at="2026-08-01T09:00:00+03:00",
        amount=800,
        reward_program="standard_cashback",
        order_id="NEXT-0731",
    )
    tx(
        "txn_b2_other_program",
        date="2026-07-22",
        posted_at="2026-07-22T09:00:00+03:00",
        amount=900,
        reward_program="N40",
        order_id="N40-0722",
    )
    tx(
        "txn_b2_processing",
        date="2026-07-23",
        status="processing",
        amount=1000,
        reward_program="standard_cashback",
        order_id="WAIT-0723",
    )
    rules = db["cashback_rules"][C]
    rules.update(
        monthly_cap=115,
        cycles=[
            {
                "id": "standard-july-register",
                "rules_revision": "posting-v2",
                "period": PERIOD,
                "program": "standard_cashback",
                "limit": 115,
                "card_ids": ["card_1187"],
                "posting_from": "2026-07-01",
                "posting_to": "2026-07-31",
                "settlement_as_of": db["today"],
            }
        ],
    )
    rules["categories_history"] = [
        {
            "id": "cafe-rate-" + month,
            "program": "standard_cashback",
            "effective_from": start,
            "effective_to": end,
            "categories": [
                {
                    "name": "Рестораны и кафе",
                    "rate": rate,
                    "mcc_codes": ["5812", "5813", "5814"],
                }
            ],
        }
        for month, start, end, rate in [
            ("june", "2026-06-01", "2026-06-30", 0.10),
            ("july", "2026-07-01", "2026-07-31", 0.05),
            ("august", "2026-08-01", None, 0.01),
        ]
    ]
    db["cashback_adjustment_permissions"]["adjustment-A"].update(
        max_amount=12, remaining_amount=12, used_amount=0, used=False
    )
    initial = {
        "id": CASE,
        "customer_id": C,
        "category": "cashback_investigation",
        "reward_program": "standard_cashback",
        "reward_period": PERIOD,
        "status": "open",
        "created_at": "2026-08-20",
        "responsible_unit": "rewards_operations",
        "amount": 10,
        "transaction_id": A,
        "transaction_ids": [A],
        "reward_items": items((A, 10)),
        "version": 1,
    }
    db["cases"][CASE] = initial
    other = deepcopy(initial)
    other.update(
        id="case-standard-other-period",
        reward_period="2026-08",
        amount=5,
        transaction_id="txn_b2_next_cycle",
        transaction_ids=["txn_b2_next_cycle"],
        reward_items=items(("txn_b2_next_cycle", 5)),
    )
    db["cases"][other["id"]] = other
    answers[C] = [a for a in answers[C] if not a.get("transaction_selection")] + [
        {
            "keywords": [],
            "answer": "Чек CF-2507-1187, 25 июля.",
            "transaction_selection": {
                "id": "customer-first-purchase",
                "merchant": "Кофемания",
                "amount": 1200,
                "date": "2026-07-25",
                "receipt": "CF-2507-1187",
            },
        },
        {
            "keywords": [],
            "answer": "Чек CF-2607-1188, 26 июля.",
            "transaction_selection": {
                "id": "customer-purchase-selection",
                "merchant": "Кофемания",
                "amount": 1400,
                "date": "2026-07-26",
                "receipt": "CF-2607-1188",
            },
        },
    ]


def configure(task, db, answers, data):
    """Rebuild only Bank2's subject contract; shared builders are run separately."""
    from tau2.domains.banking_ru.data_model import BankingDB
    from tau2.domains.banking_ru.reward_workflows import rules_with_register

    assert task["id"] == "Bank 2"
    selected = (
        D
        if next(
            a
            for a in answers[C]
            if a.get("transaction_selection", {}).get("id")
            == "customer-purchase-selection"
        )["transaction_selection"]["receipt"]
        == "CF-2607-1199"
        else B
    )
    calc = independent_oracle(db, selected)
    mixed_request = "2026-10-14-bank2-v2-mixed-request" in task["description"].get(
        "notes", ""
    )
    if mixed_request:
        from strengthen_bank2_mixed_request import TICKET as mixed_ticket
    ticket = (mixed_ticket if mixed_request else TICKET) + (
        " Прикладываю чек второй покупки и заметку прежнего сотрудника; используйте реквизиты чека."
        if "customer-receipt-b2" in db.get("documents", {})
        else ""
    )
    task.update(scenario_version=VERSION, ticket=ticket)
    task["user_scenario"]["instructions"].update(
        reason_for_call=ticket, known_info=ticket
    )
    task["description"].update(
        purpose="Выбрать покупку, восстановить полный цикл и дополнить существующее частичное расследование.",
        relevant_policies="/policies/identity.md; /knowledge/kb_standard_cashback_v2.md (полный эквивалент включён в kb_cashback_adjustment_authority.md)",
        notes=(
            "2026-10-14-bank2-v2-mixed-request; completion_only; frozen before model measurement."
            if mixed_request
            else VERSION + "; completion_only; no new model measurements."
        ),
    )
    e = task["evaluation_criteria"]
    old = e["actions"]
    e["actions"] = [
        a
        for a in old
        if a["name"] in ["find_customer", "verify_identity"]
        or a["name"] == "ask_client"
        and a["arguments"].get("request", {}).get("type") == "identity"
    ]
    e["actions"] += [
        action("bank2:v2:source", "file_read", {"path": SOURCE}),
        action("bank2:v2:rules", "get_cashback_rules", {"customer_id": C}),
        action(
            "bank2:v2:transactions", "get_transactions", {"customer_id": C, "limit": 50}
        ),
        action(
            "bank2:v2:permissions",
            "get_cashback_adjustment_permissions",
            {"customer_id": C},
        ),
        action("bank2:v2:cases", "get_cashback_investigations", {"customer_id": C}),
        action(
            "bank2:v2:selection",
            "ask_client",
            {
                "customer_id": C,
                "request": {
                    "type": "transaction_selection",
                    "merchant": "Кофемания",
                    "amount": 1400,
                },
            },
        ),
    ]
    payout = (
        action(
            "bank2:adjust",
            "execute_cashback_adjustment",
            {
                "permission_id": "adjustment-A",
                "transaction_id": A,
                "amount": calc["payout"] / 100,
                "idempotency_key": "bank2-v2-adjust",
            },
            True,
            ["permission_id", "transaction_id"],
        )
        if calc["payout"]
        else None
    )
    claim = action(
        "bank2:v2:amend",
        "amend_cashback_investigation",
        {"case_id": CASE, "items": calc["claim_items"]},
        True,
        ["case_id"],
    )
    count = sum(t["customer_id"] == C for t in db["transactions"].values())
    index = (
        next(i for i, a in enumerate(e["actions"]) if a["name"] == "get_transactions")
        + 1
    )
    for offset in range(50, count, 50):
        e["actions"].insert(
            index,
            action(
                "bank2:v2:transactions-page:" + str(offset),
                "get_transactions",
                {"customer_id": C, "limit": 50, "offset": offset},
            ),
        )
        index += 1
    if payout:
        e["actions"].append(payout)
    e["actions"].append(claim)
    e["action_order"] = [
        x
        for x in e["action_order"]
        if not x["before_action_id"].startswith("bank2:")
        and x["before_action_id"] in {a["action_id"] for a in e["actions"]}
        and x["after_action_id"] in {a["action_id"] for a in e["actions"]}
    ]
    if payout:
        e["action_order"].append(
            {
                "before_action_id": payout["action_id"],
                "after_action_id": claim["action_id"],
                "require_result_before_target": True,
                "target_argument_options": {"reserved_adjustments": [None, []]},
                "target_item_ids": [A],
            }
        )
    e["result_evidence"] = [
        x
        for x in e["result_evidence"]
        if x["evidence_id"].startswith("common_discovery_")
    ]
    registry = rules_with_register(BankingDB.model_validate(db), C).cycles[0]
    targets = [claim] + ([payout] if payout else [])
    for target in targets:
        ident = target["action_id"]
        e["result_evidence"].append(
            evidence(
                "bank2:v2:source:" + ident,
                ["file_read"],
                "kb_standard_cashback_v2",
                {
                    "complete": True,
                    "sha256": hashlib.sha256(
                        (data / "corpus" / SOURCE.lstrip("/")).read_bytes()
                    ).hexdigest(),
                },
                ident,
            )
        )
        e["result_evidence"].append(
            evidence(
                "bank2:v2:registry:" + ident,
                ["get_cashback_rules"],
                registry["id"],
                {
                    k: registry[k]
                    for k in [
                        "rules_revision",
                        "period",
                        "program",
                        "limit",
                        "card_ids",
                        "posting_from",
                        "posting_to",
                        "settlement_as_of",
                        "transaction_ids",
                        "related_posted_ids",
                        "registry_complete",
                    ]
                },
                ident,
            )
        )
        needed = [
            t
            for t in registry["transaction_ids"]
            if (db["transactions"][t]["posted_at"], t)
            <= (
                db["transactions"][A if target == payout else selected]["posted_at"],
                A if target == payout else selected,
            )
        ]

        def order(tid):
            return db["transactions"][tid]["posted_at"], tid

        outcomes = [A] if target == payout else [A, selected]
        for tid in needed:
            for rid in [tid, *registry["related_posted_ids"][tid]]:
                row = db["transactions"][rid]
                if row["kind"] == "cashback":
                    affected = [tid] if tid in outcomes else []
                else:
                    affected = [
                        outcome for outcome in outcomes if order(tid) <= order(outcome)
                    ]
                if not affected:
                    continue
                args = {}
                target_id = ident
                if target == claim:
                    # A zero-entitlement complaint has no write; still prove it before completion.
                    active = [
                        tid
                        for tid in affected
                        if tid == A or calc["rows"][tid]["gap"] > 0
                    ]
                    if active:
                        args = {
                            "target_item_ids": active,
                            "only_if_target_attempted": True,
                        }
                    else:
                        target_id = None
                fields = {
                    k: row.get(k)
                    for k in [
                        "customer_id",
                        "account_id",
                        "card_id",
                        "date",
                        "posted_at",
                        "amount",
                        "kind",
                        "status",
                        "original_transaction_id",
                        "mcc",
                        "order_id",
                        "reward_program",
                    ]
                }
                e["result_evidence"].append(
                    evidence(
                        "bank2:v2:fact:" + rid + ":" + ident,
                        ["get_transactions", "get_transaction_details"],
                        rid,
                        fields,
                        target_id,
                        **args,
                    )
                )
        history_ids = {
            next(
                h["id"]
                for h in db["cashback_rules"][C]["categories_history"]
                if h["effective_from"] <= db["transactions"][tid]["date"]
                and (
                    not h["effective_to"]
                    or db["transactions"][tid]["date"] <= h["effective_to"]
                )
            )
            for tid in needed
        }
        for hid in sorted(history_ids):
            h = next(
                h
                for h in db["cashback_rules"][C]["categories_history"]
                if h["id"] == hid
            )
            e["result_evidence"].append(
                evidence(
                    "bank2:v2:history:" + hid + ":" + ident,
                    ["get_cashback_rules"],
                    hid,
                    {
                        k: h[k]
                        for k in [
                            "program",
                            "effective_from",
                            "effective_to",
                            "categories",
                        ]
                    },
                    ident,
                )
            )
        e["result_evidence"].append(
            evidence(
                "bank2:v2:existing:" + ident,
                ["get_cashback_investigations", "get_documents"],
                CASE,
                {
                    "customer_id": C,
                    "reward_program": "standard_cashback",
                    "reward_period": PERIOD,
                    "status": "open",
                    "amount": 10,
                    "reward_items": items((A, 10)),
                },
                ident,
            )
        )
    if payout:
        p = db["cashback_adjustment_permissions"]["adjustment-A"]
        for target in targets:
            e["result_evidence"].append(
                evidence(
                    "bank2:v2:authority:" + target["action_id"],
                    ["get_cashback_adjustment_permissions"],
                    p["id"],
                    {
                        k: p[k]
                        for k in [
                            "customer_id",
                            "transaction_id",
                            "program",
                            "period",
                            "max_amount",
                            "status",
                            "valid_from",
                            "valid_to",
                            "used_amount",
                            "remaining_amount",
                            "used",
                        ]
                    },
                    target["action_id"],
                )
            )
        authority_evidence = next(
            x
            for x in e["result_evidence"]
            if x["evidence_id"] == "bank2:v2:authority:" + claim["action_id"]
        )
        authority_evidence.update(target_item_ids=[A], only_if_target_attempted=True)
    else:
        authority = next(
            a
            for a in e["actions"]
            if a["name"] == "get_cashback_adjustment_permissions"
        )
        authority["required_for_reward"] = True
        e["action_order"].append(
            {
                "before_action_id": authority["action_id"],
                "after_action_id": claim["action_id"],
                "require_result_before_target": True,
                "target_item_ids": [A],
            }
        )
    selection = next(
        a["transaction_selection"]
        for a in answers[C]
        if a.get("transaction_selection", {}).get("id") == "customer-purchase-selection"
    )
    e["result_evidence"].append(
        evidence(
            "bank2:v2:selection",
            ["ask_client", "get_documents", "get_document_status"],
            "customer-purchase-selection",
            {k: selection[k] for k in ["merchant", "amount", "date", "receipt"]},
            claim["action_id"] if calc["rows"][selected]["gap"] else None,
            **(
                {"target_item_ids": [selected], "only_if_target_attempted": True}
                if calc["rows"][selected]["gap"]
                else {}
            ),
        )
    )
    e["env_assertions"] = [
        assertion(
            "assert_standard_reward_attempts",
            {"customer_id": C, "case_id": CASE, "transaction_ids": [A, selected]},
        ),
        assertion(
            "assert_cashback_investigation",
            {
                "customer_id": C,
                "program": "standard_cashback",
                "period": PERIOD,
                "amount": calc["claim"],
                "items": calc["claim_items"],
            },
        ),
        assertion(
            "assert_no_excess_cashback_adjustment_attempts",
            {"permission_id": "adjustment-A"},
        ),
    ]
    e["db_alternative_action_replacements"] = []
    e["db_alternative_action_overrides"] = []
    e["db_alternative_action_additions"] = []
    e["forbidden_actions"] = [
        a
        for a in e["forbidden_actions"]
        if a["name"] not in ["ask_client", "execute_cashback_adjustment"]
    ]
    if not payout:
        e["forbidden_actions"].append(
            {
                "action_id": "bank2:v2:no-unapproved-payout",
                "name": "execute_cashback_adjustment",
                "arguments": {},
                "compare_args": [],
            }
        )
    from build_discovery_contract import with_contract as discovery
    from build_identity_contract import with_contract as identity

    identity([task])
    discovery([task], db)
    return calc


def migrate(data):
    tasks = json.loads((data / "tasks.json").read_text())
    db = json.loads((data / "db.json").read_text())
    answers = json.loads((data / "intake_answers.json").read_text())
    t = next(t for t in tasks if t["id"] == "Bank 2")
    if t.get("scenario_version") == VERSION:
        return tasks, db, answers
    if t.get("scenario_version") != "2026-10-14-composite-v1":
        raise ValueError("Unrecognized Bank2 parent version")
    initialize(db, answers)
    configure(t, db, answers, data)
    return tasks, db, answers


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=DATA)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--check", action="store_true")
    a = p.parse_args()
    if a.apply and a.data_dir.resolve() == DATA.resolve():
        raise ValueError(
            "Apply migration on a temporary copy first; publish reviewed files separately"
        )
    if (
        a.check
        and next(
            t
            for t in json.loads((a.data_dir / "tasks.json").read_text())
            if t["id"] == "Bank 2"
        ).get("scenario_version")
        == "2026-10-14-bank2-settlement-v3"
    ):
        from upgrade_bank2_settlement import main as settlement_main

        return settlement_main()
    updated = migrate(a.data_dir)
    if a.check:
        current = [
            json.loads((a.data_dir / n).read_text())
            for n in ["tasks.json", "db.json", "intake_answers.json"]
        ]
        if current != list(updated):
            raise SystemExit("Bank2 v2 migration pending")
        t = next(t for t in updated[0] if t["id"] == "Bank 2")
        rebuilt = deepcopy(t)
        configure(rebuilt, updated[1], updated[2], a.data_dir)
        if t != rebuilt:
            raise SystemExit("Bank2 v2 contract stale")
        print("Bank2 v2 current; independent oracle", independent_oracle(updated[1]))
        return
    if a.apply:
        for n, v in zip(["tasks.json", "db.json", "intake_answers.json"], updated):
            (a.data_dir / n).write_text(
                json.dumps(v, ensure_ascii=False, indent=2) + "\n"
            )


if __name__ == "__main__":
    main()
