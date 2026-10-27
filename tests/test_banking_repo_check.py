"""Small counterexamples for the read-only repository guard."""

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/banking_ru/check_repo.py"


@pytest.fixture
def guard():
    return runpy.run_path(str(SCRIPT))


def test_links_and_duplicate_unicode_heading_anchors(guard, tmp_path):
    doc = tmp_path / "guide.md"
    doc.write_text(
        "# Правила `DB`\n# Правила `DB`\n[ok](#правила-db-1)\n````\n[example](missing)\n````\n"
    )
    assert guard["check_documents"](tmp_path, [doc]) == []
    doc.write_text(doc.read_text() + "[bad](#нет)\n[bad](missing.md)\n")
    errors = guard["check_documents"](tmp_path, [doc])
    assert len(errors) == 2


def test_links_cannot_require_an_external_checkout(guard, tmp_path):
    doc = tmp_path / "guide.md"
    outside = tmp_path.parent / "external-guide.md"
    outside.write_text("# External\n")
    doc.write_text(f"[bad](<{outside}>)\n[bad](../external-guide.md)\n")
    assert len(guard["check_documents"](tmp_path, [doc])) == 2


def test_profiles_follow_task_data_not_hardcoded_bank_numbers(guard, tmp_path):
    tasks = [
        {
            "id": "Bank 7",
            "evaluation_criteria": {
                "reward_basis": ["DB", "ACTION"],
                "banking_contract_version": "db_action_v1",
                "action_reward_scope": "full_contract",
                "require_successful_actions": True,
                "business_process_only": True,
                "completion_mode": "completion_only",
            },
        },
        {
            "id": "Bank 8",
            "evaluation_criteria": {"reward_basis": ["DB", "ENV_ASSERTION", "ACTION"]},
        },
    ]
    doc = tmp_path / "evaluation.md"
    doc.write_text(
        "| Bank 7 | DB × ACTION | profile |\n| Остальные 1 | DB × ENV_ASSERTION × ACTION | legacy |\n"
    )
    assert guard["check_profiles"](tasks, doc) == []
    doc.write_text(doc.read_text().replace("Bank 7", "Bank 2"))
    assert guard["check_profiles"](tasks, doc)
    doc.write_text(
        "| Bank 7 | DB × ACTION | profile |\n| Остальные 2 | DB × ENV_ASSERTION × ACTION | legacy |\n"
    )
    assert guard["check_profiles"](tasks, doc)


def test_index_rejects_unlisted_task_files(guard, tmp_path):
    data = tmp_path / "data/tau2/domains/banking_ru"
    (data / "tasks").mkdir(parents=True)
    (data / "tasks.json").write_text(
        json.dumps({"task_files": ["tasks/bank_001.json"]})
    )
    for n in (1, 2):
        (data / f"tasks/bank_{n:03d}.json").write_text(json.dumps({"id": f"Bank {n}"}))
    loader = SCRIPT.parents[2] / "src/tau2/domains/banking_ru/task_store.py"
    target = tmp_path / "src/tau2/domains/banking_ru/task_store.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(loader.read_bytes())
    tasks, errors = guard["check_task_index"](tmp_path)
    assert len(tasks) == 1
    assert any("unlisted" in e for e in errors)


def test_hidden_input_detection_preserves_negative_security_examples(guard, tmp_path):
    path = tmp_path / "test_sample.py"
    path.write_text('examples = ["/artifacts/secret", "/Users/name/my_wiki/secret"]\n')
    assert guard["check_test_inputs"]([path]) == []
    path.write_text(
        'from pathlib import Path\nDATA = Path("artifacts") / "old/db.json"\n'
    )
    assert guard["check_test_inputs"]([path])
    path.write_text('data = open("/Users/name/my_wiki/data.json").read()\n')
    assert guard["check_test_inputs"]([path])


def test_hidden_input_in_composed_path(guard, tmp_path):
    path = tmp_path / "test_sample.py"
    path.write_text(
        'from pathlib import Path\nDATA = Path(__file__).parent / "artifacts" / "old/db.json"\n'
    )
    assert guard["check_test_inputs"]([path])


def test_cli_runs_from_other_directory_and_keeps_data_unchanged(tmp_path):
    root = SCRIPT.parents[2]
    data = root / "data/tau2/domains/banking_ru"

    def snapshot():
        return {
            str(p.relative_to(data)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in data.rglob("*")
            if p.is_file() and not p.name.endswith(".lock")
        }

    before = snapshot()
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Banking repository OK" in result.stdout
    assert snapshot() == before


def test_links_allow_balanced_parentheses_in_filenames(guard, tmp_path):
    (tmp_path / "guide(v1).md").write_text("# Guide\n")
    doc = tmp_path / "index.md"
    doc.write_text("[Guide](guide(v1).md)\n")
    assert guard["check_documents"](tmp_path, [doc]) == []


def test_duplicate_anchors_do_not_collide_with_literal_suffixes(guard, tmp_path):
    doc = tmp_path / "index.md"
    doc.write_text("# X\n# X\n# X-1\n[valid](#x-1-1)\n")
    assert guard["check_documents"](tmp_path, [doc]) == []


@pytest.mark.parametrize(
    "source",
    [
        'import shutil\nshutil.copytree(fixture, tmp_path / "artifacts")\n',
        'from shutil import copytree\ncopytree(fixture, tmp_path / "artifacts")\n',
        'import shutil\nshutil.copytree(fixture, dst=tmp_path / "artifacts")\n',
    ],
)
def test_copy_destination_is_an_output_not_a_dependency(guard, tmp_path, source):
    path = tmp_path / "test_sample.py"
    path.write_text(source)
    assert guard["check_test_inputs"]([path]) == []
