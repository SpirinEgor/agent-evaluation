"""Explicit Bank41 N40 source equivalence; no agent environment mutation."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def with_contract(tasks, data_dir=DATA):
    """Accept kb320's exact complete operative rules or the rewards policy."""
    updated = deepcopy(tasks)
    selected = [t for t in updated if t["id"] == "Bank 41"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 41")
    task = selected[0]
    evidence = task["evaluation_criteria"]["result_evidence"]
    operative = next(
        e for e in evidence if e["evidence_id"] == "bounded-source:kb_320:Bank 41"
    )
    reward = next(e for e in evidence if e["evidence_id"] == "bank41:reward-policy")
    alternative = {
        k: deepcopy(operative[k])
        for k in ["tool_names", "record_id", "expected_fields"]
    }
    alternative["expected_fields"]["content"] = (
        data_dir / "corpus/knowledge/kb_320.md"
    ).read_text()
    reward["alternatives"] = [alternative]
    task["description"]["relevant_policies"] = (
        "/policies/general.md; /policies/identity.md; /policies/fees_rewards.md либо полный текущий /knowledge/kb_320.md; /knowledge/kb_320.md–kb_333.md (либо полные сборники); /knowledge/processing_calendar.md"
    )
    note = " Bank41: полные полученные правила kb_320 равнозначны fees_rewards для консультации N40; навигационный обзор сам по себе недостаточен."
    if note not in task["description"]["notes"]:
        task["description"]["notes"] += note
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = args.data_dir / "tasks.json"
    before = json.loads(path.read_text())
    after = with_contract(before, args.data_dir)
    if args.check and before != after:
        raise SystemExit("Bank41 reward source contract stale")
    if not args.check:
        path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n")
    print("Bank41 reward source contract current")


if __name__ == "__main__":
    main()
