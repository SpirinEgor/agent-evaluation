"""Scoped Bank32 repair: deposit payout investigation is not account closure."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from tau2.domains.banking_ru.file_corpus import FileCorpus

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def with_contract(tasks, data_dir=DATA):
    updated = deepcopy(tasks)
    selected = [t for t in updated if t["id"] == "Bank 32"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 32")
    task = selected[0]
    criteria = task["evaluation_criteria"]
    for action in criteria["actions"]:
        if action["action_id"] == "read_process_accounts_Bank 32":
            action["action_id"] = "read_process_deposits_Bank 32"
            action["arguments"]["path"] = "/policies/deposits.md"
    receipt = FileCorpus(data_dir).read("/policies/deposits.md")
    old_id = "process-source:Bank 32:bank-policy-accounts:create_case_Bank 32"
    new_id = "process-source:Bank 32:bank-policy-deposits:create_case_Bank 32"
    source = next(
        e for e in criteria["result_evidence"] if e["evidence_id"] in {old_id, new_id}
    )
    source["evidence_id"] = new_id
    source["record_id"] = receipt["id"]
    source["expected_fields"] = {
        key: receipt[key] for key in ("path", "sha256", "complete", "content")
    }
    task["description"]["relevant_policies"] = (
        "/policies/general.md; /policies/identity.md; /policies/deposits.md; "
        "/knowledge/kb_350.md (связанный порядок обращений: /knowledge/kb_110.md)"
    )
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = args.data_dir / "tasks.json"
    before = json.loads(path.read_text())
    after = with_contract(before, args.data_dir)
    if args.apply:
        path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n")
    elif before != after:
        raise SystemExit("Bank32 source contract stale")
    print("Bank32 source contract current")


if __name__ == "__main__":
    main()
