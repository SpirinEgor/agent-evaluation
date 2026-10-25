"""Migration integrity and independent money oracle; no model calls."""

import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def module(name):
    import sys

    sys.path.insert(0, str(ROOT / "scripts/banking_ru"))
    return __import__(name)


@pytest.fixture
def upgraded(tmp_path, bank2_v4_data):
    data = bank2_v4_data
    shutil.copytree(data, tmp_path / "data")
    (tmp_path / "original-tasks.json").write_bytes((data / "tasks.json").read_bytes())
    migration = module("upgrade_bank2_multisource")
    migration.migrate(tmp_path / "data")
    return tmp_path / "data", migration


def test_v4_migration_idempotent_preserves_other_tasks(upgraded):
    data, migration = upgraded
    before = {p.name: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    migration.migrate(data)
    after = {p.name: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    assert after == before
    tasks = json.loads((data / "tasks.json").read_text())
    original = json.loads((data.parent / "original-tasks.json").read_text())
    assert [t for t in tasks if t["id"] != "Bank 2"] == [
        t for t in original if t["id"] != "Bank 2"
    ]


def test_independent_oracle_all_variants(upgraded):
    data, _ = upgraded
    oracle = module("bank2_multisource_oracle")
    db = json.loads((data / "db.json").read_text())
    expected = [
        ("main", 1200, 7300),
        ("late_activation", 1200, 2000),
        ("without_compensation", 1200, 7800),
        ("without_authority", 0, 8500),
        ("select_other_receipt", 1200, 3500),
    ]
    for name, payout, total in expected:
        variant, selected = oracle.variant(db, name)
        result = oracle.calculate(variant, selected=selected)
        assert (result["manual_payment"], result["case_total"]) == (payout, total)
    result = oracle.calculate(db)
    assert [result["rows"][x]["entitlement"] for x in oracle.PURCHASES] == [
        3000,
        1000,
        5000,
        10000,
        2000,
    ]
    assert result["balance_delta"] == 1900


def test_rejects_unsupported_version_before_writing(upgraded):
    data, migration = upgraded
    tasks = json.loads((data / "tasks.json").read_text())
    next(t for t in tasks if t["id"] == "Bank 2")["scenario_version"] = "unsupported"
    (data / "tasks.json").write_text(json.dumps(tasks))
    before = (data / "db.json").read_bytes()
    with pytest.raises(ValueError, match="version"):
        migration.migrate(data)
    assert (data / "db.json").read_bytes() == before


@pytest.mark.parametrize(
    "parent_version", ["2026-10-14-bank2-multisource-v4", "unsupported-future-version"]
)
def test_legacy_migrations_do_not_rollback_v4(upgraded, parent_version):
    data, _ = upgraded
    tasks = json.loads((data / "tasks.json").read_text())
    db = json.loads((data / "db.json").read_text())
    next(t for t in tasks if t["id"] == "Bank 2")["scenario_version"] = parent_version
    (data / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n"
    )
    with pytest.raises(ValueError, match="version"):
        module("upgrade_bank2_authority").migrate(tasks, db)
    with pytest.raises(ValueError, match="version"):
        module("upgrade_bank2_investigation").with_contract(tasks, db)
    with pytest.raises(ValueError, match="version"):
        module("upgrade_composite_rewards").migrate(data)
    with pytest.raises(ValueError, match="version"):
        module("upgrade_bank2_v2").migrate(data)
    with pytest.raises(ValueError, match="version"):
        module("upgrade_bank2_settlement").migrate(data)


def test_model_sources_independent_and_no_answers(upgraded):
    data, migration = upgraded
    manifest = json.loads((data / "manifest.json").read_text())
    for ident in migration.DOCS:
        path = "/knowledge/" + ident + ".md"
        assert path in manifest["documents"]
        content = (data / "corpus" / path.lstrip("/")).read_text()
        assert "gromov_a_1187" not in content and "txn_804" not in content
        assert "case_610903" not in content and "73 ₽" not in content
        assert len(content) < 5000
    for ident in ["kb_standard_cashback_v2", "kb_cashback_adjustment_authority"]:
        entry = manifest["documents"]["/knowledge/" + ident + ".md"]["metadata"]
        assert not any(
            "reward_" in str(v)
            for k, v in entry.items()
            if k in ("source_paths", "included_documents", "equivalent_documents")
        )
