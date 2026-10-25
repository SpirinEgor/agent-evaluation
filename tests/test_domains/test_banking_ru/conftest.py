"""Versioned parent fixtures keep historical probes separate from live scenarios."""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fees_rewards_parent_data(tmp_path_factory):
    from tests.utils.banking_parent_fixture import copy_historical_data

    return copy_historical_data(
        "fees_rewards_parent_2026_10_14",
        tmp_path_factory.mktemp("fees-rewards-parent") / "banking_ru",
    )


@pytest.fixture
def fees_rewards_parent_scenarios(fees_rewards_parent_data, monkeypatch):
    from tau2.domains.banking_ru import environment, file_pilot, utils

    p = fees_rewards_parent_data
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)


@pytest.fixture
def bank2_composite_v1_scenarios(tmp_path, monkeypatch):
    """Retain old Bank2 probes explicitly; current v2 has a separate full grader suite."""
    import json
    import sys
    from copy import deepcopy

    from tau2.domains.banking_ru import environment, file_pilot, utils

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))
    from build_access_contract import with_contract as access
    from sync_hardening_sources import sync

    fixture = (
        Path(__file__).resolve().parents[2]
        / "fixtures/banking_ru/bank2_composite_v1_2026_10_14/parent.json"
    )
    from tests.utils.banking_parent_fixture import read_parent

    parent = read_parent(fixture.parent)
    c = "gromov_a_1187"
    p = tmp_path / "composite-v1"
    from tests.utils.banking_parent_fixture import copy_historical_data

    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    tasks = json.loads((p / "tasks.json").read_text())
    db = json.loads((p / "db.json").read_text())
    answers = json.loads((p / "intake_answers.json").read_text())
    manifest = json.loads((p / "manifest.json").read_text())
    for i, t in enumerate(tasks):
        if t["id"] == "Bank 2":
            tasks[i] = deepcopy(parent["task"])
    for collection, rows in parent["subject_db"].items():
        db[collection] = {
            k: v for k, v in db[collection].items() if v.get("customer_id") != c
        }
        db[collection].update(rows)
    answers[c] = parent["answers"]
    for path, body in parent["source_files"].items():
        (p / "corpus" / path.lstrip("/")).write_text(body)
        manifest["documents"][path] = parent["source_descriptors"][path]
    access(tasks, db)
    for name, value in [
        ("tasks.json", tasks),
        ("db.json", db),
        ("intake_answers.json", answers),
        ("manifest.json", manifest),
    ]:
        (p / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    sync(p)
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)


@pytest.fixture
def bank2_v4_data(tmp_path):
    """Historical v4 task/source overlay with isolated subject rows."""
    import hashlib
    import json

    from tests.utils.banking_parent_fixture import FIXTURES, read_parent

    parent = read_parent(FIXTURES / "bank2_multisource_v4_2026_10_15")
    p = tmp_path / "v4-parent"
    from tests.utils.banking_parent_fixture import copy_historical_data

    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    restore_bank2_v5(p)
    tasks = json.loads((p / "tasks.json").read_text())
    tasks = [parent["task"] if t["id"] == "Bank 2" else t for t in tasks]
    (p / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n"
    )
    manifest = json.loads((p / "manifest.json").read_text())
    for name, body in parent["source_files"].items():
        (p / "corpus" / name.lstrip("/")).write_text(body)
        manifest["documents"][name] = parent["source_descriptors"][name]
    # Additive offer records do not affect v4, but must not count as its dataset.
    db = json.loads((p / "db.json").read_text())
    for name in (
        "reward_resolution_offers",
        "reward_resolution_preferences",
        "reward_resolution_executions",
    ):
        db.pop(name, None)
    (p / "db.json").write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n")
    manifest["intake_answers_sha256"] = hashlib.sha256(
        (p / "intake_answers.json").read_bytes()
    ).hexdigest()
    (p / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))
    from upgrade_bank2_multisource import sync_manifest

    sync_manifest(p)
    return p
