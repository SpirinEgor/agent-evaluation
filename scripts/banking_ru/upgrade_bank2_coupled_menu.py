"""Author-only Bank2 v12: coupled compatibility groups with a certified cap witness."""

import argparse
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from bank2_compatibility_oracle import valid
from build_access_contract import with_contract as access
from build_discovery_contract import with_contract as discovery
from upgrade_bank2_compatibility import VERSION as PARENT
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_composite_rewards import action, evidence

VERSION = "2026-10-21-bank2-coupled-menu-v12"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OFFER = "offer_660241"


def components():
    n = 474
    rng = Random(22102026)
    planted = [i % 3 for i in range(n)]
    rng.shuffle(planted)
    labels = []
    for _ in range(n):
        p = list(range(3))
        rng.shuffle(p)
        labels.append(p)
    edges = rng.sample(
        [(i, j) for i in range(n) for j in range(i + 1, n) if planted[i] != planted[j]],
        round(n * 4.7 / 2),
    )

    def ident(i, a):
        return f"part_{980000 + i * 11 + a}"

    rows = {
        ident(i, a): dict(
            id=ident(i, a),
            exclusive_group=f"approval_group_coupled_{i + 1}",
            amount=0.12,
            bank_cost=0.06,
            partner_cost=0.06,
            allocation_first_amount=0.03,
            incompatible_with=[],
        )
        for i in range(n)
        for a in range(3)
    }
    conflicts = []
    for i, j in edges:
        for c in range(3):
            a, b = labels[i].index(c), labels[j].index(c)
            conflicts.append((i, a, j, b))
            rows[ident(i, a)]["incompatible_with"].append(ident(j, b))
            rows[ident(j, b)]["incompatible_with"].append(ident(i, a))
    assignments = [
        [labels[i].index((c + shift) % 3) for i, c in enumerate(planted)]
        for shift in [0, 1]
    ]
    assert all(valid(dict(groups=n, conflicts=conflicts), w) for w in assignments)
    for row in rows.values():
        row["incompatible_with"].sort()
    result = list(rows.values())
    Random(22102026).shuffle(result)
    return result, *[sorted(ident(i, a) for i, a in enumerate(w)) for w in assignments]


def migrate(data):
    paths = [data / x for x in ["db.json", "tasks.json", "intake_answers.json"]]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    offer = deepcopy(db["reward_resolution_offers"]["offer_650221"])
    offer.update(
        id=OFFER,
        amount=56.88,
        components=components()[0],
        selection_budget=dict(id="budget_660241", bank=28.44, partner=28.44),
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
    assert calc["maximum"] == 5688 and calc["payouts"] == {
        "txn_804219": 1422,
        "txn_804227": 4266,
    }
    assert calc["case_total"] == 2749
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
