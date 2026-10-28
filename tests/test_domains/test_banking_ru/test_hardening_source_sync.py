"""Source synchronization preserves scenario semantics while updating receipts."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("storage", ["legacy", "compact_split"])
def test_sync_refreshes_document_and_existing_receipt_without_other_task_changes(
    tmp_path,
    storage,
):
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/banking_ru/sync_hardening_sources.py"
    )
    spec = importlib.util.spec_from_file_location("source_sync", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "corpus/knowledge").mkdir(parents=True)
    content = '---\nid: "kb_test"\ntitle: "Test"\nrevision_id: "v2"\neffective_from: 2026-08-28\n---\n\n# Test\n\nUpdated.\n'
    (tmp_path / "corpus/knowledge/kb_test.md").write_text(content)
    docs = {
        "/knowledge/kb_test.md": {
            "sha256": "old",
            "metadata": {"id": "kb_test", "revision_id": "v1"},
        }
    }
    task = {
        "id": "Bank 99",
        "ticket": "unchanged",
        "evaluation_criteria": {
            "actions": [],
            "result_evidence": [
                {
                    "expected_fields": {
                        "path": "/knowledge/kb_test.md",
                        "sha256": "old",
                        "content": "old",
                        "snapshot_sha256": "old",
                    }
                }
            ],
        },
    }
    for name, value in [
        ("manifest", {"documents": docs}),
        (
            "db",
            {
                "articles": {
                    "kb_test": {"id": "kb_test", "title": "Test", "body": "old"}
                }
            },
        ),
        ("tasks", [task]),
    ]:
        (tmp_path / (name + ".json")).write_text(json.dumps(value))
    from tau2.domains.banking_ru.task_store import load_tasks

    if storage == "compact_split":
        from tau2.domains.banking_ru.corpus_manifest import corpus_files, corpus_hash

        (tmp_path / "intake_answers.json").write_text("{}")
        compact = {
            "corpus_sha256": corpus_hash(corpus_files(tmp_path)),
            "db_sha256": hashlib.sha256(
                (tmp_path / "db.json").read_bytes()
            ).hexdigest(),
            "intake_answers_sha256": hashlib.sha256(b"{}").hexdigest(),
        }
        (tmp_path / "manifest.json").write_text(json.dumps(compact))
        folder = tmp_path / "tasks/bank_099"
        folder.mkdir(parents=True)
        (folder / "scenario.json").write_text(json.dumps({"id": task["id"]}))
        (folder / "ticket.md").write_text(task["ticket"])
        (folder / "evaluation.json").write_text(json.dumps(task["evaluation_criteria"]))
        (tmp_path / "tasks.json").write_text(
            json.dumps({"task_files": ["tasks/bank_099/scenario.json"]})
        )
    module.sync(tmp_path)
    updated = load_tasks(tmp_path / "tasks.json")[0]
    expected = updated["evaluation_criteria"]["result_evidence"][0]["expected_fields"]
    assert expected["content"] == content and expected["sha256"] != "old"
    assert (
        updated["ticket"] == "unchanged"
        and updated["evaluation_criteria"]["actions"] == []
    )
    first = {f: f.read_bytes() for f in tmp_path.rglob("*.json")}
    module.sync(tmp_path)
    assert all(f.read_bytes() == raw for f, raw in first.items())
