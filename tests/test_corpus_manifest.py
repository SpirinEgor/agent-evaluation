"""Compact corpus versions, document metadata and historical snapshots."""

import hashlib
import json

import pytest

from tau2.domains.banking_ru.corpus_manifest import load_manifest, manifest_outputs
from tau2.domains.banking_ru.file_corpus import FileCorpus


def dataset(tmp_path):
    root = tmp_path / "data"
    (root / "corpus/knowledge").mkdir(parents=True)
    for name in ["db.json", "intake_answers.json"]:
        (root / name).write_text("{}\n")
    doc = root / "corpus/knowledge/example.md"
    doc.write_text("---\nid: example\nrevision_id: v1\n---\n\n# Example\n")
    # A compact store is indicated by its three hash fields, not a file listing.
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "corpus_sha256": "0" * 64,
                "db_sha256": "0" * 64,
                "intake_answers_sha256": "0" * 64,
            }
        )
    )
    expanded = load_manifest(root)
    for path, raw in manifest_outputs(root, expanded).items():
        path.write_bytes(raw)
    return root, doc


def test_compact_manifest_rebuilds_metadata_and_roundtrips_without_changes(tmp_path):
    root, doc = dataset(tmp_path)
    stored = json.loads((root / "manifest.json").read_text())
    assert set(stored) == {"corpus_sha256", "db_sha256", "intake_answers_sha256"}
    corpus = FileCorpus(root)
    receipt = corpus.read("/knowledge/example.md")
    assert receipt["metadata"] == {"id": "example", "revision_id": "v1"}
    assert receipt["sha256"] == hashlib.sha256(doc.read_bytes()).hexdigest()
    outputs = manifest_outputs(root, load_manifest(root, verify=True))
    assert all(path.read_bytes() == raw for path, raw in outputs.items())


@pytest.mark.parametrize(
    "change", ["content", "add", "remove", "rename", "db", "answers", "symlink"]
)
def test_compact_snapshot_detects_changes_and_rejects_symlinks(tmp_path, change):
    root, doc = dataset(tmp_path)
    if change == "content":
        doc.write_text(doc.read_text() + "Changed\n")
    elif change == "add":
        (doc.parent / "new.md").write_text("---\nid: new\n---\nNew")
    elif change == "remove":
        doc.unlink()
    elif change == "rename":
        doc.rename(doc.with_name("renamed.md"))
    elif change == "db":
        (root / "db.json").write_text('{"changed":true}')
    elif change == "answers":
        (root / "intake_answers.json").write_text('{"changed":true}')
    elif change == "symlink":
        (doc.parent / "link.md").symlink_to(doc)
    with pytest.raises(ValueError):
        load_manifest(root, verify=True)


def test_legacy_manifest_remains_unchanged_and_metadata_edit_moves_to_document(
    tmp_path,
):
    root, doc = dataset(tmp_path)
    expanded = load_manifest(root)
    expanded["documents"]["/knowledge/example.md"]["metadata"]["revision_id"] = "v2"
    outputs = manifest_outputs(root, expanded)
    for path, raw in outputs.items():
        path.write_bytes(raw)
    assert (
        FileCorpus(root).read("/knowledge/example.md")["metadata"]["revision_id"]
        == "v2"
    )
    legacy = load_manifest(root)
    raw = (json.dumps(legacy, ensure_ascii=False, indent=2) + "\n").encode()
    (root / "manifest.json").write_bytes(raw)
    assert (
        FileCorpus(root).read("/knowledge/example.md")["metadata"]["revision_id"]
        == "v2"
    )
    assert manifest_outputs(root, load_manifest(root))[root / "manifest.json"] == raw


def test_ordinary_variant_manifest_is_not_a_corpus_manifest(tmp_path):
    from tau2.domains.banking_ru.task_store import read_json, write_json

    path = tmp_path / "manifest.json"
    report = {"version": "variants-v1", "variants": [{"name": "example"}]}
    write_json(path, report)
    assert read_json(path) == report


def test_frontmatter_delimiters_must_occupy_an_entire_line():
    from tau2.domains.banking_ru.corpus_manifest import (
        document_body,
        document_metadata,
        with_metadata,
    )

    raw = b"---\nid: example\ntitle: Report --- revised\n---\n\n# Actual body\n"
    meta = document_metadata(raw)
    assert meta["title"] == "Report --- revised"
    assert document_body(raw) == "# Actual body\n"
    assert with_metadata(raw, meta) == raw
    meta["revision_id"] = "v2"
    updated = with_metadata(raw, meta)
    assert document_metadata(updated) == meta
    assert document_body(updated) == document_body(raw)


def test_corpus_options_live_in_startup_document(tmp_path):
    root, _ = dataset(tmp_path)
    startup = root / "corpus/AGENTS.md"
    startup.write_text("---\nid: startup\n---\n\nInstructions\n")
    expanded = load_manifest(root)
    expanded["discoverable_workflows"] = True
    for path, raw in manifest_outputs(root, expanded).items():
        path.write_bytes(raw)
    restored = load_manifest(root, verify=True)
    assert restored["discoverable_workflows"] is True
    assert set(json.loads((root / "manifest.json").read_text())) == {
        "corpus_sha256",
        "db_sha256",
        "intake_answers_sha256",
    }
