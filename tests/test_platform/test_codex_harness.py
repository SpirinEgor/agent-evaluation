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
    assert "--dangerously-bypass-approvals-and-sandbox" in command
    assert not any(
        c.startswith(
            (
                "approval_policy=",
                "approvals_reviewer=",
                "default_permissions=",
                "permissions.",
            )
        )
        for c in configs
    )
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


def test_full_access_environment_corpus_still_denies_author_files():
    """Corpus paths stay confined even when Codex has no local sandbox."""
    from tau2.domains.banking_ru.file_corpus import FileCorpus

    corpus = FileCorpus(Path(__file__).parents[2] / "data/tau2/domains/banking_ru")
    with pytest.raises(ValueError, match="canonical absolute corpus path"):
        corpus.read("/../tasks.json")
    for path in ["/etc/passwd", "/Solution Contract.md"]:
        with pytest.raises(ValueError, match="Corpus file not found"):
            corpus.read(path)


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


def test_external_refusal_counts_as_failure_even_if_recovered():
    harness = load_harness()
    assert (
        harness.classify_result(
            {"status": "done"},
            {"reward": 1.0},
            timed_out=False,
            exit_code=0,
            closed_by_harness=False,
            external_refusals=1,
        )
        == "codex_refusal_fail"
    )


def test_attempt_journal_preserves_unknown_arguments_and_masks_secrets():
    harness = load_harness()
    transcript = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "mcp_tool_call",
                        "id": "call1",
                        "server": "identity",
                        "tool": "verify_identity",
                        "arguments": {
                            "customer_id": "owner",
                            "credential": "secret-word",
                        },
                        "timestamp": "2026-10-14T10:00:00Z",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "id": "call1",
                        "server": "identity",
                        "tool": "verify_identity",
                        "error": {
                            "message": "Tool execution rejected. Reason: private credential"
                        },
                    },
                }
            ),
        ]
    )
    journal = harness.collect_attempt_journal(transcript, "", [])
    assert journal["external_refusals"] == 1
    attempt = journal["attempts"][0]
    assert attempt["id"] == "call1"
    assert attempt["arguments"] == {"customer_id": "owner", "credential": "[REDACTED]"}
    assert attempt["delivered_to_mcp"] is False
    unknown = harness.collect_attempt_journal(
        "", "Tool execution rejected. Reason: refund_fee amount 199 forbidden", []
    )
    assert unknown["external_refusals"] == 1
    assert unknown["attempts"][0]["arguments"] is None
    assert unknown["attempts"][0]["tool"] is None


def test_invocation_timestamps_output_without_a_model():
    import asyncio
    import sys

    result = asyncio.run(
        load_harness().invoke_codex(
            [
                sys.executable,
                "-c",
                'import sys; sys.stdin.read(); print("event"); print("Tool execution rejected. Reason: blocked", file=sys.stderr)',
            ],
            "fixture",
            10,
        )
    )
    assert result["exit_code"] == 0
    assert result["stdout"] == "event\n"
    assert result["stderr"] == "Tool execution rejected. Reason: blocked\n"
    assert {o["channel"] for o in result["output_observations"]} == {"stdout", "stderr"}
    journal = load_harness().collect_attempt_journal(
        result["stdout"], result["stderr"], [], result["output_observations"]
    )
    attempt = journal["attempts"][0]
    observation = next(
        o for o in result["output_observations"] if o["channel"] == "stderr"
    )
    assert attempt["observed_at"] == observation["observed_at"]
    assert attempt["timestamp"] is None


def test_infrastructure_reason_is_not_an_external_refusal():
    journal = load_harness().collect_attempt_journal(
        "", "MCP connection failed. Reason: connection reset by peer", []
    )
    assert journal["external_refusals"] == 0


def test_repeated_external_rejections_are_preserved():
    journal = load_harness().collect_attempt_journal(
        "",
        "Tool execution rejected. Reason: policy\nTool execution rejected. Reason: policy",
        [],
    )
    assert journal["external_refusals"] == 2
    assert len(journal["attempts"]) == 2


@pytest.mark.parametrize(
    "stderr",
    [
        "Tool execution rejected",
        "This action was rejected due to unacceptable risk.",
        "Tool execution rejected\nReason: policy",
    ],
)
def test_rejection_without_explanation_is_a_failed_attempt(stderr):
    harness = load_harness()
    journal = harness.collect_attempt_journal("", stderr, [])
    assert journal["external_refusals"] == 1
    assert journal["attempts"][0]["arguments"] is None
    assert (
        harness.classify_result(
            {"status": "done"},
            {"reward": 1.0},
            timed_out=False,
            exit_code=0,
            closed_by_harness=False,
            external_refusals=journal["external_refusals"],
        )
        == "codex_refusal_fail"
    )


@pytest.mark.parametrize(
    "transcript,observed_complete",
    [
        ("", None),
        (
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "mcp_tool_call",
                        "id": "one",
                        "tool": "done",
                        "arguments": {},
                    },
                }
            ),
            True,
        ),
        (
            json.dumps(
                {
                    "type": "item.started",
                    "item": {
                        "type": "mcp_tool_call",
                        "id": "one",
                        "tool": "done",
                    },
                }
            ),
            False,
        ),
    ],
)
def test_observed_fields_do_not_verify_interception(transcript, observed_complete):
    journal = load_harness().collect_attempt_journal(transcript, "", [])
    assert journal["argument_capture_complete"] is None
    assert journal["argument_capture_status"] == "unverified"
    assert journal["observed_argument_fields_complete"] is observed_complete


def test_approval_rejection_preserves_reason_and_is_external_not_subject():
    harness = load_harness()
    reason = "MCP tool call requires approval, but approval policy is never"
    item = {
        "id": "approval-call",
        "type": "mcp_tool_call",
        "server": "support",
        "tool": "done",
        "arguments": {},
        "result": None,
        "error": {"message": reason},
        "status": "failed",
    }
    journal = harness.collect_attempt_journal(
        json.dumps({"type": "item.completed", "item": item}), "", []
    )
    assert journal["external_refusals"] == 1
    row = journal["attempts"][0]
    assert row["rejection_source"] == "approval_policy"
    assert row["blocking_layer"] == "codex_approval"
    assert row["reason"] == reason
    assert row["timestamp"] is None
    assert row["delivered_to_mcp"] is False
    assert row["arguments"] == {}
    # A subject response mentioning the same words is not an external denial.
    item.update(
        id="subject-call",
        error=None,
        result={"content": [{"type": "text", "text": reason}], "isError": True},
    )
    subject = harness.collect_attempt_journal(
        json.dumps({"type": "item.completed", "item": item}), "", []
    )
    assert subject["external_refusals"] == 0
    assert subject["attempts"][0]["rejection_source"] is None
    assert subject["attempts"][0]["blocking_layer"] == "mcp_result"
    assert subject["attempts"][0]["delivered_to_mcp"] is True


def test_stderr_approval_denial_and_guardian_have_distinct_sources():
    journal = load_harness().collect_attempt_journal(
        "",
        "MCP tool call requires approval, but approval policy is never\n"
        "Tool execution rejected. Reason: unacceptable risk\n",
        [],
    )
    assert journal["external_refusals"] == 2
    assert [r["rejection_source"] for r in journal["attempts"]] == [
        "approval_policy",
        "external_review",
    ]
    assert journal["attempts"][0]["reason"] == (
        "MCP tool call requires approval, but approval policy is never"
    )
    assert journal["attempts"][0]["arguments"] is None


@pytest.mark.parametrize("profile", [False, True])
def test_approval_denial_keeps_attempt_zero_after_done_and_banking_one(
    tmp_path, monkeypatch, profile
):
    from types import SimpleNamespace

    harness = load_harness()
    opened = SimpleNamespace(
        policy="common policy",
        ticket="ticket",
        token="test-token",
        mcp_urls={},
        max_calls=60,
        max_errors=5,
        mcp_layout="systems",
    )

    class Client:
        async def open(self, *args, **kwargs):
            return opened

        async def state(self, token):
            return {"token": token, "status": "done"}

        async def result(self, token):
            return {"reward": 1.0, "business_process_only": profile}

        async def close(self, token):
            pass

    async def captured(*args):
        return {
            "stdout": json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "one",
                        "type": "mcp_tool_call",
                        "tool": "file_list",
                        "arguments": {},
                        "error": {
                            "message": "MCP tool call requires approval, but approval policy is never"
                        },
                    },
                }
            ),
            "stderr": "",
            "output_observations": [],
            "timed_out": False,
            "exit_code": 0,
        }

    monkeypatch.setattr(harness, "invoke_isolated_codex", captured)
    import asyncio

    record = asyncio.run(
        harness.run_one_attempt(
            client=Client(),
            task_id="transport-fixture",
            model="gpt-6.1-sol",
            effort="low",
            timeout_seconds=1,
            work_root=tmp_path,
        )
    )
    assert record["result"]["reward"] == 1.0
    assert record["attempt_reward"] == 0.0
    assert record["classification"] == "codex_refusal_fail"
    assert record["bank_reward"] == 1.0
    assert record["confirmed_external_refusal"] is True
    assert record["final_reward"] == 0.0
    assert record["technical_failure"] is False
    assert record["event_observation"]["argument_capture_complete"] is None


def test_denial_time_is_observation_of_error_not_call_start():
    start = {
        "type": "item.started",
        "item": {"id": "one", "type": "mcp_tool_call", "tool": "done", "arguments": {}},
    }
    end = {
        "type": "item.completed",
        "item": {
            "id": "one",
            "type": "mcp_tool_call",
            "error": {
                "message": "MCP tool call requires approval, but approval policy is never"
            },
        },
    }
    transcript = json.dumps(start) + "\n" + json.dumps(end)
    journal = load_harness().collect_attempt_journal(
        transcript,
        "",
        [],
        [
            {"channel": "stdout", "line": 1, "observed_at": "2026-10-14T10:00:00Z"},
            {"channel": "stdout", "line": 2, "observed_at": "2026-10-14T10:00:01Z"},
        ],
    )
    assert journal["attempts"][0]["rejection_observed_at"] == "2026-10-14T10:00:01Z"
    unknown = load_harness().collect_attempt_journal(transcript, "", [])
    assert unknown["attempts"][0]["rejection_observed_at"] is None


@pytest.mark.parametrize("reward,want", [(1.0, "luna_pass"), (0.0, "luna_fail")])
def test_business_result_is_independent_of_missing_done(reward, want):
    result = {"reward": reward, "business_process_only": True}
    status = load_harness().classify_result(
        {"status": "open"},
        result,
        timed_out=False,
        exit_code=0,
        closed_by_harness=True,
    )
    assert status == want


def test_business_profile_does_not_hide_infrastructure_failure():
    status = load_harness().classify_result(
        {"status": "open"},
        {"reward": 1.0, "business_process_only": True},
        timed_out=True,
        exit_code=0,
        closed_by_harness=True,
    )
    assert status == "invalid_timeout"


@pytest.mark.parametrize(
    "initial_status,closed", [("open", True), ("budget_exhausted", False)]
)
def test_finish_attempt_preserves_protocol_state(initial_status, closed):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    state = {"status": initial_status, "token": "fixture-token"}
    calls = []

    class Client:
        @asynccontextmanager
        async def connect(self, opened, server):
            yield self

        async def call_tool(self, name, arguments):
            calls.append(name)

        async def result(self, token):
            return {"reward": 1.0, "business_process_only": True}

        async def close(self, token):
            calls.append("close")

    opened = SimpleNamespace(server_for=lambda name: "support")
    result = asyncio.run(load_harness().finish_attempt(Client(), opened, state))
    assert result["closed_by_harness"] is closed
    assert result["state_before_cleanup"] == state
    assert state["status"] == initial_status
    assert calls == (["done", "close"] if closed else ["close"])
