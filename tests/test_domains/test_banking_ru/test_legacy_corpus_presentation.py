"""The common legacy helpdesk view exposes complete articles, not audit metadata."""

import hashlib
import json

from tau2.domains.banking_ru.file_corpus import FileCorpus


def test_legacy_articles_have_only_identity_title_and_received_content(tmp_path):
    content = (
        "# Opening savings accounts\nEligibility: checking account held for 14 days.\n"
    )
    file = tmp_path / "corpus/knowledge/office/doc_0001.md"
    file.parent.mkdir(parents=True)
    file.write_text(content)
    manifest = {
        "version": 1,
        "presentation": "legacy_helpdesk",
        "documents": {
            "/knowledge/office/doc_0001.md": {
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "metadata": {
                    "id": "savings-eligibility",
                    "title": "Savings eligibility",
                    "revision_id": "2026-07",
                    "trust": "bank-policy",
                },
            }
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    corpus = FileCorpus(tmp_path)
    result = corpus.read("/knowledge/office/doc_0001.md")
    assert result == {
        "id": "savings-eligibility",
        "title": "Savings eligibility",
        "content": content,
    }
    hit = corpus.search("14 days")["matches"][0]
    assert hit["path"] == "/knowledge/office/doc_0001.md"
    assert hit["id"] == "savings-eligibility"
    assert hit["text"] == "Eligibility: checking account held for 14 days."
    assert hit["line_truncated"] is False
    assert (
        corpus.documents["/knowledge/office/doc_0001.md"]["sha256"]
        == hashlib.sha256(content.encode()).hexdigest()
    )


def test_existing_presentation_preserves_audit_receipts(tmp_path):
    file = tmp_path / "corpus/article.md"
    file.parent.mkdir()
    file.write_text("Business condition.\n")
    manifest = {
        "version": 1,
        "documents": {
            "/article.md": {
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                "metadata": {"id": "article", "title": "Article", "revision_id": "1"},
            }
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    result = FileCorpus(tmp_path).read("/article.md")
    assert result["complete"] is True
    assert result["metadata"]["revision_id"] == "1"
