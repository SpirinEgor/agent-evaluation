"""Bank2 v8: normalize offset timestamps before cap allocation and activation."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from bank2_multisource_oracle import CUSTOMER, A, B
from build_access_contract import with_contract as access
from build_discovery_contract import with_contract as discovery
from upgrade_bank2_feasible_offers import VERSION as PARENT
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_composite_rewards import evidence

VERSION = "2026-10-21-bank2-posting-order-v8"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def migrate(data):
    paths = [data / x for x in ["db.json", "tasks.json", "intake_answers.json"]]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    for ident, amount, instant in [
        ("txn_817101", 348.60, "2026-07-25T14:15:00+05:30"),
        ("txn_817105", 451.40, "2026-07-25T14:00:00+05:45"),
        ("txn_817109", 395.80, "2026-07-24T23:00:00-10:00"),
    ]:
        if ident in db["transactions"]:
            raise ValueError("Transaction ID collision: " + ident)
        row = deepcopy(db["transactions"][A])
        row.update(
            id=ident,
            amount=amount,
            merchant="Городское кафе",
            card_id="card_1187",
            date="2026-07-19",
            purchased_at="2026-07-19T15:00:00+03:00",
            posted_at=instant,
            receipt_id=ident,
            order_id=ident,
        )
        db["transactions"][ident] = row
    db["cashback_rules"][CUSTOMER]["cycles"][0]["limit"] = 322.10
    db["promotion_activations"]["pa_730416"]["activated_at"] = (
        "2026-07-26T14:30:00+05:45"
    )
    db["transactions"][B]["purchased_at"] = "2026-07-26T09:00:00+00:00"
    configure(task, db, answers, data)
    criteria = task["evaluation_criteria"]
    for ident in ["txn_817101", "txn_817105", "txn_817109"]:
        row = db["transactions"][ident]
        fields = {
            k: row[k]
            for k in [
                "customer_id",
                "card_id",
                "account_id",
                "kind",
                "status",
                "date",
                "posted_at",
                "amount",
                "mcc",
            ]
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
            if target.endswith("amend"):
                options["target_item_ids"] = [A]
            criteria["result_evidence"].append(
                evidence(
                    "bank2:v8:chronology:" + ident + ":" + target,
                    ["get_transactions", "get_transaction_details"],
                    ident,
                    fields,
                    target,
                    **options,
                )
            )
    task["scenario_version"] = VERSION
    task["description"]["notes"] = (
        VERSION + "; offset chronology and stable-ID tie; difficulty unmeasured"
    )
    access(tasks, db)
    discovery(tasks, db)
    for path, val in zip(paths, [db, tasks, answers]):
        path.write_text(json.dumps(val, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
    return db, tasks, answers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir.resolve() == DATA.resolve():
        raise ValueError("Use temporary data first")
    migrate(args.data_dir)
