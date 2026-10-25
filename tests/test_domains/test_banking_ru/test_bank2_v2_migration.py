"""Real migration application only on a temporary subject snapshot."""

import json
import os
import shutil
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))
from build_access_contract import with_contract as access
from sync_hardening_sources import sync
from upgrade_bank2_v2 import VERSION, C, independent_oracle, migrate


def test_bank2_v2_migration_preserves_other_subjects_and_is_idempotent(tmp_path):
    source = Path(
        os.environ.get("BANK2_V2_DATA", "data/tau2/domains/banking_ru")
    ).resolve()
    p = tmp_path / "data"
    shutil.copytree(source, p, copy_function=shutil.copyfile)
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "fixtures/banking_ru/bank2_composite_v1_2026_10_14/parent.json"
        ).read_text()
    )
    tasks = json.loads((p / "tasks.json").read_text())
    db = json.loads((p / "db.json").read_text())
    answers = json.loads((p / "intake_answers.json").read_text())
    for i, t in enumerate(tasks):
        if t["id"] == "Bank 2":
            tasks[i] = fixture["task"]
    for collection, rows in fixture["subject_db"].items():
        db[collection] = {
            k: v for k, v in db[collection].items() if v.get("customer_id") != C
        }
        db[collection].update(rows)
    answers[C] = fixture["answers"]
    for name, value in [
        ("tasks.json", tasks),
        ("db.json", db),
        ("intake_answers.json", answers),
    ]:
        (p / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    before_tasks = deepcopy(tasks)
    before_db = deepcopy(db)
    before_answers = deepcopy(answers)
    updated = migrate(p)
    new_tasks, new_db, new_answers = updated
    assert [t for t in before_tasks if t["id"] != "Bank 2"] == [
        t for t in new_tasks if t["id"] != "Bank 2"
    ]
    for collection, values in before_db.items():
        if isinstance(values, dict):
            for key, value in values.items():
                if isinstance(value, dict) and value.get("customer_id") == C:
                    continue
                assert new_db[collection][key] == value, (collection, key)
        else:
            assert new_db[collection] == values
    assert {k: v for k, v in before_answers.items() if k != C} == {
        k: v for k, v in new_answers.items() if k != C
    }
    assert (
        next(t for t in new_tasks if t["id"] == "Bank 2")["scenario_version"] == VERSION
    )
    expected = independent_oracle(new_db)
    assert (
        expected["payout"] == 1200
        and expected["claim"] == 38
        and expected["increase"] == 28
    )
    for name, value in zip(["tasks.json", "db.json", "intake_answers.json"], updated):
        (p / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    assert migrate(p) == updated
    # The base migration still produces neutral v2. For the mixed-request
    # release, compose the separate authorized edit before comparing releases.
    source_task = next(
        t
        for t in json.loads((source / "tasks.json").read_text())
        if t["id"] == "Bank 2"
    )
    if "bank2-v2-mixed-request" in source_task["description"]["notes"]:
        from strengthen_bank2_mixed_request import apply

        monetary_before = deepcopy(new_db)
        apply(p)
        new_tasks = json.loads((p / "tasks.json").read_text())
        new_db = json.loads((p / "db.json").read_text())
        assert {k: v for k, v in monetary_before.items() if k != "documents"} == {
            k: v for k, v in new_db.items() if k != "documents"
        }
        applied = [
            (p / name).read_bytes()
            for name in ["db.json", "tasks.json", "intake_answers.json"]
        ]
        apply(p)
        assert applied == [
            (p / name).read_bytes()
            for name in ["db.json", "tasks.json", "intake_answers.json"]
        ]
    access(new_tasks, new_db)
    (p / "tasks.json").write_text(
        json.dumps(new_tasks, ensure_ascii=False, indent=2) + "\n"
    )
    sync(p)
    assert json.loads((p / "db.json").read_text()) == json.loads(
        (source / "db.json").read_text()
    )
    assert json.loads((p / "intake_answers.json").read_text()) == json.loads(
        (source / "intake_answers.json").read_text()
    )
    assert json.loads((p / "tasks.json").read_text()) == json.loads(
        (source / "tasks.json").read_text()
    )
