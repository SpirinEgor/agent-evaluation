"""The workflow fixture is self-contained and uses the canonical hash format."""

import json
from pathlib import Path

from tau2.domains.banking_ru.corpus_manifest import COMPACT_KEYS, load_manifest


def test_reward_api_fixture_is_standalone():
    root = Path(__file__).resolve().parents[2] / "fixtures/banking_ru/reward_api"
    raw = json.loads((root / "manifest.json").read_text())
    assert set(raw) == COMPACT_KEYS
    assert load_manifest(root, verify=True)["documents"]
    assert not (root / "tasks.json").exists()


def test_operation_fixture_is_standalone():
    root = Path(__file__).resolve().parents[2] / "fixtures/banking_ru/operation_api"
    raw = json.loads((root / "manifest.json").read_text())
    assert set(raw) == COMPACT_KEYS
    assert load_manifest(root, verify=True)["documents"]
    assert not (root / "tasks.json").exists()


def test_investigation_fixture_is_standalone():
    root = Path(__file__).resolve().parents[2] / "fixtures/banking_ru/investigation_api"
    raw = json.loads((root / "manifest.json").read_text())
    assert set(raw) == COMPACT_KEYS
    assert load_manifest(root, verify=True)["documents"]
    assert not (root / "tasks.json").exists()


def test_settlement_fixture_is_standalone():
    root = Path(__file__).resolve().parents[2] / "fixtures/banking_ru/settlement_api"
    raw = json.loads((root / "manifest.json").read_text())
    assert set(raw) == COMPACT_KEYS
    assert load_manifest(root, verify=True)["documents"]
    assert not (root / "tasks.json").exists()
