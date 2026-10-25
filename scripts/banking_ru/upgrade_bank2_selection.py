"""Bank2 v9: maximize a jointly feasible menu of bank-approved concessions."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from build_access_contract import with_contract as access
from build_discovery_contract import with_contract as discovery
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_bank2_posting_order import VERSION as PARENT
from upgrade_composite_rewards import evidence

VERSION = "2026-10-21-bank2-selection-v9"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def components():
    rng = Random(27182026)
    rows = []
    for group in range(8):
        for option in range(3):
            amount = rng.randrange(620, 1360)
            bank = rng.randrange(120, amount - 100)
            rows.append(
                dict(
                    id=f"part_{941000 + group * 7 + option}",
                    exclusive_group=f"approval_group_{group + 1}",
                    amount=amount / 100,
                    bank_cost=bank / 100,
                    partner_cost=(amount - bank) / 100,
                    allocation_first_amount=rng.randrange(110, 395) / 100,
                )
            )
    return rows


def migrate(data):
    paths = [data / x for x in ["db.json", "tasks.json", "intake_answers.json"]]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    offer = deepcopy(db["reward_resolution_offers"]["offer_620157"])
    offer.update(
        id="offer_630181",
        amount=55,
        allocation_first_amount=1,
        components=components(),
        selection_budget=dict(id="budget_630181", bank=27, partner=28),
    )
    if offer["id"] in db["reward_resolution_offers"]:
        raise ValueError("Offer ID collision")
    db["reward_resolution_offers"][offer["id"]] = offer
    path = "/knowledge/reward_resolution_amounts.md"
    file = data / "corpus" / path.lstrip("/")
    body = file.read_text().replace(
        "resolution-v2-feasibility", "resolution-v3-selection"
    )
    body += """

## Составное банковское предложение

Непустое components обозначает меню частичных доплат, которые можно совместить
в одном исполнении этого предложения. В каждой exclusive_group можно выбрать
не более одного компонента. Группы сами по себе не требуют обязательного выбора.
Для выбранного подмножества отдельно сложите bank_cost и partner_cost: эти расходы
в рублях не должны превышать соответственно selection_budget.bank и
selection_budget.partner. Это источники финансирования банка и партнёра,
не комиссия клиенту: bank_cost + partner_cost каждого компонента равны amount.
Новая выплата равна сумме amount выбранных компонентов; amount самого предложения
при наличии components — верхняя граница, а не обещанная выплата. Составное
предложение имеет amount_basis=incremental; прежние начисления из этой суммы
повторно не вычитают. На allocation_first_transaction_id направьте сумму
allocation_first_amount выбранных компонентов, остаток — второй покупке.
allocation_first_amount верхнего уровня для меню не применяется.

Совместный пакет также должен укладываться в незаявленную недоплату каждой
покупки. Проверка каждого компонента отдельно не доказывает исполнимость суммы.
Не складывайте меню с другим предложением или ручной корректировкой по тем же
покупкам. При цели максимизации сравните исполнимые подмножества меню и обычные
предложения; выбор только самой большой отдельной доплаты или последовательный
жадный выбор не гарантирует максимум.

Передайте выбранные component_ids в reward_resolution_confirmation и
execute_reward_resolution. Порядок ID несущественен, повторы запрещены.
Согласие связывает именно выбранное подмножество, общую сумму, проводки,
полный остаток дела и ревизию. Меньший разрешённый пакет может быть исполнен,
но не выполняет поручение клиента о максимально возможной выплате.
"""
    file.write_text(body)
    manifest = json.loads((data / "manifest.json").read_text())
    entry = manifest["documents"][path]
    entry["metadata"]["revision_id"] = "resolution-v3-selection"
    entry["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    (data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    chronology = [
        deepcopy(x)
        for x in task["evaluation_criteria"]["result_evidence"]
        if x["evidence_id"].startswith("bank2:v8:")
    ]
    calc = configure(task, db, answers, data)
    task["evaluation_criteria"]["result_evidence"] += chronology
    # MCP numeric values may be stringified. Flat structured receipts retain
    # numeric equivalence for each raw component and budget without comparing
    # an opaque nested JSON string or requiring an arbitrary result order.
    receipt = next(
        x
        for x in task["evaluation_criteria"]["result_evidence"]
        if x["evidence_id"] == "bank2:v5:offer:offer_630181"
    )
    receipt["expected_fields"] = deepcopy(receipt["expected_fields"])
    receipt["expected_fields"].pop("components")
    receipt["expected_fields"].pop("selection_budget")
    for row in [*offer["components"], offer["selection_budget"]]:
        task["evaluation_criteria"]["result_evidence"].append(
            evidence(
                "bank2:v9:selection:" + row["id"],
                ["get_reward_resolution_offers"],
                row["id"],
                row,
                "bank2:v5:pay",
            )
        )
    # This dataset has one exact maximizing subset. The runtime still accepts any
    # feasible subset; the grader compares the resulting customer outcome.
    winner = calc["offers"][calc["winners"][0]]
    if len(winner.get("optimal_selections", [])) != 1:
        raise ValueError("Review equal optima before using this candidate")
    task.update(scenario_version=VERSION)
    task["description"]["purpose"] = (
        "Выбрать совместный максимум составного банковского предложения, сохранить права и остатки дела."
    )
    task["description"]["notes"] = VERSION + "; completion_only; difficulty unmeasured"
    access(tasks, db)
    discovery(tasks, db)
    for p, val in zip(paths, [db, tasks, answers]):
        p.write_text(json.dumps(val, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
    return db, tasks, answers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir.resolve() == DATA.resolve():
        raise ValueError("Use temporary data first")
    migrate(args.data_dir)
