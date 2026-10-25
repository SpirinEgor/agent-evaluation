"""N40 source equivalence preserves separate RO-09 authority for CRM intake."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def with_contract(tasks, data_dir=DATA):
    """N40 knowledge equivalence must not replace CRM authority in RO-09."""
    updated = deepcopy(tasks)
    selected = [t for t in updated if t["id"] == "Bank 41"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 41")
    task = selected[0]
    actions = task["evaluation_criteria"]["actions"]
    if not any(
        a["name"] in {"create_cashback_investigation", "amend_cashback_investigation"}
        for a in actions
    ):
        raise ValueError("Historical consultation-only Bank41 is not supported")
    evidence = task["evaluation_criteria"]["result_evidence"]
    reward = next(e for e in evidence if e["evidence_id"] == "bank41:reward-policy")
    reward.pop("alternatives", None)
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
