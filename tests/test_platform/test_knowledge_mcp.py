"""Knowledge contract on a self-contained immutable corpus; no bank task edits."""

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tau2.platform.knowledge import KnowledgeError, KnowledgeMCP


def corpus():
    docs = {}
    for i in range(7):
        path = f"/policies/loyalty/cashback-{i}.md"
        text = f"# Кешбэк {i}\n\nПолное правило, строка {i}.\n"
        docs[path] = {
            "content": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "metadata": {
                "document_id": "policy.cashback",
                "revision_id": f"v{i}",
                "document_type": "policy",
                "title": "Кешбэк",
                "created_at": "2026-09-01",
                "updated_at": f"2026-09-{i + 1:02d}",
            },
        }
    path = "/knowledge/calculation.md"
    text = "# Расчёт кешбэка\nПолный пример расчёта.\n"
    docs[path] = {
        "content": text,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "metadata": {
            "document_id": "knowledge.calculation",
            "revision_id": "v1",
            "document_type": "knowledge",
            "title": "Расчёт",
            "created_at": "2026-09-01",
            "updated_at": "2026-09-01",
        },
    }
    docs["/AGENTS.md"] = {
        "content": "not a published document",
        "metadata": {"document_type": "startup"},
    }
    return SimpleNamespace(documents=docs, sha256="test-snapshot")


def knowledge():
    return KnowledgeMCP(corpus(), token="public-session")


def test_catalog_revision_order_filters_and_pages():
    k = knowledge()
    first = k.list_revisions(document_id="policy.cashback")
    assert [x["revision_id"] for x in first["items"]] == ["v6", "v5", "v4", "v3", "v2"]
    last = k.list_revisions(document_id="policy.cashback", cursor=first["next_cursor"])
    assert [x["revision_id"] for x in last["items"]] == ["v1", "v0"] and last[
        "next_cursor"
    ] is None
    assert (
        k.list_documents(document_type="knowledge")["items"][0]["document_id"]
        == "knowledge.calculation"
    )
    page = k.search_documents(query=" ПОЛНОЕ  ПРАВИЛО ", document_type="policy")
    assert len(page["items"]) == 5 and all(
        "content" not in row for row in page["items"]
    )
    with pytest.raises(KnowledgeError, match="CURSOR"):
        k.search_documents(query="другой запрос", cursor=first["next_cursor"])


def test_cat_and_read_return_exact_full_text_and_hash():
    k = knowledge()
    read = k.read_document(document_id="policy.cashback", revision_id="v2")
    cat = k.corpus_command(command="cat /policies/loyalty/cashback-2.md")
    assert cat == read
    assert read["content"] == corpus().documents[read["path"]]["content"]
    assert (
        hashlib.sha256(read["content"].encode()).hexdigest() == read["content_sha256"]
    )
    assert read["updated_at"] == "2026-09-03"
    with pytest.raises(KnowledgeError):
        k.read_document(document_id="knowledge.calculation", revision_id="v2")


def test_grep_only_matches_and_separate_navigation():
    k = knowledge()
    result = k.corpus_command(command="grep -n -i 'КЕШБЭК' /policies")
    assert set(result) == {"matches", "next_cursor"}
    assert len(result["matches"]) == 7
    assert all(
        set(hit) == {"path", "line", "text", "line_truncated"}
        for hit in result["matches"]
    )
    assert all("Полное правило" not in hit["text"] for hit in result["matches"])
    roots = k.corpus_command(command="ls /")["items"]
    assert {x["path"] for x in roots} == {"/knowledge", "/policies"}
    assert (
        k.corpus_command(command="find /knowledge -name '*.md'")["items"][0][
            "document_id"
        ]
        == "knowledge.calculation"
    )
    assert k.corpus_command(command="grep -n 'отсутствует' /knowledge") == {
        "matches": [],
        "next_cursor": None,
    }


@pytest.mark.parametrize(
    "command",
    [
        "cat /../../etc/passwd",
        "cat /AGENTS.md",
        "cat /policies/x /knowledge/y",
        "ls /Users",
        "cat /etc/passwd",
        "ls / | cat /AGENTS.md",
        "curl https://example.com",
        "cat $(whoami)",
        "ls / > /tmp/out",
    ],
)
def test_commands_cannot_escape_or_execute_shell(command):
    with pytest.raises(KnowledgeError):
        knowledge().corpus_command(command=command)


def test_grep_paging_is_snapshot_bound_and_has_no_read_side_effects():
    data = corpus()
    # More matches than the grep page size, with a deliberately long matching line.
    data.documents["/knowledge/calculation.md"]["content"] = (
        "find " + "x" * 1000 + "\n"
    ) * 55
    data.documents["/knowledge/calculation.md"]["sha256"] = hashlib.sha256(
        data.documents["/knowledge/calculation.md"]["content"].encode()
    ).hexdigest()
    before = deepcopy(data.documents)
    k = KnowledgeMCP(data, token="session-a")
    first = k.corpus_command(command="grep -n find /knowledge")
    assert len(first["matches"]) == 50 and first["next_cursor"]
    assert first["matches"][0]["line_truncated"]
    last = k.corpus_command(
        command="grep -n find /knowledge", cursor=first["next_cursor"]
    )
    assert len(last["matches"]) == 5 and last["next_cursor"] is None
    assert data.documents == before
    with pytest.raises(KnowledgeError, match="CURSOR"):
        KnowledgeMCP(data, token="session-b").corpus_command(
            command="grep -n find /knowledge", cursor=first["next_cursor"]
        )


@pytest.mark.parametrize(
    "defect",
    [
        "missing_date",
        "invalid_date",
        "duplicate_date",
        "inconsistent_created",
        "duplicate_revision",
    ],
)
def test_invalid_metadata_is_explicit_never_invented(defect):
    data = corpus()
    first = data.documents["/policies/loyalty/cashback-0.md"]["metadata"]
    second = data.documents["/policies/loyalty/cashback-1.md"]["metadata"]
    if defect == "missing_date":
        del first["created_at"]
    elif defect == "invalid_date":
        first["updated_at"] = "2026-02-31"
    elif defect == "duplicate_date":
        second["updated_at"] = first["updated_at"]
    elif defect == "inconsistent_created":
        second["created_at"] = "2026-08-01"
    else:
        second["revision_id"] = first["revision_id"]
    k = KnowledgeMCP(data, token="metadata")
    with pytest.raises(KnowledgeError, match="CORPUS_METADATA"):
        k.list_documents()
    # Text search can still diagnose/explore a legacy corpus without pretending
    # that it has valid revision dates or full-read receipts.
    assert k.corpus_command(command="grep -n 'Кешбэк' /policies")["matches"]


def _session_with_corpus(data, **kwargs):
    from tau2.domains.banking_ru.environment import get_environment, get_tasks
    from tau2.platform.session import PlatformSession

    data.manifest = {}

    def constructor(solo_mode=True, task_id=None):
        env = get_environment(solo_mode=solo_mode, task_id=task_id)
        env.tools.corpus = data
        return env

    task = next(t for t in get_tasks() if t.id == "Bank 2")
    return PlatformSession(task, constructor, mcp_layout="directory", **kwargs)


def _open(session, name):
    result = session.call(
        "bank_directory",
        {"operation": "open_tool", "system_id": "knowledge", "tool_name": name},
    )
    assert not result.is_error, result


def _call(session, name, arguments):
    return session.call(
        "bank_call",
        {"system_id": "knowledge", "tool_name": name, "arguments": arguments},
    )


def test_knowledge_gateway_budgets_and_exact_receipts():
    import json

    s = _session_with_corpus(corpus(), max_calls=3, max_discovery_calls=30)
    for name in ("corpus_command", "list_documents", "read_document"):
        _open(s, name)
    assert not _call(s, "list_documents", {}).is_error
    found = _call(s, "corpus_command", {"command": "grep -n Кешбэк /policies"})
    assert not found.is_error and "documents" not in json.loads(found.content[0].text)
    assert len(s.log) == 0 and s.discovery_calls == 5
    read = _call(
        s, "read_document", {"document_id": "policy.cashback", "revision_id": "v2"}
    )
    cat = _call(s, "corpus_command", {"command": "cat /policies/loyalty/cashback-2.md"})
    assert not read.is_error and not cat.is_error
    assert json.loads(read.content[0].text) == json.loads(cat.content[0].text)
    assert len(s.log) == 2 and s.discovery_calls == 5
    assert s.log[0][0].name == "read_document"
    assert s.log[1][0].name == "corpus_command"
    assert all(
        e.get("budget") == "discovery"
        for e in s.mcp_events
        if e.get("canonical_name") == "list_documents"
    )


def test_missing_metadata_is_an_environment_error():
    data = corpus()
    del data.documents["/knowledge/calculation.md"]["metadata"]["created_at"]
    s = _session_with_corpus(data)
    _open(s, "list_documents")
    result = _call(s, "list_documents", {})
    assert result.is_error and "CORPUS_METADATA_INCOMPLETE" in result.content[0].text
    assert not s.tool_call_errors and s.environment_errors
    before = deepcopy(s.env.tools.corpus.documents)
    _open(s, "corpus_command")
    result = _call(s, "corpus_command", {"command": "grep -n кешбэк /knowledge"})
    assert not result.is_error
    assert s.env.tools.corpus.documents == before


def test_published_corpus_has_unambiguous_metadata_and_exact_reads():
    from pathlib import Path

    from tau2.domains.banking_ru.file_corpus import FileCorpus

    root = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
    source = FileCorpus(root)
    k = KnowledgeMCP(source, token="published-metadata")
    cards = []
    page = k.list_documents()
    while True:
        assert len(page["items"]) <= 5
        cards.extend(page["items"])
        if page["next_cursor"] is None:
            break
        page = k.list_documents(cursor=page["next_cursor"])
    published = {
        p: d
        for p, d in source.documents.items()
        if p.startswith(("/knowledge/", "/policies/"))
    }
    assert {card["path"] for card in cards} == set(published)
    groups = {}
    for card in cards:
        groups.setdefault(card["document_id"], []).append(card)
        read = k.read_document(card["document_id"], card["revision_id"])
        assert read == k.corpus_command("cat " + card["path"])
        assert read["content"] == published[card["path"]]["content"]
        assert read["content_sha256"] == published[card["path"]]["sha256"]
    for group in groups.values():
        assert min(c["updated_at"] for c in group) == group[0]["created_at"]
    assert not source._read_paths


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("bank_call", {"system_id": "knowledge", "tool_name": [], "arguments": {}}),
        ("bank_call", {"system_id": "knowledge", "tool_name": {}, "arguments": {}}),
        ("call_discoverable_agent_tool", {"name": [], "arguments": {}}),
    ],
)
def test_malformed_gateway_never_orphans_a_request(name, arguments):
    s = _session_with_corpus(corpus())
    assert s.call(name, arguments).is_error
    assert s.transport_complete
    assert not s.env.tools.db.tool_calls_log


def test_nul_regex_is_a_tracked_request_error():
    s = _session_with_corpus(corpus())
    _open(s, "corpus_command")
    result = _call(s, "corpus_command", {"command": "grep '\x00' /knowledge"})
    assert result.is_error and "INVALID_REGEX" in str(result)
    assert s.transport_complete and len(s.tool_call_errors) == 1
    assert not s.environment_errors and not s.log


def test_grep_treats_nul_in_document_as_text():
    source = corpus()
    doc = source.documents["/knowledge/calculation.md"]
    doc["content"] = "needle\x00content\n"
    doc["sha256"] = hashlib.sha256(doc["content"].encode()).hexdigest()
    result = KnowledgeMCP(source, token="nul").corpus_command("grep needle /knowledge")
    assert result["matches"][0]["text"] == "needle\x00content"


def test_actual_file_corpus_preserves_full_utf8_bytes(tmp_path):
    import json

    from tau2.domains.banking_ru.corpus_manifest import corpus_hash
    from tau2.domains.banking_ru.file_corpus import FileCorpus

    raw = (
        "---\r\nid: sample-v1\r\ndocument_id: policy.sample\r\nrevision_id: v1\r\n"
        "document_type: policy\r\ntitle: Sample\r\ncreated_at: 2026-09-01\r\n"
        "updated_at: 2026-09-01\r\n---\r\n\r\n# Полный текст\r\nСтрока.\r\n"
    ).encode()
    target = tmp_path / "corpus/policies/sample.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    for name in ("db.json", "intake_answers.json"):
        (tmp_path / name).write_text("{}")
    digest = hashlib.sha256(b"{}").hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "corpus_sha256": corpus_hash({"/policies/sample.md": raw}),
                "db_sha256": digest,
                "intake_answers_sha256": digest,
            }
        )
    )
    source = FileCorpus(tmp_path)
    k = KnowledgeMCP(source, token="real-file-corpus")
    assert k.read_document("policy.sample", "v1")["content"].encode() == raw
    assert k.corpus_command("cat /policies/sample.md") == k.read_document(
        "policy.sample", "v1"
    )
    assert (
        k.read_document("policy.sample", "v1")["content_sha256"]
        == hashlib.sha256(raw).hexdigest()
    )
    assert source._read_paths == set()


def test_knowledge_over_real_mcp_transport():
    import asyncio
    import json

    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server.transport_security import TransportSecuritySettings

    from tau2.platform.mcp_transport import MCPTransportJournal

    s = _session_with_corpus(corpus())
    server = s.build_server()
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    wrapped = MCPTransportJournal(app, s, None)

    async def go():
        async with server.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=wrapped), base_url="http://test"
            ) as http:
                async with streamable_http_client(
                    "http://test/mcp", http_client=http
                ) as streams:
                    async with ClientSession(streams[0], streams[1]) as client:
                        await client.initialize()
                        for name in ("corpus_command", "read_document"):
                            result = await client.call_tool(
                                "bank_directory",
                                {
                                    "operation": "open_tool",
                                    "system_id": "knowledge",
                                    "tool_name": name,
                                },
                            )
                            assert not result.is_error
                        found = await client.call_tool(
                            "bank_call",
                            {
                                "system_id": "knowledge",
                                "tool_name": "corpus_command",
                                "arguments": {"command": "grep -n Кешбэк /policies"},
                            },
                        )
                        assert not found.is_error and set(
                            json.loads(found.content[0].text)
                        ) == {"matches", "next_cursor"}
                        read = await client.call_tool(
                            "bank_call",
                            {
                                "system_id": "knowledge",
                                "tool_name": "read_document",
                                "arguments": {
                                    "document_id": "policy.cashback",
                                    "revision_id": "v2",
                                },
                            },
                        )
                        cat = await client.call_tool(
                            "bank_call",
                            {
                                "system_id": "knowledge",
                                "tool_name": "corpus_command",
                                "arguments": {
                                    "command": "cat /policies/loyalty/cashback-2.md"
                                },
                            },
                        )
                        assert not read.is_error and not cat.is_error
                        assert json.loads(read.content[0].text) == json.loads(
                            cat.content[0].text
                        )

    asyncio.run(go())
    assert s.transport_complete
    assert len(s.log) == 2 and s.discovery_calls == 3
    assert all("response_order" in e for e in s.mcp_events)
