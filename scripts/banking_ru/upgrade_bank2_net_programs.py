"""Author-only Bank2 v19: nominal offers, declared deductions, and guaranteed net cash."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from upgrade_bank2_allocation_programs import (
    VERSION as PARENT,
)
from upgrade_bank2_allocation_programs import (
    components as parent_components,
)
from upgrade_bank2_category_sources import apply_category_sources
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_composite_rewards import action, evidence

VERSION = "2026-10-21-bank2-net-programs-v19"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OFFER = "offer_670261"


def components():
    rows, witness, alt = parent_components()
    rng = Random(30102026)
    byid = {r["id"]: r for r in rows}
    for row in rows:
        net = round(row["amount"] * 100)
        deduction = rng.choice([4, 8, 12, 16, 20, 24, 28, 32])
        row["settlement_deduction"] = deduction / 100
        row["amount"] = (net + deduction) / 100
        row["bank_cost"] = (round(row["bank_cost"] * 100) + deduction // 2) / 100
        row["partner_cost"] = (round(row["partner_cost"] * 100) + deduction // 2) / 100
        for source, maximum in [("bank", 10), ("partner", 12)]:
            key = f"pool_670261_{source}_{rng.randint(1, maximum)}"
            row["pool_costs"][key] = (
                round(row["pool_costs"].get(key, 0) * 100) + deduction // 2
            ) / 100
    old = next(
        x
        for x in witness
        if byid[x]["exclusive_group"] == "approval_group_allocation_119"
    )
    combined = next(
        x
        for x in witness
        if byid[x]["exclusive_group"] == "approval_group_allocation_118"
    )
    merged = byid[next(x for x in alt if x not in witness)]
    for key in [
        "amount",
        "settlement_deduction",
        "allocation_first_amount",
        "bank_cost",
        "partner_cost",
    ]:
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
    wrong = next(
        r
        for r in rows
        if r["exclusive_group"] == byid[old]["exclusive_group"]
        and r["id"] not in set(witness) | set(alt)
    )
    for key in [
        "amount",
        "settlement_deduction",
        "allocation_first_amount",
        "bank_cost",
        "partner_cost",
        "pool_costs",
    ]:
        wrong[key] = deepcopy(byid[old][key])
    costs = wrong["pool_costs"]
    key = next(k for k in costs if "_partner_" in k)
    new = next(
        f"pool_670261_partner_{i}"
        for i in range(1, 13)
        if f"pool_670261_partner_{i}" not in costs
    )
    costs[new] = costs.pop(key)
    # Same net maximum with a strictly different gross amount and deduction.
    base = next(
        x
        for x in witness
        if byid[x]["exclusive_group"] == "approval_group_allocation_117"
    )
    lower = next(
        r
        for r in rows
        if r["exclusive_group"] == byid[base]["exclusive_group"] and r["id"] != base
    )
    for key in [
        "amount",
        "settlement_deduction",
        "allocation_first_amount",
        "bank_cost",
        "partner_cost",
        "pool_costs",
    ]:
        lower[key] = deepcopy(byid[base][key])
    for key, delta in [
        ("amount", 4),
        ("settlement_deduction", 4),
        ("bank_cost", 2),
        ("partner_cost", 2),
    ]:
        lower[key] = (round(lower[key] * 100) - delta) / 100
    assert lower["settlement_deduction"] >= 0
    for source in ["bank", "partner"]:
        key = next(
            k
            for k, v in lower["pool_costs"].items()
            if f"_{source}_" in k
            and k not in ["pool_670261_bank_11", "pool_670261_bank_12"]
            and round(v * 100) >= 2
        )
        lower["pool_costs"][key] = (round(lower["pool_costs"][key] * 100) - 2) / 100
    assert (
        sum(
            round(byid[x]["amount"] * 100)
            - round(byid[x]["settlement_deduction"] * 100)
            for x in witness
        )
        == 5712
    )
    return rows, witness, alt


def lower_deduction_selection():
    rows, witness, _ = components()
    byid = {r["id"]: r for r in rows}
    base = next(
        x
        for x in witness
        if byid[x]["exclusive_group"] == "approval_group_allocation_117"
    )
    lower = next(
        r["id"]
        for r in rows
        if r["exclusive_group"] == byid[base]["exclusive_group"] and r["id"] != base
    )
    return sorted(lower if x == base else x for x in witness)


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
        version=7,
        components=rows,
        selection_budget=dict(
            id="budget_670261",
            bank=sum(round(r["bank_cost"] * 100) for r in selected) / 100,
            partner=sum(round(r["partner_cost"] * 100) for r in selected) / 100,
            pools=pools,
        ),
    )
    db["reward_resolution_offers"][OFFER] = offer
    path = data / "corpus/knowledge/reward_resolution_amounts.md"
    body = path.read_text().replace(
        "resolution-v5-program-budgets", "resolution-v6-net-awards"
    )
    old_text = "Новая выплата равна сумме amount выбранных компонентов; amount самого предложения"
    assert old_text in body
    body = body.replace(
        old_text,
        "Новая выплата равна сумме (amount − settlement_deduction) выбранных компонентов; amount самого предложения",
    )
    body += """

## Номинальная доплата и сумма клиенту

В компоненте amount — номинальная сумма до удержания settlement_deduction.
Если поле отсутствует или равно нулю, удержания нет. Удержание задано в рублях
с точностью до копейки, неотрицательно и должно оставлять положительную выплату.
Оно применяется один раз к выбранному компоненту. bank_cost, partner_cost и
pool_costs финансируют полную номинальную сумму; их повторно из выплаты не вычитают.
Клиенту поступает amount − settlement_deduction. Удержание не создаёт отдельного
списания со счёта, не погашает недоплату и не уменьшает сохраняемое требование.

Цель maximize_additional_guaranteed_cash сравнивает именно фактически зачисляемые
рубли после удержаний. Верхняя граница amount самого составного предложения,
allocation_first_amount, total_amount, payouts и остатки дела выражены в чистых
рублях клиенту. Долю первой покупки берут из allocation_first_amount, второй
направляют остаток чистой выплаты. Не увеличивайте проводки на сумму удержаний.
Согласуйте точные component_ids и чистые суммы с клиентом до исполнения.
"""
    path.write_text(body)
    manifest_path = data / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["documents"]["/knowledge/reward_resolution_amounts.md"]["metadata"][
        "revision_id"
    ] = "resolution-v6-net-awards"
    manifest["documents"]["/knowledge/reward_resolution_amounts.md"]["sha256"] = (
        hashlib.sha256(path.read_bytes()).hexdigest()
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    db["reward_resolution_preferences"]["gromov_a_1187"]["text"] += (
        " Указанные в условиях удержания допустимы; максимизируйте фактическое зачисление после них, сохранив оставшиеся требования."
    )
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
    task["ticket"] += (
        " Сравнивайте суммы, которые фактически поступят мне после указанных в предложениях удержаний."
    )
    task["user_scenario"]["instructions"].update(
        reason_for_call=task["ticket"], known_info=task["ticket"]
    )
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
