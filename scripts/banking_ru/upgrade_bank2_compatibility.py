"""Author-only Bank2 v11: mutually compatible partial concessions."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from bank2_compatibility_oracle import construct
from build_access_contract import with_contract as access
from build_discovery_contract import with_contract as discovery
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_bank2_promotion_sources import VERSION as PARENT
from upgrade_composite_rewards import action, evidence

VERSION = "2026-10-21-bank2-compatibility-v11"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OFFER = "offer_650221"


def components():
    problem, witness = construct()

    def ident(i, a):
        return f"part_{966000 + i * 11 + a}"

    rows = {
        ident(i, a): dict(
            id=ident(i, a),
            exclusive_group=f"approval_group_joint_{i + 1}",
            amount=0.48,
            bank_cost=0.24,
            partner_cost=0.24,
            allocation_first_amount=0.12,
            incompatible_with=[],
        )
        for i in range(problem["groups"])
        for a in range(3)
    }
    for i, a, j, b in problem["conflicts"]:
        rows[ident(i, a)]["incompatible_with"].append(ident(j, b))
        rows[ident(j, b)]["incompatible_with"].append(ident(i, a))
    for row in rows.values():
        row["incompatible_with"].sort()
    result = list(rows.values())
    Random(21102026).shuffle(result)
    return result, sorted(ident(i, a) for i, a in enumerate(witness))


def migrate(data):
    paths = [data / x for x in ["db.json", "tasks.json", "intake_answers.json"]]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    offer = deepcopy(db["reward_resolution_offers"]["offer_640201"])
    offer.update(
        id=OFFER,
        amount=55.68,
        components=components()[0],
        selection_budget=dict(id="budget_650221", bank=27.84, partner=27.84),
    )
    assert OFFER not in db["reward_resolution_offers"]
    db["reward_resolution_offers"][OFFER] = offer
    path = "/knowledge/reward_resolution_amounts.md"
    file = data / "corpus" / path.lstrip("/")
    body = file.read_text().replace(
        "resolution-v3-selection", "resolution-v4-compatibility"
    )
    body += """

## Совместимость и полный состав меню

component_count показывает количество компонентов. Если components_complete=false,
компоненты доступны через get_reward_resolution_components по offer_id:
offset начинается с 0, next_offset задаёт следующую страницу, null означает конец.
Пустой components в такой записи не означает отсутствие меню. Небольшие меню
могут целиком возвращаться в get_reward_resolution_offers; оба источника допустимы.
Страницы возвращают исходные условия. Получите сведения, необходимые для проверки
выбранного пакета и доказательства максимума; чтение невыбранного компонента
само по себе не обязательно.

incompatible_with компонента перечисляет другие компоненты того же предложения,
которые нельзя включать в один пакет с ним. Достаточно запрета с любой стороны;
порядок перечисления или выбора значения не имеет. Пустой список не добавляет
ограничений. Проверяйте совместимость всего выбранного набора наряду с группами,
бюджетами и пределами недоплаты. Пропуск группы разрешён. Нельзя считать верхнюю
границу предложения достижимой, не проверив допустимую комбинацию.
"""
    file.write_text(body)
    manifest = json.loads((data / "manifest.json").read_text())
    manifest["documents"][path]["metadata"]["revision_id"] = (
        "resolution-v4-compatibility"
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
    assert calc["maximum"] == 5568 and calc["payouts"] == {
        "txn_804219": 1392,
        "txn_804227": 4176,
    }
    assert calc["case_total"] == 2869
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
