"""Scoped v5 -> v6 history expansion; use a temporary dataset before publication."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from bank2_multisource_oracle import CUSTOMER, A
from build_access_contract import with_contract as access
from build_discovery_contract import with_contract as discovery
from upgrade_bank2_multisource import configure as configure_facts
from upgrade_bank2_multisource import sync_manifest
from upgrade_bank2_resolution import configure as configure_resolution
from upgrade_composite_rewards import evidence

VERSION = "2026-10-20-bank2-history-v6"
PARENT = "2026-10-15-bank2-resolution-v5"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def purchase_id(index):
    return f"txn_816{100 + index:03}"


def refund_id(index):
    return f"txn_816{200 + index:03}"


def initialize(db):
    """Add raw historical facts, never precomputed entitlement fields."""
    template = db["transactions"][A]
    for i in range(24):
        tid = purchase_id(i)
        date = "2026-06-30" if i < 6 else f"2026-07-{3 + (i - 6) // 2:02}"
        posted = f"2026-07-{3 + i // 2:02}"
        card = "card_1187"
        purchase = {
            **deepcopy(template),
            "id": tid,
            "card_id": card,
            "date": date,
            "purchased_at": date + "T15:00:00+03:00",
            "posted_at": posted + f"T{10 + i % 2:02}:00:00+03:00",
            "amount": (10010 + i * 321) / 100,
            "merchant": "Городское кафе",
            "receipt_id": f"GC-{i + 1:04}",
            "order_id": f"GC-{i + 1:04}",
            "original_transaction_id": None,
            "reward_program": None,
            "cashback_granted": False,
            "compensates_cashback": False,
        }
        refund = {
            **deepcopy(purchase),
            "id": refund_id(i),
            "kind": "refund",
            "date": "2026-08-18",
            "purchased_at": None,
            "posted_at": "2026-08-18T12:00:00+03:00",
            "amount": (110 + i * 17) / 100,
            "original_transaction_id": tid,
            "merchant": "Возврат: Городское кафе",
        }
        rows = [purchase, refund]
        if i % 4 == 0:
            rows.append(
                {
                    **deepcopy(refund),
                    "id": f"txn_816{300 + i:03}",
                    "status": "processing",
                    "posted_at": None,
                    "amount": 2.50,
                }
            )
        if i % 6 == 0:
            rows.append(
                {
                    **deepcopy(refund),
                    "id": f"txn_816{400 + i:03}",
                    "kind": "refund",
                    "compensates_cashback": True,
                    "amount": 0.01,
                }
            )
            # A compensation is a transfer/credit, not a merchandise return.
            # Existing rules define compensation independently from its label.
            rows[-1]["kind"] = "cashback"
        for row in rows:
            if row["id"] in db["transactions"]:
                raise ValueError("History ID collision: " + row["id"])
            db["transactions"][row["id"]] = row
    cycle = next(
        x for x in db["cashback_rules"][CUSTOMER]["cycles"] if x["period"] == "2026-07"
    )
    cycle["limit"] = 282.10


def configure(task, db, answers, data):
    """Bind only causally required prefix facts before dependent actions."""
    criteria = task["evaluation_criteria"]
    criteria["result_argument_bindings"] = [
        x
        for x in criteria.get("result_argument_bindings", [])
        if not x["binding_id"].startswith("bank2:v5:")
    ]
    configure_facts(task, db, answers, data)
    calc = configure_resolution(task, db, data)
    task["scenario_version"] = VERSION
    task["description"]["notes"] = (
        VERSION + "; completion_only; difficulty not yet measured"
    )
    criteria = task["evaluation_criteria"]
    for i in range(24):
        for tid in [purchase_id(i), refund_id(i)]:
            row = db["transactions"][tid]
            fields = {
                k: row[k]
                for k in (
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
                    "compensates_cashback",
                )
            }
            for target in ["bank2:v5:pay", "bank2:v4:amend"]:
                options = dict(
                    only_if_target_attempted=True,
                    recoverable_result_statuses=[
                        "settlement_pending",
                        "state_changed",
                        "consent_stale",
                    ],
                )
                if target == "bank2:v4:amend":
                    options["target_item_ids"] = [A]
                criteria["result_evidence"].append(
                    evidence(
                        "bank2:v6:prefix:" + tid + ":" + target,
                        ["get_transactions", "get_transaction_details"],
                        tid,
                        fields,
                        target,
                        **options,
                    )
                )
    return calc


def migrate(data):
    """Preserve all other scenarios; a second application has no side effects."""
    paths = [data / name for name in ("db.json", "tasks.json", "intake_answers.json")]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError(
            "Unsupported Bank2 parent: " + str(task.get("scenario_version"))
        )
    initialize(db)
    configure(task, db, answers, data)
    access(tasks, db)
    discovery(tasks, db)
    for p, value in zip(paths, (db, tasks, answers)):
        p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
    return db, tasks, answers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir.resolve() == DATA.resolve():
        raise ValueError("Migrate a temporary copy, then verify it before publication")
    migrate(args.data_dir)
