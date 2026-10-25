"""Bank2 v10: larger jointly funded menus, accepting every optimal subset."""

import argparse
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from build_access_contract import with_contract as access
from build_discovery_contract import with_contract as discovery
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_bank2_selection import VERSION as PARENT
from upgrade_composite_rewards import evidence

VERSION = "2026-10-21-bank2-large-selection-v10"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def components():
    """Raw bank records; the author witness is never copied into agent data."""
    rng = Random(101040)
    chosen_groups = rng.sample(range(40), 20)
    units = [20] * 20
    for _ in range(1375 - sum(units)):
        units[rng.randrange(20)] += 1
    amounts = [x * 4 for x in units]
    bank = [x // 2 for x in amounts]
    while sum(bank) > 2700:
        bank[rng.randrange(20)] -= 1
    planted = {g: (amounts[i], bank[i]) for i, g in enumerate(chosen_groups)}
    rows, witness, alternate = [], [], []
    for group in range(40):
        options = []
        chosen = (group * 7 + 1) % 3
        for option in range(3):
            amount = rng.randrange(25, 201) * 4
            cost = rng.randrange(20, amount - 19)
            if group in planted and option == chosen:
                amount, cost = planted[group]
            options.append(
                dict(
                    id=f"part_{952000 + group * 11 + option}",
                    exclusive_group=f"approval_group_large_{group + 1}",
                    amount=amount / 100,
                    bank_cost=cost / 100,
                    partner_cost=(amount - cost) / 100,
                    allocation_first_amount=amount / 400,
                )
            )
        if group in planted:
            witness.append(options[chosen]["id"])
            alternate.append(options[chosen]["id"])
        if group == chosen_groups[0]:
            other = (chosen + 1) % 3
            options[other] = {**options[chosen], "id": options[other]["id"]}
            alternate[-1] = options[other]["id"]
        rows.extend(options)
    rng.shuffle(rows)
    return rows, sorted(witness), sorted(alternate)


def migrate(data):
    paths = [data / x for x in ["db.json", "tasks.json", "intake_answers.json"]]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    offer = deepcopy(db["reward_resolution_offers"]["offer_630181"])
    offer.update(
        id="offer_640201",
        components=components()[0],
        selection_budget=dict(id="budget_640201", bank=27, partner=28),
    )
    if offer["id"] in db["reward_resolution_offers"]:
        raise ValueError("Offer ID collision")
    db["reward_resolution_offers"][offer["id"]] = offer
    chronology = [
        deepcopy(x)
        for x in task["evaluation_criteria"]["result_evidence"]
        if x["evidence_id"].startswith("bank2:v8:")
    ]
    calc = configure(task, db, answers, data)
    task["evaluation_criteria"]["result_evidence"] += chronology
    # Each component is independently discoverable inside the protected raw offer
    # result. Flat receipts preserve MCP's numeric-string equivalence.
    for menu in db["reward_resolution_offers"].values():
        if not menu.get("components"):
            continue
        receipt = next(
            x
            for x in task["evaluation_criteria"]["result_evidence"]
            if x["evidence_id"] == "bank2:v5:offer:" + menu["id"]
        )
        receipt["expected_fields"] = deepcopy(receipt["expected_fields"])
        for field in ["components", "selection_budget"]:
            receipt["expected_fields"].pop(field)
        for row in [*menu["components"], menu["selection_budget"]]:
            task["evaluation_criteria"]["result_evidence"].append(
                evidence(
                    "bank2:v10:selection:" + row["id"],
                    ["get_reward_resolution_offers"],
                    row["id"],
                    row,
                    "bank2:v5:pay",
                )
            )
    assert calc["maximum"] == 5500 and calc["payouts"] == {
        "txn_804219": 1375,
        "txn_804227": 4125,
    }
    # At the maximum all feasible subsets yield this allocation, because every
    # raw component declares the exact same quarter split. IDs are not graded.
    assert all(
        round(x["allocation_first_amount"] * 400) == round(x["amount"] * 100)
        for x in offer["components"]
    )
    task.update(scenario_version=VERSION)
    task["description"].update(
        purpose="Выбрать совместный максимум большой программы доплат и сохранить невозмещённые требования.",
        notes=VERSION
        + "; completion_only; every optimal subset accepted; difficulty unmeasured",
    )
    access(tasks, db)
    discovery(tasks, db)
    for path, value in zip(paths, [db, tasks, answers]):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
    return db, tasks, answers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir.resolve() == DATA.resolve():
        raise ValueError("Use temporary data first")
    migrate(args.data_dir)
