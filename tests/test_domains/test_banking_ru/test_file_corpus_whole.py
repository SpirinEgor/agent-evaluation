"""Whole-file snapshot reads, bounds, security and truthful source receipts."""

import hashlib
import json

import pytest

from tau2.domains.banking_ru.file_corpus import FileCorpus


def corpus_root(tmp_path, documents):
    """Create a real hash-verified snapshot without production corpus changes."""
    manifest = {"version": 1, "documents": {}}
    for path, (content, metadata) in documents.items():
        target = tmp_path / "corpus" / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
        manifest["documents"][path] = {
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "metadata": metadata,
        }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def test_read_returns_exact_whole_text_without_ranges(tmp_path):
    content = "---\nid: source\n---\n\n# Правило\n\nТочный текст.\n"
    corpus = FileCorpus(
        corpus_root(
            tmp_path, {"/source.md": (content, {"id": "source", "revision_id": "r1"})}
        )
    )
    result = corpus.read("/source.md")
    assert result["content"] == content
    assert result["total_lines"] == 7
    assert result["complete"] is True
    assert result["sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert result["snapshot_sha256"] == corpus.sha256
    assert (
        not {"start_line", "end_line", "requested_start_line", "requested_end_line"}
        & result.keys()
    )


def test_snapshot_rejects_201_lines_even_with_valid_hash(tmp_path):
    root = corpus_root(tmp_path, {"/source.md": ("line\n" * 201, {"id": "source"})})
    with pytest.raises(ValueError, match="200 lines"):
        FileCorpus(root)


def test_snapshot_accepts_200_lines(tmp_path):
    content = "line\n" * 200
    corpus = FileCorpus(
        corpus_root(tmp_path, {"/source.md": (content, {"id": "source"})})
    )
    assert corpus.read("/source.md")["content"] == content
    assert corpus.read("/source.md")["total_lines"] == 200


def test_snapshot_hash_mismatch_is_rejected(tmp_path):
    root = corpus_root(tmp_path, {"/source.md": ("verified\n", {"id": "source"})})
    (root / "corpus/source.md").write_text("tampered\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        FileCorpus(root)


@pytest.mark.parametrize(
    "path", ["/../outside.md", "source.md", "/a//source.md", "/a\\source.md"]
)
def test_read_rejects_noncanonical_paths(tmp_path, path):
    corpus = FileCorpus(
        corpus_root(tmp_path, {"/source.md": ("text\n", {"id": "source"})})
    )
    with pytest.raises(ValueError, match="canonical absolute"):
        corpus.read(path)


def test_snapshot_rejects_symlink(tmp_path):
    root = corpus_root(tmp_path, {"/source.md": ("text\n", {"id": "source"})})
    target = root / "corpus/source.md"
    target.unlink()
    outside = root / "outside.md"
    outside.write_text("text\n")
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="symlinks"):
        FileCorpus(root)


@pytest.mark.parametrize(
    "aggregate_body, revision",
    [("# Index\n[source](/source.md)\n", "r1"), ("# Bundle\n\nSource rule.\n", "old")],
)
def test_index_or_wrong_revision_does_not_get_source_credit(
    tmp_path, aggregate_body, revision
):
    corpus = FileCorpus(
        corpus_root(
            tmp_path,
            {
                "/source.md": (
                    "---\nid: source\n---\n\nSource rule.\n",
                    {"id": "source", "revision_id": "r1"},
                ),
                "/bundle.md": (
                    aggregate_body,
                    {
                        "id": "bundle",
                        "source_paths": ["/source.md"],
                        "source_revisions": {"source": revision},
                    },
                ),
            },
        )
    )
    assert "included_documents" not in corpus.read("/bundle.md")
    assert "equivalent_documents" not in corpus.read("/source.md")


def test_real_bundle_returns_verified_bodies_and_whole_equivalent(tmp_path):
    corpus = FileCorpus(
        corpus_root(
            tmp_path,
            {
                "/source.md": (
                    "---\nid: source\n---\n\nSource rule.\n",
                    {"id": "source", "revision_id": "r1"},
                ),
                "/bundle.md": (
                    "# Bundle\n\nSource rule.\n",
                    {
                        "id": "bundle",
                        "source_paths": ["/source.md"],
                        "source_revisions": {"source": "r1"},
                    },
                ),
            },
        )
    )
    assert (
        corpus.read("/bundle.md")["included_documents"][0]["content"]
        == "---\nid: source\n---\n\nSource rule.\n"
    )
    equivalent = corpus.read("/source.md")["equivalent_documents"][0]
    assert equivalent["content"] == "# Bundle\n\nSource rule.\n"
    assert equivalent["complete"] is True
    assert (
        not {"start_line", "end_line", "requested_start_line", "requested_end_line"}
        & equivalent.keys()
    )
