"""Author-only correction: accept the bank's exact historical MCC mapping.

The independent rate-history, partner-terms, activation and contract receipts
remain mandatory. Together they contain all facts in the category document.
"""

import argparse
import json
from copy import deepcopy
from pathlib import Path

VERSION = "2026-10-21-bank2-category-sources-v16r"
PARENT = "2026-10-21-bank2-split-programs-v16"


def apply_category_sources(tasks, db):
    task = next(t for t in tasks if t["id"] == "Bank 2")
    row = next(
        r
        for r in db["cashback_rules"]["gromov_a_1187"]["categories_history"]
        if r["id"] == "cafe-rate-july"
    )
    source = dict(
        tool_names=["get_cashback_rules"],
        record_id=row["id"],
        expected_fields=deepcopy(row),
    )
    changed = []
    for receipt in task["evaluation_criteria"]["result_evidence"]:
        if "doc:reward_merchant_categories:" in receipt["evidence_id"]:
            receipt["alternatives"] = [deepcopy(source)]
            changed.append(receipt["evidence_id"])
    assert len(changed) == 2
    return changed


def migrate(data):
    db = json.loads((data / "db.json").read_text())
    tasks = json.loads((data / "tasks.json").read_text())
    task = next(t for t in tasks if t["id"] == "Bank 2")
    assert task["scenario_version"] in [PARENT, VERSION]
    changed = apply_category_sources(tasks, db)
    task["scenario_version"] = VERSION
    task["description"]["notes"] = (
        VERSION + "; exact alternative category source; visible environment unchanged"
    )
    (data / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n"
    )
    return changed


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, required=True)
    a = p.parse_args()
    canonical = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
    assert a.data_dir.resolve() != canonical.resolve(), "Use temporary candidate first"
    print(migrate(a.data_dir))
