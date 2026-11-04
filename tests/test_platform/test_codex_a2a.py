"""Real HTTP A2A coordinator exchanges, with a model-free solver stub."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn

from tau2.platform.a2a import build_app


def setup():
    return {
        "role": "user",
        "messageId": "first",
        "parts": [
            {
                "kind": "data",
                "data": {
                    "kind": "run_setup",
                    "instructions": "BANK INSTRUCTIONS",
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": n,
                                "description": n,
                                "parameters": {"type": "object"},
                            },
                        }
                        for n in ["bank_directory", "bank_call"]
                    ],
                    "rules": {
                        "max_turns": 100,
                        "run_timeout": "60s",
                        "channels": ["tools"],
                        "parallel_tool_calls": "reject",
                    },
                },
            },
            {"kind": "text", "text": "BANK TICKET"},
        ],
    }


@asynccontextmanager
async def serve(app):
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    job = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=10
        ) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(job, 5)
        sock.close()


async def send(client, message, method="message/stream"):
    async with client.stream(
        "POST",
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "req",
            "method": method,
            "params": {"message": message},
        },
    ) as r:
        assert r.status_code == 200
        events = []
        async for line in r.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:])["result"])
        return events


def reply(event, data):
    return {
        "role": "user",
        "taskId": event["taskId"],
        "contextId": event["contextId"],
        "messageId": "next",
        "parts": [{"kind": "data", "data": x} for x in data],
    }


@pytest.mark.parametrize("name", ["baseline", "v1"])
@pytest.mark.parametrize("cancel_after_done", [False, True])
def test_setup_results_final_receipt_and_no_direct_mcp(
    name, tmp_path, cancel_after_done
):
    async def go():
        observed = []

        async def engine(e):
            assert e.setup["instructions"] == "BANK INSTRUCTIONS"
            assert e.setup["opening"] == "BANK TICKET"
            assert [x.name for x in (await e.list_tools()).tools] == [
                "bank_directory",
                "bank_call",
            ]
            await asyncio.sleep(0.03)  # prove a heartbeat is sent before readiness
            observed.append(
                (await e.call_tool("bank_directory", {"operation": "list_systems"}))
                .content[0]
                .text
            )
            observed.append(
                (
                    await e.call_tool(
                        "bank_call",
                        {"system_id": "support", "tool_name": "done", "arguments": {}},
                    )
                )
                .content[0]
                .text
            )
            if cancel_after_done:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError as error:
                    error.partial_run = {
                        "exit_code": 1,
                        "cancelled": True,
                        "stdout": "DONE RECEIPT",
                        "broker_evidence": {"events": ["saved"]},
                        "workbench_events": [{"output": "73"}],
                    }
                    raise
            return {"exit_code": 0, "observed": observed}

        app = build_app(
            endpoint="https://agent.example/a2a",
            engine=engine,
            agent_profile=name,
            heartbeat_seconds=0.01,
            artifacts_dir=tmp_path,
        )
        async with serve(app) as client:
            card = (await client.get("/.well-known/agent-card.json")).json()
            assert card["protocolVersion"] == "0.3.0"
            assert card["capabilities"]["extensions"][0]["uri"] == "datapart-tools@v2"
            events = await send(client, setup())
            assert any(not x["final"] for x in events)
            first = events[-1]
            assert first["status"]["state"] == "input-required"
            call = first["status"]["message"]["parts"][0]["data"]["calls"][0]
            second = (
                await send(
                    client,
                    reply(
                        first,
                        [
                            {
                                "kind": "tool_results",
                                "results": [
                                    {
                                        "call_id": call["id"],
                                        "content": "DIRECTORY RESULT",
                                    }
                                ],
                            }
                        ],
                    ),
                )
            )[-1]
            call2 = second["status"]["message"]["parts"][0]["data"]["calls"][0]
            assert call2["name"] == "bank_call"
            final = (
                await send(
                    client,
                    reply(
                        second,
                        [
                            {
                                "kind": "tool_results",
                                "results": [
                                    {"call_id": call2["id"], "content": "DONE RECEIPT"}
                                ],
                            },
                            {"kind": "run_end", "reason": "done"},
                        ],
                    ),
                )
            )[-1]
            assert final["status"]["state"] == "completed"
            assert observed == ["DIRECTORY RESULT", "DONE RECEIPT"]
            assert len(list(tmp_path.glob("*.json"))) == 1
            saved = json.loads(next(tmp_path.glob("*.json")).read_text())
            if cancel_after_done:
                assert saved["status"] == "canceled"
                assert saved["runtime"]["cancelled"]
                assert saved["runtime"]["broker_evidence"]["events"] == ["saved"]
                assert saved["runtime"]["workbench_events"] == [{"output": "73"}]
                assert saved["a2a_events"][-1]["content"] == "DONE RECEIPT"
            else:
                assert saved["runtime"]["exit_code"] == 0
            assert not list(tmp_path.rglob("AGENTS.md"))

    asyncio.run(go())


def test_reject_wrong_result_context_and_cancel():
    async def go():
        cleaned = asyncio.Event()

        async def engine(e):
            try:
                await e.call_tool("bank_call", {"tool_name": "pending"})
            finally:
                cleaned.set()

        async with serve(
            build_app(endpoint="https://agent.example/a2a", engine=engine)
        ) as client:
            event = (await send(client, setup()))[-1]
            bad = reply(
                event,
                [
                    {
                        "kind": "tool_results",
                        "results": [{"call_id": "foreign", "content": "forged"}],
                    }
                ],
            )
            r = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "message/stream",
                    "params": {"message": bad},
                },
            )
            assert r.json()["error"]["code"] == -32602
            bad["contextId"] = "another-context"
            r = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "message/stream",
                    "params": {"message": bad},
                },
            )
            assert "error" in r.json()
            r = await client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tasks/cancel",
                    "params": {"id": event["taskId"]},
                },
            )
            assert r.json()["result"]["status"]["state"] == "canceled"
            assert cleaned.is_set()

    asyncio.run(go())


@pytest.mark.parametrize("agent_profile", ["baseline", "v1"])
def test_codex_engine_uses_api_setup_and_queue_transport(monkeypatch, agent_profile):
    from types import SimpleNamespace

    from tau2.platform import a2a

    captured = {}

    async def invoke(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return {"exit_code": 0}

    monkeypatch.setattr(
        a2a,
        "load_harness",
        lambda: SimpleNamespace(
            build_codex_command=lambda **kw: ["codex"],
            invoke_isolated_codex=invoke,
        ),
    )

    async def go():
        message = setup()
        data = message["parts"][0]["data"]
        data["instructions"] = (
            "BANK INSTRUCTIONS\nБанковский лимит: 50 вызовов. "
            "Discovery: отдельный бюджет, числовой лимит не установлен."
        )
        data["rules"]["max_turns"] = 500
        exchange = a2a.Exchange({**data, "opening": "TICKET FROM API"})
        run = a2a.codex_engine(
            model="gpt-6.1-sol",
            effort="low",
            image="sha256:" + "a" * 64,
            agent_profile=agent_profile,
            timeout_seconds=900,
        )
        await run(exchange)
        assert captured["args"][4] == data["instructions"]
        assert '"max_turns": 500' in captured["args"][1]
        assert "TICKET FROM API" in captured["args"][1]
        assert captured["args"][2] == 60
        assert captured["kwargs"]["tool_transport"] is exchange
        assert captured["kwargs"]["agent_profile"] == agent_profile
        assert ("Codex Workbench V1" in captured["args"][1]) == (agent_profile == "v1")

    asyncio.run(go())


def test_authorization_and_model_completion_does_not_claim_business_success(tmp_path):
    async def go():
        async def engine(e):
            return {"exit_code": 0}

        app = build_app(
            endpoint="https://agent.example/a2a",
            engine=engine,
            bearer="test-only",
            artifacts_dir=tmp_path,
        )
        async with serve(app) as client:
            r = await client.post("/a2a", json={})
            assert r.status_code == 401
            client.headers["Authorization"] = "Bearer test-only"
            events = await send(client, setup())
            assert events[-1]["status"]["state"] == "failed"
            # A closed failed run must not occupy the single session slot.
            second = await send(client, setup())
            assert second[-1]["status"]["state"] == "failed"
            assert len(list(tmp_path.glob("*.json"))) == 2

    asyncio.run(go())
