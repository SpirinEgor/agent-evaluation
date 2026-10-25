"""Author-only Bank2 v13: joint funding and compatibility with a certified cap witness."""

import argparse
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from build_access_contract import with_contract as access
from build_discovery_contract import with_contract as discovery
from upgrade_bank2_coupled_menu import (
    VERSION as PARENT,
)
from upgrade_bank2_coupled_menu import (
    components as parent_components,
)
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_composite_rewards import action, evidence

VERSION = "2026-10-21-bank2-funded-menu-v13"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OFFER = "offer_670261"


def components():
    rows, witness, alternate = parent_components()
    mapping = {r["id"]: f"part_{int(r['id'].split('_')[1]) + 20000}" for r in rows}
    for r in rows:
        r["id"] = mapping[r["id"]]
        r["incompatible_with"] = [mapping[x] for x in r["incompatible_with"]]
        r["exclusive_group"] = r["exclusive_group"].replace("coupled", "funded")
    witness = [mapping[x] for x in witness]
    alternate = [mapping[x] for x in alternate]
    for i in [474, 475]:
        for a in range(3):
            ident = f"part_{1000000 + i * 11 + a}"
            rows.append(
                dict(
                    id=ident,
                    exclusive_group=f"approval_group_funded_{i + 1}",
                    amount=0.12,
                    bank_cost=0.06,
                    partner_cost=0.06,
                    allocation_first_amount=0.03,
                    incompatible_with=[],
                )
            )
            if a == 0:
                witness.append(ident)
            if a == 1:
                alternate.append(ident)
    rng = Random(23102026)
    costs = {r["id"]: rng.randint(1, 11) for r in rows}
    target = sum(costs[x] for x in witness)
    delta = target - sum(costs[x] for x in alternate)
    for ident in alternate:
        change = max(1 - costs[ident], min(11 - costs[ident], delta))
        costs[ident] += change
        delta -= change
    assert delta == 0 and not set(witness) & set(alternate)
    for r in rows:
        r.update(
            bank_cost=costs[r["id"]] / 100, partner_cost=(12 - costs[r["id"]]) / 100
        )
    Random(23102026).shuffle(rows)
    return rows, sorted(witness), sorted(alternate)


def migrate(data):
    paths = [data / x for x in ["db.json", "tasks.json", "intake_answers.json"]]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    offer = deepcopy(db["reward_resolution_offers"]["offer_660241"])
    rows, witness, _ = components()
    bank = sum(round(r["bank_cost"] * 100) for r in rows if r["id"] in witness)
    offer.update(
        id=OFFER,
        amount=57.12,
        components=rows,
        selection_budget=dict(
            id="budget_670261", bank=bank / 100, partner=(5712 - bank) / 100
        ),
    )
    assert OFFER not in db["reward_resolution_offers"]
    db["reward_resolution_offers"][OFFER] = offer
    chronology = [
        deepcopy(x)
        for x in task["evaluation_criteria"]["result_evidence"]
        if x["evidence_id"].startswith("bank2:v8:")
    ]
    calc = configure(task, db, answers, data)
    e = task["evaluation_criteria"]
    e["result_evidence"] += chronology
    # Flat raw receipts support both available sources, with the same causal bound.
    for menu in db["reward_resolution_offers"].values():
        if not menu.get("components"):
            continue
        receipt = next(
            x
            for x in e["result_evidence"]
            if x["evidence_id"] == "bank2:v5:offer:" + menu["id"]
        )
        receipt["expected_fields"] = deepcopy(receipt["expected_fields"])
        for key in ["components", "selection_budget"]:
            receipt["expected_fields"].pop(key)
        for row in menu["components"]:
            e["result_evidence"].append(
                evidence(
                    "bank2:v11:component:" + row["id"],
                    [
                        "get_reward_resolution_offers",
                        "get_reward_resolution_components",
                    ],
                    row["id"],
                    row,
                    "bank2:v5:pay",
                    only_if_target_attempted=True,
                    target_argument_options={
                        "offer_id": [menu["id"]],
                        "component_ids.*": [row["id"]],
                    },
                )
            )
        row = menu["selection_budget"]
        e["result_evidence"].append(
            evidence(
                "bank2:v11:budget:" + row["id"],
                ["get_reward_resolution_offers"],
                row["id"],
                row,
                "bank2:v5:pay",
            )
        )
        if len(menu["components"]) > 128:
            pages = [
                action(
                    f"bank2:v11:page:{menu['id']}:{offset}",
                    "get_reward_resolution_components",
                    dict(offer_id=menu["id"], offset=offset, limit=100),
                )
                for offset in range(0, len(menu["components"]), 100)
            ]
            where = (
                next(
                    i
                    for i, a in enumerate(e["actions"])
                    if a["name"] == "get_reward_resolution_offers"
                )
                + 1
            )
            e["actions"][where:where] = pages
    assert calc["maximum"] == 5712 and calc["payouts"] == {
        "txn_804219": 1428,
        "txn_804227": 4284,
    }
    assert calc["case_total"] == 2725
    task["scenario_version"] = VERSION
    task["description"]["notes"] = (
        VERSION
        + "; completion_only; all feasible maxima accepted; difficulty unmeasured"
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
