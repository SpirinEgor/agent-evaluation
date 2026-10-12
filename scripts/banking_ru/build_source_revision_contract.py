"""Remove whole-corpus grading pins from the five affected banking tasks only."""

import argparse
import fcntl
import json
import os
from copy import deepcopy
from pathlib import Path

TASK_IDS = frozenset({"Bank 1", "Bank 2", "Bank 3", "Bank 5", "Bank 7"})
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def with_contract(tasks):
    """Keep document facts and all unrelated tasks/fields exactly unchanged."""
    updated = deepcopy(tasks)
    selected = [task for task in updated if task["id"] in TASK_IDS]
    if {task["id"] for task in selected} != TASK_IDS or len(selected) != 5:
        raise ValueError("Expected exactly one task for each affected ID")
    for task in selected:
        for evidence in task["evaluation_criteria"].get("result_evidence", []):
            if "file_read" in evidence["tool_names"]:
                evidence["expected_fields"].pop("snapshot_sha256", None)
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = args.data_dir / "tasks.json"
    with path.with_name(path.name + ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = path.read_bytes()
        original = json.loads(before)
        updated = with_contract(original)
        if updated != original:
            if args.check:
                raise SystemExit("Bank 1/2/3/5/7 source revision contract is stale")
            if path.read_bytes() != before:
                raise RuntimeError("Concurrent tasks.json change; refusing overwrite")
            temporary = path.with_name(
                path.name + f".{os.getpid()}.source-revision.tmp"
            )
            temporary.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
            )
            os.replace(temporary, path)
        print("Bank 1/2/3/5/7 document revision contract is current")


if __name__ == "__main__":
    main()
