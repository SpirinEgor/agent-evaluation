import importlib.util
import re
from pathlib import Path

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.mcp_systems import SYSTEMS, startup_policy
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
    policy = startup_policy()
    assert s.policy == policy
    assert get_environment(task_id="Bank 3", solo_mode=True).policy == policy
    assert not re.search(r"/policies/|/knowledge/|РО-\d|kb_", policy)
    assert "Системы банка:" not in policy
    assert not any(description in policy for description in SYSTEMS.values())
    assert "MCP" in policy and "done" in policy


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
