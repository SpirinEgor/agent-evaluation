"""Codex/V1 A2A 0.3 adapter for eval-platform datapart-tools@v2.

Only the coordinator executes bank tools. The persistent isolated runtime uses a
queue transport instead of a direct MCP connection. No agent startup files.
"""

import argparse
import asyncio
import hmac
import importlib.util
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from tau2.platform.agents import profile, prompt_for


class TextResult:
    def __init__(self, text):
        self.content = [SimpleNamespace(type="text", text=text)]
        # datapart-tools@v2 carries content, not an MCP isError flag.
        self.is_error = False

    def model_dump(self, **kwargs):
        return {
            "content": [{"type": "text", "text": self.content[0].text}],
            "isError": False,
        }


class Exchange:
    def __init__(self, setup):
        self.id, self.context = uuid4().hex, uuid4().hex
        self.setup = setup
        self.outgoing = asyncio.Queue()
        self.pending = {}
        self.events = []
        self.lock = asyncio.Lock()
        self.job = None
        self.ended = False

    async def list_tools(self):
        from mcp.types import Tool

        return SimpleNamespace(
            tools=[
                Tool(
                    name=t["function"]["name"],
                    description=t["function"]["description"],
                    inputSchema=t["function"]["parameters"],
                )
                for t in self.setup["tools"]
            ]
        )

    async def call_tool(self, name, arguments):
        if self.ended:
            raise RuntimeError("Run already ended")
        call_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[call_id] = future
        self.events.append(
            {
                "kind": "tool_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            }
        )
        await self.outgoing.put(
            (
                "input-required",
                [
                    {
                        "kind": "data",
                        "data": {
                            "kind": "tool_calls",
                            "calls": [
                                {"id": call_id, "name": name, "arguments": arguments}
                            ],
                        },
                    }
                ],
            )
        )
        try:
            return TextResult(await future)
        finally:
            self.pending.pop(call_id, None)

    def receive(self, rows):
        if not isinstance(rows, list) or len(rows) != len(self.pending):
            raise ValueError("Expected exactly one result for every pending call")
        ids = [r["call_id"] for r in rows]
        if len(set(ids)) != len(ids) or set(ids) != set(self.pending):
            raise ValueError("Unknown, duplicate or missing call_id")
        if any(not isinstance(r["content"], str) for r in rows):
            raise ValueError("Result content must be text")
        for r in rows:
            f = self.pending[r["call_id"]]
            if f.done():
                raise ValueError("Result already delivered")
        for r in rows:
            self.events.append({"kind": "tool_result_received", **r})
            self.pending[r["call_id"]].set_result(r["content"])


def load_harness():
    path = (
        Path(__file__).resolve().parents[3] / "scripts/banking_ru/run_codex_harness.py"
    )
    spec = importlib.util.spec_from_file_location("banking_a2a_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def codex_engine(*, model, effort, image, agent_profile, timeout_seconds):
    profile(agent_profile)
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("A verified immutable image ID is required")

    async def run(exchange):
        h = load_harness()
        setup = exchange.setup
        rules = setup["rules"]
        # A2A rules count turns, not banking calls. Do not relabel max_turns.
        prompt = "\n\n".join(
            [
                "Выполните банковское поручение через bank_directory и bank_call. "
                "Все банковские действия исполняет платформа. Завершите через bank_call done.",
                "Правила запуска платформы: " + json.dumps(rules, ensure_ascii=False),
                setup.get("opening") or "",
            ]
        )
        limit = float(rules["run_timeout"][:-1])
        if limit > timeout_seconds:
            raise ValueError(
                "Platform run_timeout exceeds the configured adapter capacity"
            )
        return await h.invoke_isolated_codex(
            h.build_codex_command(
                model=model,
                effort=effort,
                mcp_urls={"coordinator": "http://127.0.0.1:1/not-used"},
                workdir=Path("/workspace"),
            ),
            prompt_for(prompt, agent_profile),
            limit,
            image,
            setup["instructions"],
            expected_image_id=image,
            agent_profile=agent_profile,
            tool_transport=exchange,
        )

    return run


def build_app(
    *,
    endpoint,
    engine,
    agent_profile="v1",
    heartbeat_seconds=5,
    max_sessions=1,
    bearer=None,
    artifacts_dir=None,
):
    profile(agent_profile)
    if not 0 < heartbeat_seconds <= 5 or max_sessions < 1:
        raise ValueError("Invalid heartbeat interval or session capacity")
    sessions = {}
    artifacts = Path(artifacts_dir) if artifacts_dir else None

    async def stop(e):
        e.ended = True
        if e.job and not e.job.done():
            e.job.cancel()
        if e.job:
            await asyncio.gather(e.job, return_exceptions=True)
        sessions.pop(e.id, None)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.gather(*(stop(e) for e in list(sessions.values())))

    app = FastAPI(lifespan=lifespan)

    async def execute(e):
        try:
            result = await engine(e)
            if artifacts:
                artifacts.mkdir(parents=True, exist_ok=True)
                (artifacts / (e.id + ".json")).write_text(
                    json.dumps(
                        {"runtime": result, "a2a_events": e.events}, ensure_ascii=False
                    )
                )
            # Model completion alone does not complete the benchmark.
            if not e.ended:
                await e.outgoing.put(("failed", []))
        except asyncio.CancelledError as error:
            if artifacts:
                artifacts.mkdir(parents=True, exist_ok=True)
                (artifacts / (e.id + ".json")).write_text(
                    json.dumps(
                        {
                            "status": "canceled",
                            "runtime": getattr(error, "partial_run", None),
                            "pending_call_ids": list(e.pending),
                            "a2a_events": e.events,
                        }
                    )
                )
            raise
        except Exception as error:
            if artifacts:
                artifacts.mkdir(parents=True, exist_ok=True)
                (artifacts / (e.id + ".json")).write_text(
                    json.dumps(
                        {
                            "status": "failed",
                            "runtime": getattr(error, "partial_run", None),
                            "a2a_events": e.events,
                        }
                    )
                )
            await e.outgoing.put(("failed", []))
        finally:
            if not e.ended:
                sessions.pop(e.id, None)

    @app.get("/.well-known/agent-card.json")
    async def card():
        return dict(
            name="banking-codex-" + agent_profile,
            description="Isolated banking Codex agent",
            version="1",
            protocolVersion="0.3.0",
            url=endpoint,
            capabilities={
                "streaming": True,
                "extensions": [{"uri": "datapart-tools@v2", "required": True}],
            },
            defaultInputModes=["text", "data"],
            defaultOutputModes=["text", "data"],
            skills=[
                {
                    "id": "banking",
                    "name": "Banking",
                    "description": "Solve banking tasks",
                    "tags": ["banking"],
                }
            ],
            securitySchemes={"bearer": {"type": "http", "scheme": "bearer"}}
            if bearer
            else {},
            security=[{"bearer": []}] if bearer else [],
        )

    def event(e, state, parts, final, request_id):
        status = {"state": state}
        if parts:
            status["message"] = {
                "kind": "message",
                "role": "agent",
                "messageId": uuid4().hex,
                "taskId": e.id,
                "contextId": e.context,
                "parts": parts,
            }
        return (
            "data: "
            + json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "kind": "status-update",
                        "taskId": e.id,
                        "contextId": e.context,
                        "status": status,
                        "final": final,
                    },
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )

    @app.post("/a2a")
    async def rpc(request: Request):
        if bearer and not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + bearer
        ):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        body = await request.body()
        if len(body) > 2 * 1024 * 1024:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        request_id = None
        e = None
        acquired = False
        try:
            envelope = json.loads(body)
            if not isinstance(envelope, dict):
                raise ValueError("Expected an object")
            request_id = envelope.get("id")
            if envelope.get("jsonrpc") != "2.0":
                raise ValueError("JSON-RPC 2.0 required")
            method, params = envelope["method"], envelope["params"]
            if method == "tasks/cancel":
                e = sessions[params["id"]]
                if e.lock.locked():
                    # Cancellation is allowed while a streamed turn is active.
                    await e.outgoing.put(("canceled", []))
                await stop(e)
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "kind": "task",
                        "id": e.id,
                        "contextId": e.context,
                        "status": {"state": "canceled"},
                    },
                }
            if method != "message/stream":
                raise ValueError("Unsupported method")
            msg = params["message"]
            if msg.get("role") != "user":
                raise ValueError("Platform message must have role user")
            parts = msg["parts"]
            data = {}
            texts = []
            for part in parts:
                if part.get("kind") == "text":
                    texts.append(part["text"])
                elif part.get("kind") == "data":
                    value = part["data"]
                    if not isinstance(value, dict):
                        raise ValueError("Expected DataPart object")
                    kind = value.get("kind")
                    if kind in {"run_setup", "tool_results", "run_end"}:
                        if kind in data:
                            raise ValueError("Duplicate data kind")
                        data[kind] = value
            if not msg.get("taskId"):
                if (
                    set(data) != {"run_setup"}
                    or len(texts) > 1
                    or len(sessions) >= max_sessions
                ):
                    raise ValueError("Expected one new setup within capacity")
                setup = data["run_setup"]
                if not isinstance(setup["instructions"], str) or not isinstance(
                    setup["tools"], list
                ):
                    raise ValueError("Invalid setup")
                names = [t["function"]["name"] for t in setup["tools"]]
                if sorted(names) != ["bank_call", "bank_directory"]:
                    raise ValueError("Expected exactly the two banking tools")
                for t in setup["tools"]:
                    if (
                        t["type"] != "function"
                        or not isinstance(t["function"]["parameters"], dict)
                        or not isinstance(t["function"]["description"], str)
                    ):
                        raise ValueError("Invalid tool schema")
                rules = setup["rules"]
                if (
                    type(rules["max_turns"]) is not int
                    or rules["max_turns"] < 1
                    or not re.fullmatch(r"\d+(?:\.\d+)?s", rules["run_timeout"])
                    or float(rules["run_timeout"][:-1]) <= 0
                    or "tools" not in rules["channels"]
                    or rules["parallel_tool_calls"] not in {"allow", "reject"}
                ):
                    raise ValueError("Invalid run rules")
                e = Exchange({**setup, "opening": texts[0] if texts else None})
                sessions[e.id] = e
            else:
                e = sessions[msg["taskId"]]
                if (
                    msg.get("contextId") != e.context
                    or e.lock.locked()
                    or "run_setup" in data
                    or texts
                ):
                    raise ValueError("Invalid or concurrent continuation")
                if not data or set(data) - {"tool_results", "run_end"}:
                    raise ValueError("Expected results or run_end")
            await e.lock.acquire()
            acquired = True
            if "run_end" in data and data["run_end"]["reason"] not in {
                "done",
                "max_turns",
                "run_timeout",
            }:
                raise ValueError("Unknown end reason")
            if "tool_results" in data:
                e.receive(data["tool_results"]["results"])
            if "run_end" in data:
                if data["run_end"]["reason"] not in {
                    "done",
                    "max_turns",
                    "run_timeout",
                }:
                    raise ValueError("Unknown end reason")
                e.ended = True
            if e.job is None:
                e.job = asyncio.create_task(execute(e))
        except (ValueError, KeyError, TypeError, AttributeError):
            if acquired:
                e.lock.release()
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid request, task or call IDs",
                    },
                }
            )

        async def stream():
            try:
                yield event(e, "working", [], False, request_id)
                if e.ended:
                    # Results of the final action have been delivered first.
                    try:
                        await asyncio.wait_for(asyncio.shield(e.job), 5)
                    except TimeoutError:
                        pass
                    await stop(e)
                    yield event(e, "completed", [], True, request_id)
                    return
                while True:
                    try:
                        state, result_parts = await asyncio.wait_for(
                            e.outgoing.get(), heartbeat_seconds
                        )
                    except TimeoutError:
                        yield event(e, "working", [], False, request_id)
                        continue
                    yield event(e, state, result_parts, True, request_id)
                    if state in {"failed", "canceled"}:
                        await stop(e)
                    return
            except asyncio.CancelledError:
                await stop(e)
                raise
            finally:
                if e.lock.locked():
                    e.lock.release()

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def main():
    import uvicorn

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9002)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--preflight-spec", type=Path, required=True)
    p.add_argument("--model", default="gpt-6.1-sol")
    p.add_argument("--effort", default="low")
    p.add_argument("--profile", choices=["baseline", "v1"], default="v1")
    p.add_argument("--artifacts-dir", type=Path, required=True)
    a = p.parse_args()
    spec = json.loads(a.preflight_spec.read_text())
    selected = spec["model"]
    if (
        selected["name"],
        selected["effort"],
        selected["image"],
        spec.get("agent_profile", "baseline"),
    ) != (a.model, a.effort, a.image, a.profile):
        raise ValueError("Runtime differs from the verified preflight specification")
    load_harness().load_preflight().boundary_check(spec)
    engine = codex_engine(
        model=a.model,
        effort=a.effort,
        image=a.image,
        agent_profile=a.profile,
        timeout_seconds=900,
    )
    uvicorn.run(
        build_app(
            endpoint=a.endpoint,
            engine=engine,
            agent_profile=a.profile,
            bearer=os.getenv("BANKING_A2A_TOKEN"),
            artifacts_dir=a.artifacts_dir,
        ),
        host=a.host,
        port=a.port,
    )


if __name__ == "__main__":
    main()
