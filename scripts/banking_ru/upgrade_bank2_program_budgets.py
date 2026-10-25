"""Author-only Bank2 v14: independently limited funding programs with a certified cap witness."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from upgrade_bank2_funded_menu import VERSION as PARENT
from upgrade_bank2_funded_menu import components as parent_components
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_composite_rewards import action, evidence

VERSION = "2026-10-21-bank2-program-budgets-v14"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OFFER = "offer_670261"


def components():
    rows, witness, _ = parent_components()
    rng = Random(24102026)
    for row in rows:
        row.update(
            bank_cost=0.06,
            partner_cost=0.06,
            pool_costs={
                f"pool_670261_bank_{rng.randrange(12) + 1}": 0.06,
                f"pool_670261_partner_{rng.randrange(12) + 1}": 0.06,
            },
        )
    # A distinct permitted alternative in one isolated group proves that
    # identity of the constructed certificate is not an acceptance condition.
    byid = {r["id"]: r for r in rows}
    old = next(
        x for x in witness if byid[x]["exclusive_group"] == "approval_group_funded_475"
    )
    alternative = next(
        r
        for r in rows
        if r["exclusive_group"] == byid[old]["exclusive_group"] and r["id"] != old
    )
    alternative["pool_costs"] = deepcopy(byid[old]["pool_costs"])
    alt = sorted(alternative["id"] if x == old else x for x in witness)
    return rows, witness, alt


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
        version=2,
        components=rows,
        selection_budget=dict(
            id="budget_670261", bank=28.56, partner=28.56, pools=pools
        ),
    )
    db["reward_resolution_offers"][OFFER] = offer
    path = "/knowledge/reward_resolution_amounts.md"
    file = data / "corpus" / path.lstrip("/")
    body = file.read_text().replace(
        "resolution-v4-compatibility", "resolution-v5-program-budgets"
    )
    body += """

## Бюджеты отдельных программ

selection_budget.pools, если список непуст, задаёт дополнительные независимые
бюджеты программ: id, source (bank или partner), amount в рублях. pool_costs
компонента распределяет его bank_cost и partner_cost по этим программам.
Сумма расходов bank-программ должна равняться bank_cost компонента, а сумма
partner-программ — partner_cost. Неизвестные программы и отрицательные расходы
недопустимы. Эти суммы не добавляют выплату клиенту и не являются комиссией.
Для выбранного пакета отдельно суммируйте расходы каждой программы: расход
не может превышать её amount. Одновременно действуют общие бюджеты bank и
partner, совместимость, группы, верхняя граница предложения и недоплата покупок.
Свободный бюджет одной программы не покрывает превышение другой. При пустом
pools дополнительные программные ограничения отсутствуют; pool_costs пуст.
"""
    file.write_text(body)
    manifest = json.loads((data / "manifest.json").read_text())
    manifest["documents"][path]["metadata"]["revision_id"] = (
        "resolution-v5-program-budgets"
    )
    manifest["documents"][path]["sha256"] = hashlib.sha256(
        file.read_bytes()
    ).hexdigest()
    (data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
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
