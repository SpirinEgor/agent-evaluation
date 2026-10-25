"""Author-only v22: tightly coupled funding equalities and a fresh less-determined choice graph."""

import argparse
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from bank2_compatibility_oracle import construct
from bank2_resolution_oracle import compatibility_choices, component_fingerprint
from upgrade_bank2_dependency_programs import VERSION as PARENT
from upgrade_bank2_multisource import sync_manifest
from upgrade_bank2_percentage_programs import OFFER, percentage

VERSION = "2026-10-21-bank2-balanced-programs-v22"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def components():
    problem, assignment = construct(n=472, edges=1100, seed=22102029)
    rng = Random(22102030)
    ids = {(i, a): f"part_{1120000 + i * 3 + a}" for i in range(476) for a in range(3)}
    rows = [
        dict(
            id=ids[i, a],
            exclusive_group=f"approval_group_balanced_{i + 1}",
            incompatible_with=[],
            required_companions=[],
        )
        for i in range(476)
        for a in range(3)
    ]
    byid = {r["id"]: r for r in rows}
    for i, a, j, b in problem["conflicts"]:
        byid[ids[i, a]]["incompatible_with"].append(ids[j, b])
        byid[ids[j, b]]["incompatible_with"].append(ids[i, a])
    witness = [ids[i, a] for i, a in enumerate(assignment + [0, 0, 0, 0])]
    chosen = set(witness)
    firsts = {r["id"]: rng.randint(1, 5) for r in rows}
    target = [1] * 119 + [3] * 238 + [5] * 119
    rng.shuffle(target)
    firsts.update(dict(zip(witness, target)))
    for row in rows:
        rate = rng.choice([0.125, 0.1875])
        first = firsts[row["id"]]
        costs = {
            "pool_670261_bank_11": first / 100,
            "pool_670261_bank_12": (6 - first) / 100,
        }
        keys = rng.sample(range(1, 11), 2)
        cut = rng.randrange(1, 12)
        costs.update(
            {f"pool_670261_bank_{k}": v / 100 for k, v in zip(keys, [cut, 12 - cut])}
        )
        keys = rng.sample(range(1, 13), 3)
        cuts = sorted(rng.sample(range(1, 18), 2))
        costs.update(
            {
                f"pool_670261_partner_{k}": v / 100
                for k, v in zip(keys, [cuts[0], cuts[1] - cuts[0], 18 - cuts[1]])
            }
        )
        row.update(
            amount=0.36,
            settlement_deduction=(36 - 12 - percentage(36, rate)) / 100,
            settlement_deduction_rate=rate,
            bank_cost=0.18,
            partner_cost=0.18,
            allocation_first_amount=first / 100,
            pool_costs=costs,
        )
    # Sparse substantive conditions retain branching; financial feasibility must also be solved.
    for row in rng.sample(rows[: 472 * 3], 120):
        own = (int(row["id"].split("_")[1]) - 1120000) // 3
        targets = rng.sample([i for i in range(472) if i != own], 3)
        clause = [ids[i, rng.randrange(3)] for i in targets]
        if row["id"] in chosen and not chosen.intersection(clause):
            j = rng.randrange(3)
            clause[j] = witness[targets[j]]
        rng.shuffle(clause)
        row["required_companions"] = [clause]
    financial = [
        "amount",
        "settlement_deduction",
        "settlement_deduction_rate",
        "bank_cost",
        "partner_cost",
        "allocation_first_amount",
        "pool_costs",
    ]
    old = witness[-1]
    combined = witness[-2]
    merged = byid[ids[475, 1]]
    for key in ["amount", "bank_cost", "partner_cost", "allocation_first_amount"]:
        merged[key] = (
            round(byid[old][key] * 100) + round(byid[combined][key] * 100)
        ) / 100
    merged["settlement_deduction_rate"] = 0.125
    merged["settlement_deduction"] = (72 - 24 - percentage(72, 0.125)) / 100
    merged["pool_costs"] = {
        k: (
            round(byid[old]["pool_costs"].get(k, 0) * 100)
            + round(byid[combined]["pool_costs"].get(k, 0) * 100)
        )
        / 100
        for k in set(byid[old]["pool_costs"]) | set(byid[combined]["pool_costs"])
    }
    alternate = sorted(
        [x for x in witness if x not in [old, combined]] + [merged["id"]]
    )
    wrong = byid[ids[475, 2]]
    for key in financial:
        wrong[key] = deepcopy(byid[old][key])
    costs = wrong["pool_costs"]
    key = next(k for k in costs if "_partner_" in k)
    new = next(
        f"pool_670261_partner_{i}"
        for i in range(1, 13)
        if f"pool_670261_partner_{i}" not in costs
    )
    costs[new] = costs.pop(key)
    lower = byid[ids[473, 1]]
    base = witness[-3]
    for key in financial:
        lower[key] = deepcopy(byid[base][key])
    lower["amount"] = 0.32
    lower["settlement_deduction"] = (
        32 - 12 - percentage(32, lower["settlement_deduction_rate"])
    ) / 100
    lower["bank_cost"] = lower["partner_cost"] = 0.16
    for source in ["bank", "partner"]:
        key = next(
            k
            for k, v in lower["pool_costs"].items()
            if f"_{source}_" in k
            and k not in ["pool_670261_bank_11", "pool_670261_bank_12"]
            and round(v * 100) >= 2
        )
        lower["pool_costs"][key] = (round(lower["pool_costs"][key] * 100) - 2) / 100
    bad = byid[ids[472, 1]]
    base = witness[-4]
    for key in financial:
        bad[key] = deepcopy(byid[base][key])
    bad["required_companions"] = [
        [next(ids[0, a] for a in range(3) if ids[0, a] not in chosen)]
    ]
    rng.shuffle(rows)
    return rows, sorted(witness), alternate


def replace_isolated(group, index):
    rows, witness, _ = components()
    byid = {r["id"]: r for r in rows}
    return sorted(
        f"part_{1120000 + (group - 1) * 3 + index}"
        if byid[x]["exclusive_group"] == f"approval_group_balanced_{group}"
        else x
        for x in witness
    )


def lower_deduction_selection():
    return replace_isolated(474, 1)


def pool_over_selection():
    return replace_isolated(476, 2)


def dependency_violation_selection():
    return replace_isolated(473, 1)


def migrate(data):
    dbpath = data / "db.json"
    taskpath = data / "tasks.json"
    db = json.loads(dbpath.read_text())
    tasks = json.loads(taskpath.read_text())
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, json.loads((data / "intake_answers.json").read_text())
    assert task["scenario_version"] == PARENT
    rows, witness, _ = components()
    offer = db["reward_resolution_offers"][OFFER]
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
        version=10,
        components=rows,
        selection_budget=dict(
            id="budget_670261", bank=85.68, partner=85.68, pools=pools
        ),
    )
    fp = component_fingerprint(rows)
    cert = Path(__file__).with_name("compatibility_certificates") / (fp + ".json")
    cert.write_text(json.dumps(dict(component_ids=witness), indent=2) + "\n")
    for ids in [witness, components()[2], lower_deduction_selection()]:
        assert (
            compatibility_choices(
                offer, {"txn_804219": 1437, "txn_804227": 5300}, certificate=ids
            )[0]["additional"]
            == 5712
        )
    e = task["evaluation_criteria"]
    oldids = {
        r["expected_fields"]["id"]
        for r in e["result_evidence"]
        if r["evidence_id"].startswith("bank2:v11:component:")
        and r.get("target_argument_options", {}).get("offer_id") == [OFFER]
    }
    template = next(
        deepcopy(r)
        for r in e["result_evidence"]
        if r["evidence_id"].startswith("bank2:v11:component:")
        and r["expected_fields"]["id"] in oldids
    )
    e["result_evidence"] = [
        r
        for r in e["result_evidence"]
        if not (
            r["evidence_id"].startswith("bank2:v11:component:")
            and r["expected_fields"]["id"] in oldids
        )
    ]
    for row in rows:
        r = deepcopy(template)
        r["evidence_id"] = "bank2:v11:component:" + row["id"]
        r["record_id"] = row["id"]
        r["target_argument_options"]["component_ids.*"] = [row["id"]]
        r["expected_fields"] = deepcopy(row)
        r["expected_fields"]["pool_costs"] = {
            k: str(v) for k, v in row["pool_costs"].items()
        }
        e["result_evidence"].append(r)
    for r in e["result_evidence"]:
        if r["evidence_id"] == "bank2:v5:offer:" + OFFER:
            r["expected_fields"]["version"] = 10
        if r["evidence_id"] == "bank2:v11:budget:budget_670261":
            r["expected_fields"] = {
                k: v for k, v in offer["selection_budget"].items() if k != "pools"
            }
        if r["evidence_id"].startswith("bank2:v14:pool:pool_670261_"):
            r["expected_fields"] = deepcopy(
                next(p for p in pools if p["id"] == r["expected_fields"]["id"])
            )
    for a in e["actions"]:
        args = a["arguments"]
        packet = args.get("request", args)
        if packet.get("offer_id") == OFFER and "offer_version" in packet:
            packet.update(offer_version=10, component_ids=witness)
    task["scenario_version"] = VERSION
    task["description"]["notes"] = (
        VERSION
        + "; completion_only; all feasible maxima accepted; difficulty unmeasured"
    )
    dbpath.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n")
    taskpath.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
    return db, tasks, json.loads((data / "intake_answers.json").read_text())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", type=Path, required=True)
    a = p.parse_args()
    if a.data_dir.resolve() == DATA.resolve():
        raise ValueError("Temporary data only")
    migrate(a.data_dir)
