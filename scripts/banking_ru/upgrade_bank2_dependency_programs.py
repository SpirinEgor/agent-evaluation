"""Author-only v21: simultaneously conditional funding packages; all facts in common corpus."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from random import Random

from bank2_resolution_oracle import compatibility_choices, component_fingerprint
from upgrade_bank2_multisource import sync_manifest
from upgrade_bank2_percentage_programs import (
    OFFER,
    lower_deduction_selection,
)
from upgrade_bank2_percentage_programs import (
    VERSION as PARENT,
)
from upgrade_bank2_percentage_programs import (
    components as parent_components,
)
from upgrade_composite_rewards import action, evidence

VERSION = "2026-10-21-bank2-dependency-programs-v21"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def components():
    rows, witness, alternate = parent_components()
    byid = {r["id"]: r for r in rows}
    chosen = set(witness)
    groups = {i: [f"part_{1100000 + i * 3 + a}" for a in range(3)] for i in range(472)}
    rng = Random(21102029)
    for row in rows:
        row["required_companions"] = []
        own = (int(row["id"].split("_")[1]) - 1100000) // 3
        if own >= 472:
            continue
        for _ in range(3):
            targets = rng.sample([i for i in groups if i != own], 3)
            clause = [rng.choice(groups[i]) for i in targets]
            if row["id"] in chosen and not chosen.intersection(clause):
                j = rng.randrange(3)
                clause[j] = next(x for x in groups[targets[j]] if x in chosen)
            rng.shuffle(clause)
            row["required_companions"].append(clause)
    # A financially identical isolated option violates only a declared companion.
    base = next(
        x for x in witness if byid[x]["exclusive_group"] == "approval_group_percent_473"
    )
    bad = byid["part_1101417"]
    identity = {k: bad[k] for k in ["id", "exclusive_group"]}
    bad.clear()
    bad.update(deepcopy(byid[base]))
    bad.update(identity)
    bad["required_companions"] = [[next(x for x in groups[0] if x not in chosen)]]
    assert all(
        chosen.intersection(c) for x in witness for c in byid[x]["required_companions"]
    )
    return rows, witness, alternate


def dependency_violation_selection():
    rows, witness, _ = components()
    byid = {r["id"]: r for r in rows}
    return sorted(
        "part_1101417"
        if byid[x]["exclusive_group"] == "approval_group_percent_473"
        else x
        for x in witness
    )


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
    offer.update(version=9, components=rows)
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
    path = data / "corpus/knowledge/reward_resolution_amounts.md"
    body = path.read_text().replace(
        "resolution-v7-percentage-awards", "resolution-v8-conditional-awards"
    )
    body += "\n## Зависимые части предложения\n\nПоле required_companions содержит условия совместного выбора. Проверяйте их по\n[правилам зависимых пакетов](reward_resolution_dependencies.md) вместе с\nнесовместимостью, бюджетами, удержаниями и свободными требованиями.\n"
    path.write_text(body)
    metadata = dict(
        id="reward_resolution_dependencies",
        section="Акции и кешбэк",
        title="Зависимые части предложения урегулирования",
        keywords=["урегулирование", "зависимость", "пакет", "совместный выбор"],
        revision_id="dependencies-v1",
        effective_from="2026-06-01",
        effective_to=None,
        document_type="bank-knowledge",
        trust="bank-policy",
    )
    body = (
        "---\n"
        + "\n".join(
            k + ": " + json.dumps(v, ensure_ascii=False) for k, v in metadata.items()
        )
        + "\n---\n\n"
    )
    body += """# Зависимые части предложения урегулирования

required_companions — список условий выбранного компонента. Каждое условие —
список ID компонентов того же предложения: хотя бы один из них должен входить
в окончательный component_ids. Выполняются ВСЕ условия выбранного компонента;
внутри одного условия достаточно ЛЮБОГО одного указанного ID. Например,
[["a", "b"], ["c"]] требует (a или b) и одновременно c.
Пустой внешний список не добавляет условий. Пустое внутреннее условие
невыполнимо; ссылка на неизвестный компонент означает некорректные условия.

Проверяется весь пакет одновременно. Выбранный спутник сам подчиняется своим
условиям, группе, несовместимостям, бюджетам и удержаниям. Циклы допустимы,
если конечный набор удовлетворяет всем условиям; последовательное отдельное
исполнение частей не нужно. Условие не добавляет компонент автоматически,
не отменяет запрет двух частей одной группы и не разрешает превышение бюджета.
Невыбранные компоненты не создают обязательств. Порядок ID в пакете не влияет
на допустимость. Группы по-прежнему можно пропускать, если условия оставшихся
компонентов выполнены. Согласуйте именно окончательный выбранный набор.
"""
    doc = data / "corpus/knowledge/reward_resolution_dependencies.md"
    doc.write_text(body)
    mp = data / "manifest.json"
    manifest = json.loads(mp.read_text())
    amountsha = hashlib.sha256(path.read_bytes()).hexdigest()
    docsha = hashlib.sha256(doc.read_bytes()).hexdigest()
    manifest["documents"]["/knowledge/reward_resolution_amounts.md"]["metadata"][
        "revision_id"
    ] = "resolution-v8-conditional-awards"
    manifest["documents"]["/knowledge/reward_resolution_amounts.md"]["sha256"] = (
        amountsha
    )
    manifest["documents"]["/knowledge/reward_resolution_dependencies.md"] = dict(
        metadata=metadata, sha256=docsha
    )
    mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    e = task["evaluation_criteria"]
    byid = {r["id"]: r for r in rows}
    for item in e["result_evidence"]:
        if item["evidence_id"] == "bank2:v5:offer:" + OFFER:
            item["expected_fields"]["version"] = 9
        if item["evidence_id"].startswith("bank2:v11:component:"):
            ident = item["evidence_id"].split(":")[-1]
            if ident in byid:
                row = deepcopy(byid[ident])
                row["pool_costs"] = {k: str(v) for k, v in row["pool_costs"].items()}
                item["expected_fields"] = row
        if (
            item.get("expected_fields", {}).get("path")
            == "/knowledge/reward_resolution_amounts.md"
        ):
            item["expected_fields"]["sha256"] = amountsha
    for a in e["actions"]:
        args = a["arguments"]
        packet = args.get("request", args)
        if packet.get("offer_id") == OFFER and "offer_version" in packet:
            packet["offer_version"] = 9
    where = next(
        i for i, a in enumerate(e["actions"]) if a["action_id"] == "bank2:v5:consent"
    )
    e["actions"].insert(
        where,
        action(
            "bank2:v21:dependency-source",
            "file_read",
            dict(path="/knowledge/reward_resolution_dependencies.md"),
        ),
    )
    e["result_evidence"].append(
        evidence(
            "bank2:v21:dependency-source",
            ["file_read"],
            "reward_resolution_dependencies",
            dict(
                path="/knowledge/reward_resolution_dependencies.md",
                complete=True,
                sha256=docsha,
            ),
            "bank2:v5:pay",
            only_if_target_attempted=True,
            target_argument_options={"offer_id": [OFFER]},
        )
    )
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
