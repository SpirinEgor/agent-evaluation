"""Standalone banking test data cannot depend on the live benchmark corpus."""

import importlib.util
import shutil
from pathlib import Path

import pytest

from tau2.domains.banking_ru import utils
from tau2.domains.banking_ru.corpus_manifest import load_manifest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/banking_ru"


@pytest.mark.parametrize(
    "name", ["operation_api", "investigation_api", "settlement_api", "reward_api"]
)
def test_standalone_fixture_does_not_read_current_dataset(name, tmp_path, monkeypatch):
    copy = Path(shutil.copytree(FIXTURES / name, tmp_path / name))
    monkeypatch.setattr(utils, "BANKING_RU_DATA_DIR", tmp_path / "missing-current")
    assert load_manifest(copy, verify=True)["documents"]


@pytest.mark.parametrize(
    "name", ["operation_api", "investigation_api", "settlement_api", "reward_api"]
)
@pytest.mark.parametrize(
    "target", ["db.json", "intake_answers.json", "corpus/AGENTS.md"]
)
def test_standalone_fixture_rejects_tampering(name, target, tmp_path):
    copy = Path(shutil.copytree(FIXTURES / name, tmp_path / name))
    (copy / target).write_text("corrupted")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_manifest(copy, verify=True)


def test_settlement_mechanism_never_opens_current_dataset(tmp_path, monkeypatch):
    from tau2.domains.banking_ru import environment, file_pilot
    from tests.utils.banking_settlement_fixture import ready

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
    path = Path(__file__).resolve().parents[3] / "scripts/banking_ru/run_basic_tests.py"
    spec = importlib.util.spec_from_file_location("basic_inventory", path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    names = {Path(p).name for p in runner.GROUPS["regressions"]}
    assert {
        "test_fixture_integrity.py",
        "test_reward_workflow_protocols.py",
        "test_resolution_dependencies.py",
        "test_contract_infrastructure.py",
    } <= names
    assert all(
        (runner.ROOT / node).is_file()
        for group in runner.GROUPS.values()
        for node in group
    )
    assert ".glob(" not in path.read_text()


def test_api_fixture_cannot_load_live_benchmark_tasks(operation_api_scenarios):
    from tau2.domains.banking_ru.environment import get_tasks

    with pytest.raises(FileNotFoundError):
        get_tasks()


@pytest.mark.parametrize("change", ["add", "delete"])
def test_fixture_rejects_changed_corpus_inventory(change, tmp_path):
    copy = Path(shutil.copytree(FIXTURES / "operation_api", tmp_path / "fixture"))
    if change == "add":
        (copy / "corpus/extra.md").write_text("unexpected")
    else:
        (copy / "corpus/AGENTS.md").unlink()
    with pytest.raises(ValueError, match="hash mismatch"):
        load_manifest(copy, verify=True)
