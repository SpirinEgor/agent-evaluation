"""Pipeline gates must never turn broken validation or isolation into model difficulty."""

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "banking_prototype", ROOT / "scripts/banking_ru/prototype.py"
)


def module():
    value = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(value)
    return value


def grade(path, reward):
    value = {
        "reward": reward,
        "reward_basis": ["DB", "ENV_ASSERTION", "ACTION"],
        "reward_breakdown": {"DB": reward, "ENV_ASSERTION": 1, "ACTION": 1},
    }
    with gzip.open(path, "wt") as stream:
        json.dump({"half": value, "full": value}, stream)


def test_control_cannot_pass_just_because_command_exited_zero(tmp_path):
    p = tmp_path / "reference.json.gz"
    grade(p, 0)
    with pytest.raises(ValueError):
        module().check_control(p, 1)


def test_control_accepts_product_zero_without_requiring_each_factor_zero(tmp_path):
    p = tmp_path / "noop.json.gz"
    grade(p, 0)
    module().check_control(p, 0)


def test_control_checks_both_modes_and_all_three_components(tmp_path):
    p = tmp_path / "reference.json.gz"
    grade(p, 1)
    module().check_control(p, 1)
    with gzip.open(p, "rt") as stream:
        value = json.load(stream)
    value["full"]["reward_breakdown"]["ACTION"] = 0
    with gzip.open(p, "wt") as stream:
        json.dump(value, stream)
    with pytest.raises(ValueError):
        module().check_control(p, 1)


def test_input_mutation_invalidates_cached_gate(tmp_path):
    p = tmp_path / "input"
    p.write_text("first")
    original = module().hash_files([p])
    p.write_text("second")
    assert original != module().hash_files([p])


def test_fingerprint_rejects_symlink_inputs(tmp_path):
    p = tmp_path / "input"
    p.write_text("first")
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(ValueError):
        module().hash_files([link])


def test_leak_scan_blocks_author_solution_but_allows_business_rules():
    m = module()
    assert (
        m.leak_hits({"knowledge": "Лимит57.12, правило округления и документы"}, [])
        == []
    )
    assert m.leak_hits(
        {"startup": "Read /artifacts/solution-contracts/Bank-2/Solution Contract.md"},
        [],
    )
    assert m.leak_hits({"schema": "AUTHOR_ONLY_49338"}, ["AUTHOR_ONLY_49338"])


def test_no_claim_of_difficulty_from_timeout_or_unreviewed_zero():
    m = module()
    assert m.decision([{"outcome": "technical_failure"}]) == "inconclusive"
    assert m.decision([{"outcome": "unreviewed_zero"}] * 3) == "audit_required"
    assert m.decision([{"outcome": "substantive_failure"}] * 3) == "confirmation_ready"


def test_one_success_overrides_selected_failures():
    assert (
        module().decision(
            [{"outcome": "substantive_failure"}] * 3 + [{"outcome": "success"}]
        )
        == "retire_candidate"
    )


def test_codex_refusal_counts_as_failure_but_needs_audit():
    assert (
        module().record_outcome({"confirmed_external_refusal": True, "final_reward": 0})
        == "unreviewed_zero"
    )


def test_harness_cleanup_and_timeouts_do_not_become_substantive_failures():
    m = module()
    assert (
        m.record_outcome({"technical_failure": True, "final_reward": 0})
        == "technical_failure"
    )
    assert (
        m.record_outcome({"final_reward": 0, "state": {"status": "done"}})
        == "unreviewed_zero"
    )
    assert (
        m.record_outcome({"final_reward": 1, "state": {"status": "done"}}) == "success"
    )


def test_repeat_requires_independent_review_and_stops_after_success():
    m = module()
    with pytest.raises(ValueError):
        m.allow_attempt([{"outcome": "unreviewed_zero"}], 3)
    with pytest.raises(ValueError):
        m.allow_attempt([{"outcome": "success"}], 3)
    with pytest.raises(ValueError):
        m.allow_attempt([{"outcome": "substantive_failure"}] * 3, 3)
    m.allow_attempt([{"outcome": "substantive_failure"}], 3)
    m.allow_attempt([], 3)


def test_gzip_cleanup_preserves_original_hash(tmp_path):
    p = tmp_path / "server.log"
    p.write_text("evidence\n" * 100)
    before = module().sha(p)
    result = module().compress_verified(p)
    assert result["sha256"] == before
    assert not p.exists()
    assert (
        gzip.decompress(Path(result["path"]).read_bytes())
        == ("evidence\n" * 100).encode()
    )
