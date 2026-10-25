"""Correct the author-only source contract without changing the agent environment."""

import argparse
import json
from pathlib import Path

from upgrade_bank2_large_selection import VERSION as PARENT
from upgrade_bank2_multisource import sync_manifest, with_promotion_source_alternatives

VERSION = "2026-10-21-bank2-large-selection-v10r"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def migrate(data):
    path = data / "tasks.json"
    tasks = json.loads(path.read_text())
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return tasks
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    with_promotion_source_alternatives(
        task, json.loads((data / "db.json").read_text()), data
    )
    rules = [
        r
        for r in task["evaluation_criteria"]["result_evidence"]
        if r["evidence_id"].startswith("bank2:v4:offer:")
    ]
    if not rules or not all(r.get("alternatives") for r in rules):
        raise ValueError("This edition does not supply the current bank offer terms")
    task["scenario_version"] = VERSION
    task["description"]["notes"] = (
        VERSION
        + "; source alternative correction; original model trajectory solved the subject"
    )
    path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
    return tasks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir.resolve() == DATA.resolve():
        raise ValueError("Use temporary data first")
    migrate(args.data_dir)
