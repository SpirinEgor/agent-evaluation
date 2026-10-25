"""Author-only Bank2 v18: fresh compatibility and coupled per-purchase funding allocation."""

import argparse
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from bank2_compatibility_oracle import construct
from upgrade_bank2_category_sources import apply_category_sources
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_bank2_weighted_programs import VERSION as PARENT
from upgrade_composite_rewards import action, evidence

VERSION = "2026-10-21-bank2-allocation-programs-v18"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OFFER = "offer_670261"


def components():
    # Fresh public graph: prior offers do not supply an isomorphic assignment.
    problem, assignment = construct(n=116, edges=314, seed=28102026)
    rng = Random(29102026)
    ids = {(i, a): f"part_{1090000 + i * 3 + a}" for i in range(119) for a in range(3)}
    rows = [
        dict(
            id=ids[i, a],
            exclusive_group=f"approval_group_allocation_{i + 1}",
            incompatible_with=[],
        )
        for i in range(119)
        for a in range(3)
    ]
    byid = {r["id"]: r for r in rows}
    for i, a, j, b in problem["conflicts"]:
        byid[ids[i, a]]["incompatible_with"].append(ids[j, b])
        byid[ids[j, b]]["incompatible_with"].append(ids[i, a])
    witness = [ids[i, a] for i, a in enumerate(assignment + [0, 0, 0])]
    target = [32] * 39 + [48] * 41 + [64] * 39
    rng.shuffle(target)
    amounts = {r["id"]: rng.choice([16, 32, 48, 64, 80]) for r in rows}
    amounts.update(dict(zip(witness, target)))
    ratios = {r["id"]: rng.choice([1, 2, 3]) for r in rows}
    # Within each value class, paired 1/8 and 3/8 shares plus one 1/4
    # prove a 1/4 aggregate witness without requiring uniform individual shares.
    for amount in [32, 48, 64]:
        selected = [x for x in witness if amounts[x] == amount]
        pairs = (len(selected) - 1) // 2
        shares = [1] * pairs + [3] * pairs + [2]
        rng.shuffle(shares)
        ratios.update(dict(zip(selected, shares)))
    for row in rows:
        amount = amounts[row["id"]]
        first = amount * ratios[row["id"]] // 8
        half = amount // 2
        # 11/12 together fund one quarter of total. At the offer maximum,
        # their equal caps force the aggregate allocation to A, while
        # individual component allocations remain heterogeneous.
        assert first % 2 == 0
        costs = {
            "pool_670261_bank_11": first / 200,
            "pool_670261_bank_12": (amount // 4 - first // 2) / 100,
        }
        ordinary = amount // 4
        pools = rng.sample(range(1, 11), 2)
        cut = rng.randrange(1, ordinary)
        costs.update(
            {
                f"pool_670261_bank_{k}": v / 100
                for k, v in zip(pools, [cut, ordinary - cut])
            }
        )
        pools = rng.sample(range(1, 13), 3)
        cuts = sorted(rng.sample(range(1, half), 2))
        costs.update(
            {
                f"pool_670261_partner_{k}": v / 100
                for k, v in zip(pools, [cuts[0], cuts[1] - cuts[0], half - cuts[1]])
            }
        )
        row.update(
            amount=amount / 100,
            allocation_first_amount=first / 100,
            bank_cost=half / 100,
            partner_cost=half / 100,
            pool_costs=costs,
        )
    old = witness[-1]
    combined = witness[-2]
    other = [byid[ids[118, a]] for a in [1, 2]]
    fields = [
        "amount",
        "allocation_first_amount",
        "bank_cost",
        "partner_cost",
        "pool_costs",
    ]
    for row in other:
        row.update({k: deepcopy(byid[old][k]) for k in fields})
    merged = other[0]
    for key in fields[:-1]:
        merged[key] = (
            round(byid[old][key] * 100) + round(byid[combined][key] * 100)
        ) / 100
    merged["pool_costs"] = {
        key: (
            round(byid[old]["pool_costs"].get(key, 0) * 100)
            + round(byid[combined]["pool_costs"].get(key, 0) * 100)
        )
        / 100
        for key in set(byid[old]["pool_costs"]) | set(byid[combined]["pool_costs"])
    }
    alt = sorted([x for x in witness if x not in [old, combined]] + [merged["id"]])
    wrong = other[1]["pool_costs"]
    key = next(k for k in wrong if "_partner_" in k)
    new = next(
        f"pool_670261_partner_{i}"
        for i in range(1, 13)
        if f"pool_670261_partner_{i}" not in wrong
    )
    wrong[new] = wrong.pop(key)
    rng.shuffle(rows)
    assert len(rows) == 357 and len(witness) == 119 and len(alt) == 118
    assert sum(round(byid[x]["amount"] * 100) for x in witness) == 5712
    assert sum(round(byid[x]["allocation_first_amount"] * 100) for x in witness) == 1428
    return rows, sorted(witness), alt


def pool_over_selection():
    rows, witness, alt = components()
    byid = {r["id"]: r for r in rows}
    old = next(
        x
        for x in witness
        if byid[x]["exclusive_group"] == "approval_group_allocation_119"
    )
    wrong = next(
        r["id"]
        for r in rows
        if r["exclusive_group"] == byid[old]["exclusive_group"]
        and r["id"] not in set(witness) | set(alt)
    )
    return sorted(wrong if x == old else x for x in witness)


def migrate(data):
    paths = [data / x for x in ["db.json", "tasks.json", "intake_answers.json"]]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    offer = deepcopy(db["reward_resolution_offers"][OFFER])
    rows, witness, _ = components()
    selected = [r for r in rows if r["id"] in witness]
    pools = [
        dict(
            id=f"pool_670261_{source}_{i}",
            source=source,
            amount=sum(
                round(r["pool_costs"].get(f"pool_670261_{source}_{i}", 0) * 100)
                for r in selected
            )
            / 100,
        )
        for source in ["bank", "partner"]
        for i in range(1, 13)
    ]
    offer.update(
        version=6,
        components=rows,
        selection_budget=dict(
            id="budget_670261", bank=28.56, partner=28.56, pools=pools
        ),
    )
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
            expected = deepcopy(row)
            if "pool_costs" in expected:
                expected["pool_costs"] = {
                    k: str(v) for k, v in expected["pool_costs"].items()
                }
            e["result_evidence"].append(
                evidence(
                    "bank2:v11:component:" + row["id"],
                    [
                        "get_reward_resolution_offers",
                        "get_reward_resolution_components",
                    ],
                    row["id"],
                    expected,
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
                {k: v for k, v in row.items() if k != "pools"},
                "bank2:v5:pay",
            )
        )
        for pool in row.get("pools", []):
            e["result_evidence"].append(
                evidence(
                    "bank2:v14:pool:" + pool["id"],
                    ["get_reward_resolution_offers"],
                    pool["id"],
                    pool,
                    "bank2:v5:pay",
                    only_if_target_attempted=True,
                    target_argument_options={"offer_id": [menu["id"]]},
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
    apply_category_sources(tasks, db)
    task["scenario_version"] = VERSION
    task["description"]["notes"] = (
        VERSION
        + "; completion_only; all feasible maxima accepted; difficulty unmeasured"
    )
    # Existing offer ID/owner retained; cross-task alignment deferred until difficulty is measured.
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
