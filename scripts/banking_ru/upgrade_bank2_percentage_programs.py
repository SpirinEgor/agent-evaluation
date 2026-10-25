"""Author-only Bank2 v20: percentage deductions, explicit rounding policy, and a larger fresh coupled menu."""

import argparse
import hashlib
import json
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from random import Random

from bank2_compatibility_oracle import construct
from upgrade_bank2_category_sources import apply_category_sources
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest
from upgrade_bank2_net_programs import VERSION as PARENT
from upgrade_composite_rewards import action, evidence

VERSION = "2026-10-21-bank2-percentage-programs-v20"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OFFER = "offer_670261"


def percentage(gross, rate):
    exact = gross * Fraction(str(rate))
    q, r = divmod(exact.numerator, exact.denominator)
    return q + int(
        2 * r > exact.denominator or (2 * r == exact.denominator and q % 2 == 1)
    )


def components():
    problem, assignment = construct(n=472, edges=1274, seed=31102026)
    rng = Random(31102027)
    ids = {(i, a): f"part_{1100000 + i * 3 + a}" for i in range(476) for a in range(3)}
    rows = [
        dict(
            id=ids[i, a],
            exclusive_group=f"approval_group_percent_{i + 1}",
            incompatible_with=[],
        )
        for i in range(476)
        for a in range(3)
    ]
    byid = {r["id"]: r for r in rows}
    for i, a, j, b in problem["conflicts"]:
        byid[ids[i, a]]["incompatible_with"].append(ids[j, b])
        byid[ids[j, b]]["incompatible_with"].append(ids[i, a])
    witness = [ids[i, a] for i, a in enumerate(assignment + [0, 0, 0, 0])]
    firsts = {r["id"]: rng.randint(1, 5) for r in rows}
    target = [1] * 119 + [3] * 238 + [5] * 119
    rng.shuffle(target)
    firsts.update(dict(zip(witness, target)))
    for row in rows:
        gross = rng.choice([20, 24, 28, 32, 36, 40])
        rate = rng.choice([0.125, 0.1875])
        first = firsts[row["id"]]
        fixed = gross - 12 - percentage(gross, rate)
        assert fixed >= 0
        costs = {
            "pool_670261_bank_11": first / 100,
            "pool_670261_bank_12": (6 - first) / 100,
        }
        ordinary = gross // 2 - 6
        keys = rng.sample(range(1, 11), 2)
        cut = rng.randrange(1, ordinary)
        costs.update(
            {
                f"pool_670261_bank_{k}": v / 100
                for k, v in zip(keys, [cut, ordinary - cut])
            }
        )
        keys = rng.sample(range(1, 13), 3)
        cuts = sorted(rng.sample(range(1, gross // 2), 2))
        costs.update(
            {
                f"pool_670261_partner_{k}": v / 100
                for k, v in zip(
                    keys, [cuts[0], cuts[1] - cuts[0], gross // 2 - cuts[1]]
                )
            }
        )
        row.update(
            amount=gross / 100,
            settlement_deduction=fixed / 100,
            settlement_deduction_rate=rate,
            bank_cost=gross / 200,
            partner_cost=gross / 200,
            allocation_first_amount=first / 100,
            pool_costs=costs,
        )
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
    gross = round(merged["amount"] * 100)
    merged["settlement_deduction_rate"] = 0.125
    merged["settlement_deduction"] = (gross - 24 - percentage(gross, 0.125)) / 100
    merged["pool_costs"] = {
        key: (
            round(byid[old]["pool_costs"].get(key, 0) * 100)
            + round(byid[combined]["pool_costs"].get(key, 0) * 100)
        )
        / 100
        for key in set(byid[old]["pool_costs"]) | set(byid[combined]["pool_costs"])
    }
    alt = sorted([x for x in witness if x not in [old, combined]] + [merged["id"]])
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
    base = witness[-3]
    lower = byid[ids[473, 1]]
    for key in financial:
        lower[key] = deepcopy(byid[base][key])
    gross = round(lower["amount"] * 100) - 4
    rate = lower["settlement_deduction_rate"]
    lower["amount"] = gross / 100
    lower["settlement_deduction"] = (gross - 12 - percentage(gross, rate)) / 100
    assert lower["settlement_deduction"] >= 0
    for key in ["bank_cost", "partner_cost"]:
        lower[key] = (round(lower[key] * 100) - 2) / 100
    for source in ["bank", "partner"]:
        key = next(
            k
            for k, v in lower["pool_costs"].items()
            if f"_{source}_" in k
            and k not in ["pool_670261_bank_11", "pool_670261_bank_12"]
            and round(v * 100) >= 2
        )
        lower["pool_costs"][key] = (round(lower["pool_costs"][key] * 100) - 2) / 100
    rng.shuffle(rows)
    assert len(rows) == 1428 and len(witness) == 476 and len(alt) == 475
    assert sum(round(byid[x]["allocation_first_amount"] * 100) for x in witness) == 1428
    return rows, sorted(witness), alt


def lower_deduction_selection():
    rows, witness, _ = components()
    byid = {r["id"]: r for r in rows}
    base = next(
        x for x in witness if byid[x]["exclusive_group"] == "approval_group_percent_474"
    )
    lower = f"part_{1100000 + 473 * 3 + 1}"
    return sorted(lower if x == base else x for x in witness)


def pool_over_selection():
    rows, witness, alt = components()
    byid = {r["id"]: r for r in rows}
    old = next(
        x for x in witness if byid[x]["exclusive_group"] == "approval_group_percent_476"
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
        version=8,
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
    body = path.read_text().split("## Номинальная доплата и сумма клиенту")[0]
    body = body.replace("resolution-v6-net-awards", "resolution-v7-percentage-awards")
    body = body.replace(
        "сумме (amount − settlement_deduction) выбранных компонентов",
        "сумме чистых доплат после фиксированной и процентной частей удержания выбранных компонентов",
    )
    body += """## Номинальная доплата и сумма клиенту

В компоненте amount — номинальная сумма. settlement_deduction задаёт фиксированную
часть удержания, settlement_deduction_rate — долю номинала для процентной части.
Отсутствующее поле равно нулю. Порядок вычисления и округления определён в
[правилах удержаний](reward_settlement_deductions.md).
Верхняя граница amount самого меню, allocation_first_amount, total_amount,
payouts и остатки дела выражены в чистых рублях клиенту. bank_cost, partner_cost
и pool_costs финансируют номинал и повторно из выплаты не вычитаются.
Удержание не создаёт отдельного списания со счёта, не погашает недоплату и не
уменьшает сохраняемое требование. Максимизируйте фактическое зачисление после
всех удержаний; согласуйте component_ids и чистые суммы до исполнения.
"""
    path.write_text(body)
    metadata = dict(
        id="reward_settlement_deductions",
        section="Акции и кешбэк",
        title="Расчёт удержаний из составных доплат",
        keywords=["урегулирование", "удержание", "процент", "округление"],
        revision_id="deductions-v1-percentage",
        effective_from="2026-06-01",
        effective_to=None,
        document_type="bank-knowledge",
        trust="bank-policy",
    )
    content = (
        "---\n"
        + "\n".join(
            k + ": " + json.dumps(v, ensure_ascii=False) for k, v in metadata.items()
        )
        + "\n---\n\n"
    )
    content += """# Расчёт удержаний из составных доплат

Правило применяется к компонентам составных предложений урегулирования.
settlement_deduction_rate — доля, например 0.125 означает 12.5 процента.
По каждому выбранному компоненту отдельно умножьте номинальный amount на эту долю.
Округлите процентную часть до ближайшей копейки; на точной половине копейки
выберите результат с чётным количеством копеек (half-even).
Например, 0.52 × 0.125 = 0.065 рубля округляется до 0.06, а 0.44 × 0.125 = 0.055
тоже до 0.06. Не округляйте процент по всему пакету и не заменяйте это правило
округлением половин вверх. Для начисления исходного кешбэка сохраняется его
собственное правило; здесь рассчитывается только удержание из предложения.

К округлённой процентной части прибавьте фиксированную settlement_deduction.
Чистая доплата компонента равна номиналу минус полученное полное удержание.
Суммируйте чистые доплаты и отдельно заявленные allocation_first_amount.
Фиксированная часть задаётся в целых копейках, неотрицательна; ставка конечна,
неотрицательна и меньше 1. Удержание должно оставлять положительную чистую доплату,
а первая покупка должна получать положительную сумму меньше этой доплаты.
Номинал, процентное и фиксированное удержания не добавляют погашение требований:
учитывается только фактическое зачисление клиенту.
"""
    doc = data / "corpus/knowledge/reward_settlement_deductions.md"
    doc.write_text(content)
    manifest_path = data / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    descriptor = manifest["documents"]["/knowledge/reward_resolution_amounts.md"]
    descriptor["metadata"]["revision_id"] = "resolution-v7-percentage-awards"
    descriptor["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["documents"]["/knowledge/reward_settlement_deductions.md"] = dict(
        metadata=metadata, sha256=hashlib.sha256(doc.read_bytes()).hexdigest()
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
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
    # Reference need not fetch unselected menus when its feasible net cap
    # witness already dominates their published upper bounds; model corpus is unchanged.
    e["actions"] = [
        a
        for a in e["actions"]
        if a["name"] != "get_reward_resolution_components"
        or a["arguments"]["offer_id"] == OFFER
    ]
    doc_path = "/knowledge/reward_settlement_deductions.md"
    read = action("bank2:v20:percentage-source", "file_read", dict(path=doc_path))
    where = next(
        i for i, a in enumerate(e["actions"]) if a["action_id"] == "bank2:v5:consent"
    )
    e["actions"].insert(where, read)
    e["result_evidence"].append(
        evidence(
            "bank2:v20:percentage-source",
            ["file_read"],
            "reward_settlement_deductions",
            dict(
                path=doc_path,
                complete=True,
                sha256=hashlib.sha256(doc.read_bytes()).hexdigest(),
            ),
            "bank2:v5:pay",
            only_if_target_attempted=True,
            target_argument_options={"offer_id": [OFFER]},
        )
    )
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
