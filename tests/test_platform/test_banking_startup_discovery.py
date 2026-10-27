import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.mcp_systems import SYSTEMS
from tau2.platform.session import PlatformSession

ROOT = Path(__file__).resolve().parents[2]


def session():
    return PlatformSession(
        next(t for t in get_tasks() if t.id == "Bank 3"),
        get_environment,
        mcp_layout="systems",
    )


def test_startup_has_no_system_map_or_document_targets():
    s = session()
    policy = get_environment(task_id="Bank 3", solo_mode=True).policy
    assert s.policy == policy
    assert not re.search(r"/policies/|/knowledge/|РО-\d|kb_", policy)
    assert "Системы банка:" not in policy
    assert not any(description in policy for description in SYSTEMS.values())
    assert "MCP" in policy and "done" in policy


@pytest.mark.parametrize("layout", ["single", "systems"])
def test_session_uses_startup_from_its_environment_snapshot(layout, tmp_path):
    from tau2.domains.banking_ru.corpus_manifest import corpus_files, corpus_hash
    from tau2.domains.banking_ru.data_model import BankingDB
    from tau2.domains.banking_ru.file_corpus import FileCorpus
    from tau2.domains.banking_ru.file_pilot import get_file_environment
    from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR

    shutil.copytree(BANKING_RU_DATA_DIR / "corpus", tmp_path / "corpus")
    for name in ("db.json", "intake_answers.json", "manifest.json"):
        shutil.copy2(BANKING_RU_DATA_DIR / name, tmp_path / name)
    startup = tmp_path / "corpus/AGENTS.md"
    startup.write_text(startup.read_text() + "\nТестовая строка из снимка корпуса.\n")
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["corpus_sha256"] = corpus_hash(corpus_files(tmp_path))
    manifest_path.write_text(json.dumps(manifest))
    corpus = FileCorpus(tmp_path)
    db = BankingDB.model_validate_json((tmp_path / "db.json").read_text())
    task = next(t for t in get_tasks() if t.id == "Bank 3")

    def changed_snapshot(solo_mode=False, task_id=None):
        return get_file_environment(
            tmp_path, "banking_ru", solo_mode=solo_mode, snapshot=(corpus, db)
        )

    s = PlatformSession(task, changed_snapshot, mcp_layout=layout)
    assert s.policy == s.env.get_policy()
    assert "Тестовая строка из снимка корпуса." in s.policy
    assert s.env.run_metadata["startup"]["content"] == s.policy


def test_harness_prompt_has_no_duplicate_system_catalog():
    spec = importlib.util.spec_from_file_location(
        "startup_harness", ROOT / "scripts/banking_ru/run_codex_harness.py"
    )
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    prompt = h.build_prompt(
        policy="POLICY",
        ticket="TICKET",
        mcp_urls={"crm": "secret", "knowledge": "secret"},
    )
    assert "POLICY" in prompt and "TICKET" in prompt and "done" in prompt
    assert "Доступные MCP:" not in prompt
    assert "crm" not in prompt and "knowledge" not in prompt and "secret" not in prompt


def test_mcp_exposes_purpose_and_api_without_document_recipe():
    s = session()
    for name in SYSTEMS:
        assert (
            s.build_server(name).create_initialization_options().instructions
            == SYSTEMS[name]
        )
    for tool in s.mcp_tools:
        assert not re.search(
            r"/policies/[^\s]+|/knowledge/[^\s]+|\bkb_\w+", tool.description or ""
        )
        assert tool.input_schema


def test_documents_discoverable_without_given_paths():
    s = session()
    assert not s.call("file_list", {"path": "/"}, system="knowledge").is_error
    assert not s.call(
        "file_search",
        {"root": "/", "pattern": "идентификац|обращени|интернет"},
        system="knowledge",
    ).is_error
