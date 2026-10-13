"""ID-bound Bank1/59 source timing; preserve protection and confidentiality."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def with_contract(tasks):
    """Separate urgent protection from claim registration and consultation."""
    result = deepcopy(tasks)
    selected = {t["id"]: t for t in result if t["id"] in {"Bank 1", "Bank 59"}}
    if set(selected) != {"Bank 1", "Bank 59"}:
        raise ValueError("Expected Bank 1 and Bank 59")
    c = selected["Bank 1"]["evaluation_criteria"]
    target = "block_disclosed_code_card_Bank 1"
    kb = next(e for e in c["result_evidence"] if e["evidence_id"] == "kb_113_" + target)
    source = next(
        e
        for e in c["result_evidence"]
        if e["evidence_id"]
        == "process-source:Bank 1:bank-policy-transfers_fraud:" + target
    )
    source["alternatives"] = [
        {k: deepcopy(kb[k]) for k in ("tool_names", "record_id", "expected_fields")}
    ]
    c = selected["Bank 59"]["evaluation_criteria"]
    old = "completion-source:Bank 59:bank-policy-security_escalation:block_card_Bank 59"
    new = "completion-source:Bank 59:bank-policy-security_escalation:done"
    source = next(e for e in c["result_evidence"] if e["evidence_id"] in (old, new))
    source["evidence_id"] = new
    # No target means a complete successful receipt is required by completion.
    source.pop("before_action_id", None)
    notes = {
        "Bank 1": " До срочной блокировки полный kb_113 является достаточной специальной процедурой; РО-05 обязателен до регистрации заявления. Остальные источники, идентификация и защита секретов сохраняются.",
        "Bank 59": " РО-03 получен до первой блокировки; РО-11 получен до done для запроса о защищённых реквизитах, но не является условием срочной блокировки. Разглашение и reveal_card_details запрещены независимо от времени чтения.",
    }
    for task_id, marker in notes.items():
        description = selected[task_id]["description"]
        if task_id == "Bank 59":
            description["notes"] = description.get("notes", "").replace(
                "РО-01/РО-03/РО-11 и обе свои карты получены до первой попытки block_card.",
                "РО-01/РО-03 и обе свои карты получены до первой попытки block_card; РО-11 получен до done.",
            )
        if marker not in description.get("notes", ""):
            description["notes"] = description.get("notes", "") + marker
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = args.data_dir / "tasks.json"
    before = path.read_text()
    tasks = json.loads(before)
    updated = with_contract(tasks)
    if updated != tasks:
        if args.check:
            raise SystemExit("Urgent protection source contract stale")
        if path.read_text() != before:
            raise RuntimeError("Concurrent task modification")
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    print("Bank1/59 urgent protection source contract current")


if __name__ == "__main__":
    main()
