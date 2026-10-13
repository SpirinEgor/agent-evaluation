"""Build-level checks for fully readable shared corpus, without task execution."""

import importlib.util
import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[3] / "data/tau2/domains/banking_ru"
SCRIPT = DATA.parents[3] / "scripts/banking_ru/build_bounded_corpus.py"


def test_all_exposed_markdown_fits_whole_read():
    manifest = json.loads((DATA / "manifest.json").read_text())
    oversized = {
        path: len((DATA / "corpus" / path.lstrip("/")).read_text().splitlines())
        for path in manifest["documents"]
        if len((DATA / "corpus" / path.lstrip("/")).read_text().splitlines()) > 200
    }
    assert oversized == {}


def _builder():
    spec = importlib.util.spec_from_file_location("bounded_corpus_build", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_rejects_oversized_file_before_write(tmp_path):
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "oversized.md").write_text("line\n" * 201)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"documents": {"/oversized.md": {}}})
    )
    with pytest.raises(ValueError, match="200 lines"):
        _builder().validate_corpus(tmp_path)


def test_builder_accepts_exactly_200_lines(tmp_path):
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "bounded.md").write_text("line\n" * 200)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"documents": {"/bounded.md": {}}})
    )
    _builder().validate_corpus(tmp_path)


def test_overview_read_does_not_grant_source_receipts():
    from tau2.domains.banking_ru.file_corpus import FileCorpus

    corpus = FileCorpus(DATA)
    for number in (41, 53):
        receipt = corpus.read(f"/knowledge/kb_final_{number}.md")
        assert receipt["complete"] is True
        assert not receipt.get("included_documents")
        assert not receipt.get("equivalent_documents")
        assert not receipt["metadata"].get("source_paths")


def test_bundles_return_every_original_source_with_its_full_body():
    from tau2.domains.banking_ru.file_corpus import FileCorpus

    corpus = FileCorpus(DATA)
    expected = {
        41: {f"kb_{i}" for i in range(320, 334)},
        53: {"kb_167", "kb_168"} | {f"kb_{i}" for i in range(340, 350)},
    }
    for number, ids in expected.items():
        metadata = corpus.documents[f"/knowledge/kb_final_{number}.md"]["metadata"]
        received = {}
        for path in metadata["related_bundle_paths"]:
            receipt = corpus.read(path)
            assert receipt["total_lines"] <= 200
            for document in receipt["included_documents"]:
                received[document["id"]] = document
        assert set(received) == ids
        for article_id in ids:
            document = received[article_id]
            assert document["complete"] is True
            assert (
                document["content"]
                == (DATA / "corpus/knowledge" / f"{article_id}.md").read_text()
            )


def test_bank41_requires_sources_besides_overview_and_gold_reads_them():
    tasks = json.loads((DATA / "tasks.json").read_text())
    task = next(t for t in tasks if t["id"] == "Bank 41")
    criteria = task["evaluation_criteria"]
    expected_paths = {f"/knowledge/kb_{i}.md" for i in range(320, 334)}
    evidence_paths = {
        e.get("expected_fields", {}).get("path") for e in criteria["result_evidence"]
    }
    gold_paths = {
        a.get("arguments", {}).get("path")
        for a in criteria["actions"]
        if a["name"] == "file_read"
    }
    assert expected_paths <= evidence_paths
    assert expected_paths <= gold_paths
    # The overview helps navigation; the complete operative sources satisfy
    # the contract independently, as checked by the no-navigation trajectory.
    assert "/knowledge/kb_final_41.md" not in evidence_paths
    assert "/knowledge/kb_final_41.md" in gold_paths
    assert not any(
        a.get("required_for_reward", True)
        for a in criteria["actions"]
        if a.get("arguments", {}).get("path") == "/knowledge/kb_final_41.md"
    )


def test_build_rejects_oversized_unchanged_source_before_output(tmp_path):
    import shutil

    target = tmp_path / "data"
    shutil.copytree(DATA, target)
    source = target / "corpus/knowledge/kb_101.md"
    source.write_text("line\n" * 201)
    tasks_before = (target / "tasks.json").read_bytes()
    with pytest.raises(ValueError, match="200 lines"):
        _builder().build(target)
    assert (target / "tasks.json").read_bytes() == tasks_before
