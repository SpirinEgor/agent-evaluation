"""Remove redundant document obligations only for explicitly assigned case IDs.

Required complete alternative articles, actual receipts and all protections stay
in the task contracts. This does not claim whole-file receipt for a subset.
"""

import argparse
import json
from copy import deepcopy
from pathlib import Path

REMOVED = {
    "Bank 20": {"/policies/disputes.md"},
    "Bank 34": {"/policies/loans.md"},
    "Bank 35": {"/policies/loans.md"},
    "Bank 41": {"/knowledge/kb_final_41.md"},
    "Bank 53": {"/policies/deposits.md", "/policies/loans.md"},
}


def with_contract(tasks):
    """Return an idempotent scoped migration with unchanged nonselected tasks."""
    result = deepcopy(tasks)
    for task in result:
        paths = REMOVED.get(task["id"])
        if paths is None:
            continue
        c = task["evaluation_criteria"]
        if task["id"] == "Bank 34":
            c["result_evidence"] = [
                e
                for e in c["result_evidence"]
                if e.get("expected_fields", {}).get("path") not in paths
            ]
            continue
        required = {
            "Bank 20": "/policies/general.md; /policies/identity.md; /policies/transfers_fraud.md; /knowledge/processing_calendar.md",
            "Bank 35": "/policies/general.md; /policies/identity.md; /knowledge/kb_168.md; /knowledge/kb_345.md",
            "Bank 41": "/policies/general.md; /policies/identity.md; /policies/fees_rewards.md либо полный текущий /knowledge/kb_320.md; /knowledge/kb_320.md–kb_333.md (либо полные сборники); /knowledge/processing_calendar.md",
            "Bank 53": "/policies/general.md; /policies/identity.md; /knowledge/kb_340.md–kb_349.md (либо полные сборники)",
        }
        task["description"]["relevant_policies"] = required[task["id"]]
        note = " Источники18: необязательные дублирующие регламенты и навигационные обзоры не заменяют обязательные полные правила; полный сборник текущих текстов равноценен отдельным статьям."
        if note not in task["description"]["notes"]:
            task["description"]["notes"] += note
        c["result_evidence"] = [
            e
            for e in c["result_evidence"]
            if e.get("expected_fields", {}).get("path") not in paths
        ]
        for path in paths:
            if path.startswith("/policies/"):
                c.setdefault("process_source_targets", {})[path] = []
        # Keep optional reads in the reference, so historical routes still work.
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = args.data_dir / "tasks.json"
    tasks = json.loads(path.read_text())
    updated = with_contract(tasks)
    if args.apply:
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    elif updated != tasks:
        raise SystemExit("Sources18 task contracts are stale")
    print("sources18 scoped contracts: current")


if __name__ == "__main__":
    main()
