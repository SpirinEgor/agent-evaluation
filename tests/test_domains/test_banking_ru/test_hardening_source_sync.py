"""Source synchronization preserves scenario semantics while updating receipts."""

import importlib.util
import json
from pathlib import Path


def test_sync_refreshes_document_and_existing_receipt_without_other_task_changes(
    tmp_path,
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
    module.sync(tmp_path)
    updated = json.loads((tmp_path / "tasks.json").read_text())[0]
    expected = updated["evaluation_criteria"]["result_evidence"][0]["expected_fields"]
    assert expected["content"] == content and expected["sha256"] != "old"
    assert (
        updated["ticket"] == "unchanged"
        and updated["evaluation_criteria"]["actions"] == []
    )
    first = {f: f.read_bytes() for f in tmp_path.glob("*.json")}
    module.sync(tmp_path)
    assert all(f.read_bytes() == raw for f, raw in first.items())


def test_subscription_generator_preserves_hardened_claim_plan():
    import sys

    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "scripts/banking_ru"))
    import shutil
    import tempfile

    from build_subscription_regulation_contract import build

    with tempfile.TemporaryDirectory(dir=root) as temp:
        data = Path(temp) / "data"
        from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR

        shutil.copytree(BANKING_RU_DATA_DIR, data)
        tasks = json.loads((data / "tasks.json").read_text())
        task = next(t for t in tasks if t["id"] == "Bank 54")
        task["description"]["notes"] = "Hardened 2200 plan with linked receipts"
        task["evaluation_criteria"]["result_evidence"].append(
            {
                "evidence_id": "bank54:txn_314104:accept_claim",
                "tool_names": ["get_transactions"],
                "record_id": "txn_314104",
                "expected_fields": {"amount": 700},
            }
        )
        (data / "tasks.json").write_text(json.dumps(tasks))
        outputs = build(data)
        updated = json.loads(outputs[data / "tasks.json"])
        assert (
            next(t for t in updated if t["id"] == "Bank 54")["description"]["notes"]
            == task["description"]["notes"]
        )
