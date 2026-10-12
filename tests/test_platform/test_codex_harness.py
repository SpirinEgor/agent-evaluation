import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).parents[2] / "scripts" / "banking_ru" / "run_codex_harness.py"
)


def load_harness():
    spec = importlib.util.spec_from_file_location("run_codex_harness", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_classify_requires_model_done_for_luna_fail():
    """A harness-closed session is never evidence of a Luna failure."""
    harness = load_harness()

    status = harness.classify_result(
        {"status": "open", "calls": 24, "errors": 0},
        {"reward": 0.0},
        timed_out=False,
        exit_code=0,
        closed_by_harness=True,
    )

    assert status == "invalid_missing_done"


def test_classify_counts_completed_zero_reward_as_luna_fail():
    """A completed, healthy zero-reward session is a model result."""
    harness = load_harness()

    status = harness.classify_result(
        {"status": "done", "calls": 24, "errors": 0},
        {"reward": 0.0},
        timed_out=False,
        exit_code=0,
        closed_by_harness=False,
    )

    assert status == "luna_fail"


def test_build_command_exposes_only_the_opened_session_server():
    """A batch run must not inherit local tools or another task's MCP URL."""
    harness = load_harness()

    command = harness.build_codex_command(
        model="gpt-5.6-luna",
        effort="low",
        mcp_urls={
            "crm": "http://127.0.0.1:8017/s/token/crm/mcp",
            "support": "http://127.0.0.1:8017/s/token/support/mcp",
        },
        workdir=Path("/tmp/codex-run"),
    )

    assert command[:3] == ["codex", "exec", "--model"]
    assert "--ignore-user-config" in command
    assert "--ephemeral" in command
    assert "--approve-for-me" in command
    configs = [command[i + 1] for i, arg in enumerate(command) if arg == "--config"]
    assert 'mcp_servers.crm.url="http://127.0.0.1:8017/s/token/crm/mcp"' in configs
    assert (
        'mcp_servers.support.url="http://127.0.0.1:8017/s/token/support/mcp"' in configs
    )
    assert not any("banking_ru_pilot" in c for c in configs)


def test_build_prompt_keeps_policy_and_ticket_in_the_model_context():
    """The model must receive the whole task contract, not just the ticket."""
    harness = load_harness()

    prompt = harness.build_prompt(
        policy="POLICY: verify the customer before acting",
        ticket="TICKET: recover access for Ivan",
    )

    assert "POLICY: verify the customer before acting" in prompt
    assert "TICKET: recover access for Ivan" in prompt
    assert "done" in prompt


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI unavailable")
def test_codex_cli_parses_configured_system_names_without_literal_quotes():
    command = load_harness().build_codex_command(
        model="gpt-5.6-luna",
        effort="low",
        mcp_urls={"crm": "http://127.0.0.1:1/mcp", "support": "http://127.0.0.1:2/mcp"},
        workdir=Path("/tmp"),
    )
    overrides = [command[i + 1] for i, arg in enumerate(command) if arg == "--config"]
    args = ["codex", "mcp", "list", "--json"]
    for override in overrides:
        args.extend(["--config", override])
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    names = {item["name"] for item in json.loads(result.stdout)}
    assert {"crm", "support"} <= names
    assert not {'"crm"', '"support"'} & names
