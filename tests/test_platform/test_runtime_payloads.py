"""Neutral canaries only. Never depend on the real private payload store."""

import hashlib
import json

import pytest

from tau2.platform.runtime_payloads import (
    PREFIX,
    PayloadError,
    PayloadStore,
    materialize_bytes,
    materialize_content,
    materialize_value,
)


def test_runtime_binding_preserves_json_quoting_and_plain_document(
    tmp_path, monkeypatch
):
    store = PayloadStore(tmp_path / "store")
    text = 'Нейтральный пример: "офис"\nОткрыт в 09:00.'
    ref = store.put(text)
    monkeypatch.setenv("BANKING_PAYLOAD_STORE", str(store.root))
    raw = json.dumps({"body": ref, "amount": 42}, ensure_ascii=False).encode()
    expected = json.dumps({"body": text, "amount": 42}, ensure_ascii=False).encode()
    assert materialize_bytes(raw, json_document=True) == expected
    assert materialize_value({"body": ref}) == {"body": text}
    assert (
        materialize_bytes(("Header\n" + ref).encode()) == ("Header\n" + text).encode()
    )
    assert json.loads(materialize_content(raw.decode(), store))["body"] == text


def test_missing_store_and_tampering_do_not_fall_back_or_print_text(
    tmp_path, monkeypatch
):
    store = PayloadStore(tmp_path)
    ref = store.put("NEUTRAL_PRIVATE_CANARY")
    monkeypatch.delenv("BANKING_PAYLOAD_STORE", raising=False)
    with pytest.raises(PayloadError, match="NOT_CONFIGURED"):
        materialize_bytes(ref.encode())
    (tmp_path / ref[len(PREFIX) : -2]).write_text("DIFFERENT_PRIVATE_CANARY")
    with pytest.raises(PayloadError) as error:
        store.text(ref)
    assert str(error.value) == "PAYLOAD_INTEGRITY"
    assert "CANARY" not in str(error.value)


def test_symlink_and_traversal_are_rejected(tmp_path):
    text = "neutral"
    key = hashlib.sha256(text.encode()).hexdigest()
    other = tmp_path / "other"
    other.write_text(text)
    (tmp_path / key).symlink_to(other)
    store = PayloadStore(tmp_path)
    with pytest.raises(PayloadError, match="UNAVAILABLE"):
        store.get(key)
    with pytest.raises(PayloadError, match="INVALID_ID"):
        store.get("../../other")


def test_no_recursive_expansion_and_no_execution(tmp_path):
    store = PayloadStore(tmp_path)
    inner = store.put("neutral nested content")
    with pytest.raises(PayloadError, match="RESERVED_TOKEN"):
        store.put(inner)
    with pytest.raises(PayloadError, match="INVALID_REFERENCE"):
        store.text("{{bank_payload:../../x}}")
    with pytest.raises(PayloadError, match="IN_KEY"):
        store.value({inner: "value"})


def test_full_platform_observation_and_trace_receive_same_text(tmp_path, monkeypatch):
    from tau2.data_model.message import ToolMessage
    from tau2.domains.mock.environment import get_environment, get_tasks
    from tau2.platform.session import PlatformSession

    store = PayloadStore(tmp_path / "store")
    text = "NEUTRAL_RUNTIME_CANARY"
    ref = store.put(text)
    session = PlatformSession(get_tasks()[0], get_environment, payload_store=store)
    name = session.mcp_tools[0].name
    # Trusted runtime inputs are hydrated before tool dispatch. Responses must
    # not expand tokens reflected from an agent's arguments or an error string.
    body = materialize_value({"body": ref}, store)
    monkeypatch.setattr(
        session.env,
        "get_response",
        lambda call: ToolMessage(
            id=call.id,
            role="tool",
            requestor="assistant",
            content=json.dumps(body),
        ),
    )
    result = session.call(name, {})
    assert json.loads(result.content[0].text)["body"] == text
    assert session.log[-1][1].content == result.content[0].text
    assert session.mcp_events[-1]["response"] == result.content[0].text
    assert PREFIX not in session.log[-1][1].content
    monkeypatch.setattr(
        session.env,
        "get_response",
        lambda call: ToolMessage(
            id=call.id,
            role="tool",
            requestor="assistant",
            content=json.dumps(call.arguments),
        ),
    )
    reflected = session.call(name, {"echo": ref})
    assert json.loads(reflected.content[0].text)["echo"] == ref


def test_plain_tasks_need_no_store(monkeypatch):
    monkeypatch.delenv("BANKING_PAYLOAD_STORE", raising=False)
    assert materialize_bytes(b'{"amount":42}', json_document=True) == b'{"amount":42}'
    assert materialize_value({"amount": 42}) == {"amount": 42}


def test_manifest_refresh_hashes_logical_inputs_but_preserves_templates(
    tmp_path, monkeypatch
):
    from tau2.domains.banking_ru.corpus_manifest import (
        corpus_files,
        corpus_hash,
        load_manifest,
        manifest_outputs,
        serialized,
    )

    store = PayloadStore(tmp_path / "private")
    monkeypatch.setenv("BANKING_PAYLOAD_STORE", str(store.root))
    ref = store.put('Нейтральный "документ"\nВторая строка.')
    root = tmp_path / "data"
    corpus = root / "corpus"
    corpus.mkdir(parents=True)
    document = (
        "---\nid: neutral\ncreated_at: '2026-11-10'\nupdated_at: '2026-11-10'\n---\n\n"
        + ref
    ).encode()
    (corpus / "neutral.md").write_bytes(document)
    db = serialized({"body": ref})
    (root / "db.json").write_bytes(db)
    (root / "intake_answers.json").write_bytes(b"{}\n")
    original = {
        "corpus_sha256": corpus_hash(corpus_files(root)),
        "db_sha256": hashlib.sha256(
            materialize_bytes(db, json_document=True)
        ).hexdigest(),
        "intake_answers_sha256": hashlib.sha256(b"{}\n").hexdigest(),
    }
    (root / "manifest.json").write_bytes(serialized(original))
    expanded = load_manifest(root, verify=True)
    output = manifest_outputs(root, expanded, {root / "db.json": db})
    assert json.loads(output[root / "manifest.json"]) == original
    expanded["documents"]["/neutral.md"]["metadata"]["updated_at"] = "2026-11-11"
    output = manifest_outputs(root, expanded)
    assert ref.encode() in output[corpus / "neutral.md"]
    assert b"body" not in output[corpus / "neutral.md"]
    for path, raw in output.items():
        path.write_bytes(raw)
    load_manifest(root, verify=True)


def test_developer_export_excludes_history_and_blocks_known_text(tmp_path, monkeypatch):
    import runpy
    from pathlib import Path

    source = Path(__file__).resolve().parents[2]
    module = runpy.run_path(str(source / "scripts/banking_ru/manage_payloads.py"))
    function = module["export_developer"]
    root = tmp_path / "repo"
    root.mkdir()
    store = PayloadStore(tmp_path / "private")
    text = "NEUTRAL_PRIVATE_CANARY_WITH_ENOUGH_LENGTH_FOR_EXPORT_CHECK"
    ref = store.put(text)
    (root / "scenario.json").write_text(json.dumps({"body": ref}))
    (root / ".git").mkdir()
    (root / ".git/raw-history").write_text(text)
    function.__globals__["ROOT"] = root
    monkeypatch.setattr(
        module["subprocess"], "check_output", lambda *a, **k: b"scenario.json\x00"
    )
    destination = tmp_path / "export"
    function(destination, store)
    assert not (destination / ".git").exists()
    assert text not in (destination / "scenario.json").read_text()
    (root / "unsafe.txt").write_text(text)
    monkeypatch.setattr(
        module["subprocess"],
        "check_output",
        lambda *a, **k: b"scenario.json\x00unsafe.txt\x00",
    )
    with pytest.raises(ValueError, match="Known payload remains"):
        function(tmp_path / "rejected", store)
    assert not (tmp_path / "rejected").exists()


def test_sdk_bundle_provisions_private_store_outside_developer_repo(
    tmp_path, monkeypatch
):
    import io
    import runpy
    import tarfile
    from pathlib import Path
    from types import SimpleNamespace

    source = Path(__file__).resolve().parents[2]
    module = runpy.run_path(str(source / "scripts/banking_ru/build_platform_bundle.py"))
    build = module["build_bundle"]
    root = tmp_path / "repo"
    integration = root / "integrations/eval_platform"
    integration.mkdir(parents=True)
    for name in (
        "benchmark.yaml",
        "runtime.yaml",
        "Dockerfile",
        "platform_benchmark.py",
        "platform_tasks.py",
        "platform_evaluator.py",
    ):
        (integration / name).write_text("neutral")
    runtime = root / "src/tau2"
    (runtime / "evaluator").mkdir(parents=True)
    (runtime / "platform/sdk").mkdir(parents=True)
    for name in ("tasks.py", "evaluator.py"):
        (runtime / "platform/sdk" / name).write_text("neutral")
    data = root / "data/tau2/domains/banking_ru"
    (data / "corpus").mkdir(parents=True)
    (data / "tasks").mkdir()
    store = PayloadStore(tmp_path / "private")
    ref = store.put("NEUTRAL_BUNDLE_CANARY")
    for name in ("db.json", "manifest.json", "intake_answers.json"):
        (data / name).write_text(
            json.dumps({"body": ref}) if name == "db.json" else "{}"
        )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w"):
        pass
    monkeypatch.setattr(
        module["subprocess"],
        "run",
        lambda cmd, **kw: SimpleNamespace(
            stdout=buffer.getvalue() if cmd[0] == "git" else b""
        ),
    )
    build.__globals__["ROOT"] = root
    with pytest.raises(ValueError, match="store is required"):
        build(tmp_path, tmp_path / "missing")
    with pytest.raises(ValueError, match="outside"):
        build(tmp_path, root / "unsafe-bundle", store.root)
    output = tmp_path / "bundle"
    build(tmp_path, output, store.root)
    key = ref[len(PREFIX) : -2]
    assert (output / "private_payloads" / key).read_text() == "NEUTRAL_BUNDLE_CANARY"
    assert ref in (output / "execute/data/tau2/domains/banking_ru/db.json").read_text()
    assert not (output / "execute/src/tau2/evaluator").exists()
