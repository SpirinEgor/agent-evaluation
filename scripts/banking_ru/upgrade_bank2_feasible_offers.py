"""Bank2 v7: maximize an executable offer while preserving existing claims."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from bank2_multisource_oracle import A
from build_access_contract import with_contract as access
from build_discovery_contract import with_contract as discovery
from upgrade_bank2_history import VERSION as PARENT
from upgrade_bank2_history import configure
from upgrade_bank2_multisource import sync_manifest

VERSION = "2026-10-20-bank2-feasible-offers-v7"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def migrate(data):
    """Apply to a temporary v6 dataset; do not remove or transfer old claims."""
    paths = [data / x for x in ["db.json", "tasks.json", "intake_answers.json"]]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    template = db["reward_resolution_offers"]["offer_610723"]
    for ident, amount, basis, first in [
        ("offer_620151", 100, "cumulative", 15),
        ("offer_620153", 25, "incremental", 14.38),
        ("offer_620157", 24.25, "incremental", 11),
        ("offer_620159", 96.25, "cumulative", 11),
        ("offer_620163", 96.24, "cumulative", 11),
        ("offer_620167", 24.50, "incremental", 14.50),
    ]:
        if ident in db["reward_resolution_offers"]:
            raise ValueError("Offer ID collision: " + ident)
        db["reward_resolution_offers"][ident] = {
            **deepcopy(template),
            "id": ident,
            "amount": amount,
            "amount_basis": basis,
            "allocation_first_transaction_id": A,
            "allocation_first_amount": first,
        }
    path = "/knowledge/reward_resolution_amounts.md"
    file = data / "corpus" / path.lstrip("/")
    body = file.read_text().replace(
        'revision_id: "resolution-v1"', 'revision_id: "resolution-v2-feasibility"'
    )
    body += (
        "\n\n## Исполнимость распределения\n\n"
        "Одобрение предложения не резервирует деньги и не отменяет уже заявленные "
        "требования. Перед выбором максимума проверьте каждую позицию фиксированного "
        "распределения: новая выплата по покупке не должна превышать её текущую "
        "недоплату за вычетом сумм этой покупки во всех открытых делах. Не уменьшайте "
        "и не переносите ранее заявленную сумму ради принятия более крупного "
        "предложения. Нельзя самостоятельно менять распределение банка. "
        "Сравнивайте дополнительные рубли только исполнимых разрешённых вариантов.\n"
    )
    file.write_text(body)
    manifest = json.loads((data / "manifest.json").read_text())
    descriptor = manifest["documents"][path]
    descriptor["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    descriptor["metadata"]["revision_id"] = "resolution-v2-feasibility"
    (data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    configure(task, db, answers, data)
    task["scenario_version"] = VERSION
    task["description"]["purpose"] = (
        "Выбрать максимум исполнимой доплаты, учитывая фиксированное распределение и ранее заявленные требования."
    )
    task["description"]["notes"] = (
        VERSION + "; completion_only; difficulty not yet measured"
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
        raise ValueError("Use a temporary dataset first")
    migrate(args.data_dir)
