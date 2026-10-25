"""Historical tests own their complete input and reject changed fixture bytes."""

import hashlib
from pathlib import Path

import pytest

from tau2.domains.banking_ru import utils


def tree_hash(path):
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.rglob("*")
        if p.is_file()
    }


def test_historical_copy_does_not_read_current_dataset(tmp_path, monkeypatch):
    from tests.utils.banking_parent_fixture import copy_historical_data

    before = copy_historical_data("fees_rewards_parent_2026_10_14", tmp_path / "before")
    monkeypatch.setattr(utils, "BANKING_RU_DATA_DIR", tmp_path / "missing-current")
    after = copy_historical_data("fees_rewards_parent_2026_10_14", tmp_path / "after")
    assert tree_hash(before) == tree_hash(after)
    from tau2.domains.banking_ru.file_corpus import FileCorpus

    assert FileCorpus(after).documents


def test_historical_fixture_rejects_tampering(tmp_path):
    from tests.utils.banking_parent_fixture import copy_historical_data, verify_fixture

    path = copy_historical_data("fees_rewards_parent_2026_10_14", tmp_path / "copy")
    (path / "db.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_fixture(path)


def test_historical_subject_overlay_rejects_tampering(tmp_path):
    from tests.utils.banking_parent_fixture import read_parent

    fixture = tmp_path / "subject"
    fixture.mkdir()
    (fixture / "parent.json").write_text("{}")
    (fixture / "integrity.json").write_text('{"parent.json": "wrong"}')
    with pytest.raises(ValueError, match="hash mismatch"):
        read_parent(fixture)


def test_settlement_mechanism_never_opens_current_dataset(tmp_path, monkeypatch):
    from tau2.domains.banking_ru import environment, file_pilot
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import ready

    for module in (utils, environment, file_pilot):
        for name, value in (
            ("BANKING_RU_DATA_DIR", tmp_path / "missing"),
            ("BANKING_RU_DB_PATH", tmp_path / "missing/db.json"),
        ):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    env = ready()
    assert env.tools.db.transactions["txn_552290"].customer_id == "gromov_a_1187"
    assert env.tools.corpus.documents


def test_basic_inventory_is_explicit_and_preserves_shared_mechanisms():
    import importlib.util

    path = Path(__file__).resolve().parents[3] / "scripts/banking_ru/run_basic_tests.py"
    spec = importlib.util.spec_from_file_location("basic_inventory", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    names = {Path(p).name for p in runner.GROUPS["regressions"]}
    assert {
        "test_historical_fixture_integrity.py",
        "test_reward_workflow_protocols.py",
        "test_resolution_dependencies.py",
        "test_contract_infrastructure.py",
    } <= names
    assert "test_bank2_weighted_programs.py" not in names
    assert all(
        (runner.ROOT / node).is_file()
        for group in runner.GROUPS.values()
        for node in group
    )
    assert ".glob(" not in path.read_text()
