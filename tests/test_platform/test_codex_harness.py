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


def test_build_prompt_keeps_ticket_without_duplicating_startup():
    """The model must receive the whole task contract, not just the ticket."""
    harness = load_harness()

    prompt = harness.build_prompt(
        policy="POLICY: verify the customer before acting",
        ticket="TICKET: recover access for Ivan",
    )

    assert "POLICY: verify the customer before acting" not in prompt
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
        mcp_urls={"banking_ru_pilot": "http://test/mcp"},
        max_calls=100,
        max_errors=0,
        max_discovery_calls=None,
        mcp_layout="directory",
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


def test_main_rejects_model_launch_without_validation_receipts(tmp_path, monkeypatch):
    import argparse
    import asyncio

    harness = load_harness()
    called = []
    monkeypatch.setattr(harness, "PlatformClient", lambda *args: called.append(True))
    args = argparse.Namespace(work_root=tmp_path, preflight_dir=None)
    with pytest.raises(ValueError, match="preflight"):
        asyncio.run(harness.async_main(args))
    assert not called


def test_wrong_container_image_is_rejected_before_launch():
    harness = load_harness()
    with pytest.raises(ValueError, match="image"):
        harness.verify_container_image({"Image": "sha256:other"}, "sha256:verified")
    harness.verify_container_image({"Image": "sha256:verified"}, "sha256:verified")


def test_model_is_not_invoked_when_actual_image_differs(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    h = load_harness()
    calls = []
    fake = SimpleNamespace(
        prepare_container=lambda *args: {"Image": "sha256:wrong"},
        remove_container=lambda name: calls.append("removed"),
    )
    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
    monkeypatch.setattr(h.importlib.util, "spec_from_file_location", lambda *args: spec)
    monkeypatch.setattr(h.importlib.util, "module_from_spec", lambda arg: fake)

    async def no_model(*args):
        calls.append("model")

    monkeypatch.setattr(h, "invoke_codex", no_model)
    with pytest.raises(ValueError, match="image"):
        asyncio.run(
            h.invoke_isolated_codex(
                [], "prompt", 1, expected_image_id="sha256:verified"
            )
        )
    assert calls == ["removed"]


def test_journal_uses_persisted_platform_events(tmp_path):
    h = load_harness()
    token = "a" * 32
    item = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "id": "cli-1",
            "tool": "get_cards",
            "arguments": {"customer_id": "own"},
        },
    }
    record = {
        "task_id": "Bank 2",
        "token": token,
        "run": {"stdout": json.dumps(item), "stderr": "", "output_observations": []},
        "confirmed_external_refusal": False,
    }
    (tmp_path / f"{token}.json").write_text(
        json.dumps(
            {
                "id": token,
                "task_id": "Bank 2",
                "info": {
                    "mcp_events": [
                        {
                            "call_id": "mcp-3",
                            "original_name": "get_cards",
                            "canonical_name": "get_cards",
                            "arguments": {"customer_id": "own"},
                        }
                    ]
                },
            }
        )
    )
    h.attach_persisted_journal(record, tmp_path)
    event = record["attempt_journal"]["attempts"][0]
    assert event["delivered_to_mcp"] is True
    assert event["mcp_call_id"] == "mcp-3"


def test_journal_rejects_wrong_session_or_task(tmp_path):
    h = load_harness()
    token = "a" * 32
    record = {"task_id": "Bank 2", "token": token, "run": {"stdout": "", "stderr": ""}}
    (tmp_path / f"{token}.json").write_text(
        json.dumps({"id": token, "task_id": "Bank 40", "info": {"mcp_events": []}})
    )
    with pytest.raises(ValueError, match="provenance"):
        h.attach_persisted_journal(record, tmp_path)
    record["token"] = "../outside"
    with pytest.raises(ValueError, match="token"):
        h.attach_persisted_journal(record, tmp_path)


def test_supported_harness_requests_directory_before_any_model_call(tmp_path):
    import asyncio

    class Captured(Exception):
        pass

    class Client:
        async def open(self, task_id, **kwargs):
            assert kwargs["mcp_layout"] == "directory"
            raise Captured()

    with pytest.raises(Captured):
        asyncio.run(
            load_harness().run_one_attempt(
                client=Client(),
                task_id="Bank 2",
                model="unused",
                effort="low",
                timeout_seconds=1,
                work_root=tmp_path,
            )
        )


def test_directory_harness_round_trip_with_real_mcp_and_no_model(
    app, settings, tmp_path, monkeypatch
):
    import asyncio

    import httpx

    from tau2.platform.client import PlatformClient

    h = load_harness()
    observed = {}
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "scripts/banking_ru")
    )
    gates = h.load_preflight()
    surface_dir = tmp_path / "checked-surface"
    surface_dir.mkdir()
    (surface_dir / "AUTHOR_ONLY_canary.txt").write_text("AUTHOR_ONLY_roundtrip_fixture")
    gates.surface_child(
        {"task_id": "Bank 2", "model": {"max_calls": 100, "max_errors": 0}}, surface_dir
    )
    expected = gates.read(surface_dir / "surface-packet.json")

    async def go():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://platform"
            ) as http:
                client = PlatformClient("http://platform", http_client=http)
                original = client.open

                async def capture(*args, **kwargs):
                    opened = await original(*args, **kwargs)
                    observed["opened"] = opened
                    return opened

                client.open = capture

                async def local_stub(command, prompt, *args, **kwargs):
                    opened = observed["opened"]
                    assert opened.mcp_layout == "directory"
                    assert "bank_directory" in prompt and "bank_call" in prompt
                    async with client.connect(opened) as mcp:
                        actual = (await mcp.list_tools()).model_dump(mode="json")
                        gates.verify_live_surface(
                            expected,
                            opened.policy,
                            prompt,
                            {"banking_ru_pilot": actual},
                            [],
                            configuration=gates.session_configuration(opened),
                        )
                        assert {t.name for t in (await mcp.list_tools()).tools} == {
                            "bank_directory",
                            "bank_call",
                        }
                        assert (
                            await mcp.call_tool("file_read", {"path": "/AGENTS.md"})
                        ).is_error
                        page = await mcp.call_tool(
                            "bank_directory", {"operation": "list_systems"}
                        )
                        assert len(json.loads(page.content[0].text)["items"]) == 5
                        for name in ("list_documents", "read_document"):
                            assert not (
                                await mcp.call_tool(
                                    "bank_directory",
                                    {
                                        "operation": "open_tool",
                                        "system_id": "knowledge",
                                        "tool_name": name,
                                    },
                                )
                            ).is_error
                        cards = await mcp.call_tool(
                            "bank_call",
                            {
                                "system_id": "knowledge",
                                "tool_name": "list_documents",
                                "arguments": {},
                            },
                        )
                        card = json.loads(cards.content[0].text)["items"][0]
                        result = await mcp.call_tool(
                            "bank_call",
                            {
                                "system_id": "knowledge",
                                "tool_name": "read_document",
                                "arguments": {
                                    "document_id": card["document_id"],
                                    "revision_id": card["revision_id"],
                                },
                            },
                        )
                        assert (
                            not result.is_error
                            and json.loads(result.content[0].text)["content"]
                        )
                        assert not (
                            await mcp.call_tool(
                                "bank_directory",
                                {
                                    "operation": "open_tool",
                                    "system_id": "support",
                                    "tool_name": "done",
                                },
                            )
                        ).is_error
                        assert not (
                            await mcp.call_tool(
                                "bank_call",
                                {
                                    "system_id": "support",
                                    "tool_name": "done",
                                    "arguments": {},
                                },
                            )
                        ).is_error
                    return {
                        "stdout": "",
                        "stderr": "",
                        "output_observations": [],
                        "timed_out": False,
                        "exit_code": 0,
                    }

                monkeypatch.setattr(h, "invoke_isolated_codex", local_stub)
                return await h.run_one_attempt(
                    client=client,
                    task_id="Bank 2",
                    model="local-stub",
                    effort="low",
                    timeout_seconds=5,
                    work_root=tmp_path,
                )

    record = asyncio.run(go())
    assert record["mcp_layout"] == "directory" and record["state"]["status"] == "done"
    assert not record["technical_failure"] and record["final_reward"] == 0
    assert record["discovery"]["calls"] == 5
    h.attach_persisted_journal(record, settings.results_dir)
    saved = json.loads((settings.results_dir / (record["token"] + ".json")).read_text())
    events = saved["info"]["mcp_events"]
    assert any(e.get("canonical_name") == "read_document" for e in events)
    assert all("response_order" in e for e in events)
    assert record["platform_event_status"] == "linked"


def test_harness_closes_wrong_layout_before_invoking_runtime(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    h = load_harness()
    closed = []

    class Client:
        async def open(self, *args, **kwargs):
            return SimpleNamespace(
                token="fixture", mcp_layout="systems", mcp_urls={"crm": "http://test"}
            )

        async def close(self, token):
            closed.append(token)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Runtime must not be invoked")

    monkeypatch.setattr(h, "invoke_isolated_codex", forbidden)
    result = asyncio.run(
        h.run_one_attempt(
            client=Client(),
            task_id="Bank 2",
            model="unused",
            effort="low",
            timeout_seconds=1,
            work_root=tmp_path,
        )
    )
    assert result["classification"] == "invalid_harness" and closed == ["fixture"]
