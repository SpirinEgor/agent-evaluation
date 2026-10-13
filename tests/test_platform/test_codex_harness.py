import importlib.util
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    assert "--approve-for-me" not in command
    configs = [command[i + 1] for i, arg in enumerate(command) if arg == "--config"]
    assert 'approval_policy="never"' in configs
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
def test_solution_contract_host_channels_are_disabled():
    """CLI consumes the harness overrides and removes local reading channels."""
    command = load_harness().build_codex_command(
        model="gpt-6.1-sol", effort="low", mcp_urls={}, workdir=Path("/tmp")
    )
    overrides = [command[i + 1] for i, arg in enumerate(command) if arg == "--config"]
    args = ["codex"]
    for override in overrides:
        args.extend(["--config", override])
    result = subprocess.run(
        args + ["features", "list"], capture_output=True, text=True, check=True
    )
    features = {
        parts[0]: parts[-1]
        for line in result.stdout.splitlines()
        if len(parts := line.split()) >= 3
    }
    for name in [
        "shell_tool",
        "view_image",
        "apps",
        "plugins",
        "browser_use",
        "computer_use",
        "multi_agent",
        "hooks",
        "memories",
    ]:
        assert features[name] == "false", name
    assert features["skip_host_skill_discovery"] == "true"
    assert features["code_mode_host"] == "true"
    assert "project_doc_max_bytes=0" in overrides
    assert 'web_search="disabled"' in overrides


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI unavailable")
@pytest.mark.parametrize("with_mcp", [False, True])
def test_codex_request_excludes_auditor_instructions_and_host_tools(tmp_path, with_mcp):
    """Inspect the real CLI request using a local stub; no model is called."""
    canary = "AUDITOR_SOLUTION_BANK32_CANARY_7E0A"
    (tmp_path / "AGENTS.md").write_text(canary)
    (tmp_path / "Solution Contract.md").write_text(canary)
    workdir = tmp_path / "agent"
    workdir.mkdir()
    auditor = tmp_path / "auditor"
    auditor.mkdir()
    (auditor / "Solution Contract.md").write_text(canary)
    received = threading.Event()
    payloads = []
    mcp_methods = []
    request_paths = []

    class Capture(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/mcp":
                method = request.get("method")
                mcp_methods.append(method)
                if "id" not in request:
                    self.send_response(202)
                    self.end_headers()
                    return
                if method == "initialize":
                    result = {
                        "protocolVersion": request["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "Banking isolation fixture",
                            "version": "1",
                        },
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "read_fixture",
                                "description": "Read fixture",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ]
                    }
                else:
                    result = {}
                response = json.dumps(
                    {"jsonrpc": "2.0", "id": request["id"], "result": result}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
                return
            payloads.append(request)
            request_paths.append(self.path)
            received.set()
            self.send_response(503)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Capture)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    command = load_harness().build_codex_command(
        model="gpt-6.1-sol",
        effort="low",
        mcp_urls={"crm": f"http://127.0.0.1:{server.server_port}/mcp"}
        if with_mcp
        else {},
        workdir=workdir,
        auditor_roots=(auditor,),
    )
    command[-1:-1] = [
        "--config",
        'model_provider="isolation_stub"',
        "--config",
        'model_providers.isolation_stub.name="Local isolation stub"',
        "--config",
        f'model_providers.isolation_stub.base_url="http://127.0.0.1:{server.server_port}/v1"',
        "--config",
        'model_providers.isolation_stub.wire_api="responses"',
        "--config",
        "model_providers.isolation_stub.requires_openai_auth=false",
        "--config",
        "model_providers.isolation_stub.request_max_retries=0",
    ]
    process = None
    try:
        with (
            (tmp_path / "stdout").open("w") as stdout,
            (tmp_path / "stderr").open("w") as stderr,
        ):
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr
            )
            process.stdin.write(b"Isolation diagnostic. Do not use any tools.\n")
            process.stdin.close()
            assert received.wait(20), (tmp_path / "stderr").read_text()[-1000:]
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert canary not in json.dumps(payloads)
    if os.getenv("ISOLATION_DIAGNOSTICS"):
        Path(os.environ["ISOLATION_DIAGNOSTICS"]).write_text(
            json.dumps(
                {
                    "paths": request_paths,
                    "mcp_methods": mcp_methods,
                    "requests": payloads,
                    "stderr": (tmp_path / "stderr").read_text(),
                },
                indent=2,
            )
        )
    tools = [
        tool
        for item in payloads[0]["input"]
        if item.get("type") == "additional_tools"
        for tool in item["tools"]
    ]
    assert "<skills_instructions>" not in json.dumps(payloads)
    if with_mcp:
        assert "tools/list" in mcp_methods
    else:
        # Any tool without a configured MCP is an extra access channel.
        assert "tools/list" not in mcp_methods
    # Code-mode transport is allowed; host read/execute tools are absent.
    exec_tool = next(
        t
        for namespace in tools
        for t in namespace.get("tools", [])
        if t["name"] == "exec"
    )
    for forbidden in [
        "### `exec_command`",
        "### `view_image`",
        "## web",
        "## image_gen",
    ]:
        assert forbidden not in exec_tool["description"]


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI unavailable")
def test_permissions_deny_auditor_file_but_allow_agent_workspace(tmp_path):
    """Exercise the OS sandbox profile, rather than trusting prompt wording."""
    auditor = tmp_path / "auditor"
    auditor.mkdir()
    hidden = auditor / "Solution Contract.md"
    hidden.write_text("AUDITOR_ONLY_SOLUTION")
    workdir = tmp_path / "agent"
    workdir.mkdir()
    visible = workdir / "AGENTS.md"
    visible.write_text("VISIBLE_BANK_POLICY")
    command = load_harness().build_codex_command(
        model="gpt-6.1-sol",
        effort="low",
        mcp_urls={},
        workdir=workdir,
        auditor_roots=(auditor,),
    )
    args = ["codex", "sandbox", "-P", "banking_mcp_only", "-C", str(workdir)]
    for i, arg in enumerate(command):
        if arg == "--config":
            args.extend([arg, command[i + 1]])
    denied = subprocess.run(
        args + ["/bin/cat", str(hidden)], capture_output=True, text=True, timeout=20
    )
    allowed = subprocess.run(
        args + ["/bin/cat", str(visible)], capture_output=True, text=True, timeout=20
    )
    assert denied.returncode != 0, denied.stdout
    assert "AUDITOR_ONLY_SOLUTION" not in denied.stdout + denied.stderr
    assert allowed.returncode == 0, allowed.stderr
    assert allowed.stdout == "VISIBLE_BANK_POLICY"


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
