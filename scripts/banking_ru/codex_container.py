"""Externally isolate full-access Codex; never expose host files to apply_patch."""

import asyncio
import importlib.util
import json
import subprocess
import time
from contextlib import AsyncExitStack
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_IMAGE = "banking-codex-isolated:0.166.0-alpha.5.1"

BANK_TOOLS = {"bank_directory", "bank_call"}


def broker_module():
    spec = importlib.util.spec_from_file_location(
        "banking_codex_broker", Path(__file__).with_name("codex_broker.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def banking_model_catalog(catalog: dict, model: str) -> dict:
    """Keep the pinned model's limits, replacing only its native tool surface."""
    models = [deepcopy(m) for m in catalog["models"] if m["slug"] == model]
    if len(models) != 1:
        raise ValueError("Requested model is absent from the pinned Codex catalog")
    if models[0].get("use_responses_lite") is not True:
        raise ValueError("Model has no verified serial Responses transport")
    models[0].update(
        apply_patch_tool_type=None,
        experimental_supported_tools=[],
        tool_mode="direct",
        # Keep MCP results intact in Codex continuation history. The host
        # enforces the separate 2 MiB request/128 MiB transcript limits.
        truncation_policy={"mode": "tokens", "limit": 500000},
    )
    return {"models": models}


def dynamic_bank_tools(schemas: list[dict]) -> list[dict]:
    if len(schemas) != 2 or {s["name"] for s in schemas} != BANK_TOOLS:
        raise ValueError("Exactly bank_directory and bank_call are required")
    return [
        dict(
            type="function",
            name=s["name"],
            description=s["description"],
            inputSchema=s.get("inputSchema", s.get("input_schema")),
            deferLoading=False,
        )
        for s in schemas
    ]


def validate_model_request(request: dict, contract: dict) -> dict:
    """Validate the entire initial wire input, not a substring tool denylist."""
    allowed = {
        "model",
        "stream",
        "input",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "store",
        "include",
        "prompt_cache_key",
        "text",
        "client_metadata",
    }
    if set(request) - allowed or request.get("model") != contract["model"]:
        raise ValueError("Unexpected model request fields or model")
    if (
        request.get("store") is not False
        or request.get("reasoning", {}).get("effort") != contract["effort"]
    ):
        raise ValueError("Unexpected model request persistence or reasoning effort")
    if request.get("parallel_tool_calls") is not False:
        raise ValueError(
            "Parallel calls would hide result-before-argument dependencies"
        )
    if set(request.get("text", {})) - {"verbosity"}:
        raise ValueError("Unexpected model output instructions")
    dynamic_bank_tools(contract["schemas"])
    expected_tools = [
        dict(
            type="function",
            name=s["name"],
            description=s["description"],
            parameters=s.get("inputSchema", s.get("input_schema")),
            strict=False,
        )
        for s in contract["schemas"]
    ]
    expected = [
        dict(
            type="additional_tools",
            role="developer",
            tools=[
                dict(
                    type="namespace",
                    name="functions",
                    description="",
                    tools=expected_tools,
                )
            ],
        ),
        dict(
            type="message",
            role="developer",
            content=[dict(type="input_text", text=contract["startup"])],
        ),
        dict(
            type="message",
            role="user",
            content=[dict(type="input_text", text=contract["prompt"])],
        ),
    ]
    actual = deepcopy(request.get("input"))
    if not isinstance(actual, list):
        raise ValueError("Missing complete model input")
    for item in actual:
        item.pop("id", None)

    # Codex adds an empty properties map to object schemas. This changes no
    # accepted arguments; no other schema or description changes are allowed.
    def normalize(value):
        if isinstance(value, dict):
            value = {key: normalize(child) for key, child in value.items()}
            if value.get("type") == "object":
                value.setdefault("properties", {})
        elif isinstance(value, list):
            value = [normalize(child) for child in value]
        return value

    if normalize(actual) != normalize(expected):
        raise ValueError(
            "Model input contains unexpected instructions, history or tools"
        )
    return {"passed": True, "tools": sorted(BANK_TOOLS), "messages": 2}


def create_command(image: str, name: str) -> list[str]:
    """Create a fresh container with only empty volatile mounts."""
    return [
        "docker",
        "create",
        "--name",
        name,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "256",
        "--memory",
        "3g",
        "--cpus",
        "2",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/root/.codex:rw,noexec,nosuid,size=128m",
        "--tmpfs",
        "/workspace:rw,nosuid,size=128m",
        "--tmpfs",
        "/tmp:rw,nosuid,size=256m",
        image,
        "sleep",
        "infinity",
    ]


def validate_inspection(inspected: dict) -> None:
    """Fail closed on a host mount, privileged mode, or shared host namespace."""
    if any(m.get("Type") != "tmpfs" for m in inspected.get("Mounts", [])):
        raise ValueError("Model container must have no host or volume mounts")
    config = inspected["HostConfig"]
    if (
        config.get("Privileged")
        or config.get("NetworkMode") != "none"
        or config.get("ReadonlyRootfs") is not True
    ):
        raise ValueError("Model container requires network=none and a read-only root")
    if config.get("PidMode") == "host" or config.get("IpcMode") == "host":
        raise ValueError("Host PID/IPC namespace is forbidden")
    if "ALL" not in config.get("CapDrop", []):
        raise ValueError("All container capabilities must be dropped")
    if not any(
        s.startswith("no-new-privileges") for s in config.get("SecurityOpt", [])
    ):
        raise ValueError("no-new-privileges must be enabled")


def inspect_container(name: str) -> dict:
    """Return and validate Docker's effective isolation state."""
    result = subprocess.run(
        ["docker", "inspect", name], check=True, capture_output=True, text=True
    )
    inspected = json.loads(result.stdout)[0]
    validate_inspection(inspected)
    return inspected


def copy_bytes(name: str, destination: str, content: bytes) -> None:
    """Copy one explicit runtime file without a host mount or secret argv."""
    # docker cp targets the backing filesystem and is not a reliable write into
    # a running tmpfs. Write through a process in the container's mount namespace.
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            name,
            "node",
            "-e",
            "const fs=require('fs');fs.writeFileSync(process.argv[1],fs.readFileSync(0),{mode:0o600});",
            destination,
        ],
        input=content,
        check=True,
        capture_output=True,
    )


def prepare_container(image: str, name: str, policy: str) -> dict:
    """Start a credential-free runtime, copying only the bank startup."""
    subprocess.run(create_command(image, name), check=True, capture_output=True)
    try:
        inspected = inspect_container(name)
        subprocess.run(["docker", "start", name], check=True, capture_output=True)
        copy_bytes(name, "/workspace/AGENTS.md", policy.encode())
        inventory = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "node",
                "-e",
                """
const fs=require('fs');
function walk(p){if(!fs.existsSync(p))return [];return fs.readdirSync(p,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(p+'/'+e.name):[p+'/'+e.name]);}
process.stdout.write(JSON.stringify(Object.fromEntries(['/root/.codex','/root/.agents','/root/.cache','/workspace'].map(p=>[p,walk(p)]))));
""",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        fresh = json.loads(inventory.stdout)
        if fresh != {
            "/root/.codex": [],
            "/root/.agents": [],
            "/root/.cache": [],
            "/workspace": ["/workspace/AGENTS.md"],
        }:
            raise ValueError("Codex home/workspace contains pre-existing state")
        inspected["banking_fresh_state"] = fresh
        return inspected
    except Exception:
        remove_container(name)
        raise


def remove_container(name: str) -> None:
    """Never report successful cleanup when the daemon could not remove it."""
    result = subprocess.run(
        ["docker", "rm", "-f", name], capture_output=True, text=True, timeout=15
    )
    if result.returncode:
        absent = subprocess.run(
            ["docker", "inspect", name], capture_output=True, text=True, timeout=15
        )
        if absent.returncode == 0 or not any(
            s in absent.stderr.lower() for s in ["no such object", "no such container"]
        ):
            raise RuntimeError(
                "Container cleanup is unverified; further attempts forbidden"
            )


def app_server_command(command):
    """Keep launch settings but broker MCP as explicit dynamic tools."""
    config, urls = {}, {}
    for i, arg in enumerate(command):
        if arg == "--config":
            key, value = command[i + 1].split("=", 1)
            if key.startswith("mcp_servers."):
                if key.endswith(".url"):
                    urls[key.split(".")[1]] = json.loads(value)
            else:
                config[key] = value
    if len(urls) != 1:
        raise ValueError("Exactly one directory MCP endpoint is required")
    # No inherited provider keys/headers/URLs are copied into the guest.
    config = {
        k: v
        for k, v in config.items()
        if k != "model_provider" and not k.startswith("model_providers.")
    }
    for key, value in {
        "model_provider": "banking_relay",
        "model_providers.banking_relay.name": "Host-only inference",
        "model_providers.banking_relay.base_url": "http://127.0.0.1:8765/v1",
        "model_providers.banking_relay.wire_api": "responses",
        "model_providers.banking_relay.requires_openai_auth": False,
        "model_providers.banking_relay.supports_websockets": False,
        "model_providers.banking_relay.request_max_retries": 0,
        "model_providers.banking_relay.stream_max_retries": 0,
    }.items():
        config[key] = json.dumps(value)
    config["model_catalog_json"] = '"/root/.codex/banking-models.json"'
    for key in [
        "include_permissions_instructions",
        "include_apps_instructions",
        "include_collaboration_mode_instructions",
        "include_environment_context",
        "include_environment_context_time",
        "skills.include_instructions",
        "agents.enabled",
        "features.goals",
        "features.sleep_tool",
        "features.code_mode_host",
        "features.code_mode_only",
        "features.tool_suggest",
        "tools.experimental_request_user_input.enabled",
        "tools.update_plan.enabled",
    ]:
        config[key] = "false"
    actual = ["codex"]
    for key, value in config.items():
        actual.extend(["--config", key + "=" + value])
    actual.append("app-server")
    return (
        actual,
        urls,
        dict(
            model=command[command.index("--model") + 1],
            effort=json.loads(config["model_reasoning_effort"]),
        ),
    )


async def invoke_app_server(
    name, command, prompt, policy, timeout_seconds, *, inference_forward=None
):
    """Route only two explicit dynamic tools to the fresh session's real MCP."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    actual, urls, model = app_server_command(command)
    if inference_forward is None and any(
        c.startswith("model_provider=") and c != 'model_provider="openai"'
        for c in command
    ):
        raise ValueError("Custom providers require an explicit trusted stub forwarder")
    broker = broker_module()
    bundled = await asyncio.to_thread(
        subprocess.run,
        ["docker", "exec", name, "codex", "debug", "models", "--bundled"],
        check=True,
        capture_output=True,
        text=True,
    )
    catalog = banking_model_catalog(json.loads(bundled.stdout), model["model"])
    await asyncio.to_thread(
        copy_bytes,
        name,
        "/root/.codex/banking-models.json",
        json.dumps(catalog).encode(),
    )
    lines, observations, raw_events = [], [], []
    usage = None
    relay = None
    started = time.monotonic()
    process = stderr_task = None
    raw_bytes = 0
    stderr_chunks = []
    messages_emitted = 0
    timed_out, exit_code = False, 0

    def emit(event):
        lines.append(json.dumps(event, ensure_ascii=False) + "\n")
        observations.append(
            dict(
                channel="stdout",
                line=len(lines),
                observed_at=datetime.now(UTC).isoformat(),
            )
        )

    async def send(value):
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()

    def emit_host_messages():
        nonlocal messages_emitted
        if relay is None:
            return
        for item in relay.guard.trusted_messages[messages_emitted:]:
            messages_emitted += 1
            emit(
                dict(
                    type="item.completed",
                    item=dict(
                        id=f"host-model-message-{messages_emitted}",
                        type="agent_message",
                        model_refusal=any(
                            c["type"] == "refusal" for c in item.get("content", [])
                        ),
                        text="".join(
                            c["text"] if c["type"] == "output_text" else c["refusal"]
                            for c in item.get("content", [])
                        ),
                    ),
                )
            )

    async def read_stderr():
        nonlocal raw_bytes
        while chunk := await process.stderr.read(16384):
            raw_bytes += len(chunk)
            if raw_bytes > broker.MAX_TRANSCRIPT:
                raise ValueError("Codex stderr/event budget exhausted")
            stderr_chunks.append(chunk)

    async def receive():
        nonlocal raw_bytes
        reading = asyncio.create_task(process.stdout.readline())
        try:
            while True:
                if stderr_task.done() and stderr_task.exception():
                    raise ValueError("Codex stderr/event budget exhausted")
                watching = [reading, relay.task]
                if not stderr_task.done():
                    watching.append(stderr_task)
                completed, _ = await asyncio.wait(
                    watching, return_when=asyncio.FIRST_COMPLETED
                )
                if reading in completed:
                    break
                if relay.task in completed:
                    raise RuntimeError("Host inference relay stopped")
            line = reading.result()
        finally:
            if not reading.done():
                reading.cancel()
                await asyncio.gather(reading, return_exceptions=True)
        if not line:
            raise RuntimeError("Codex app-server closed before turn completion")
        raw_bytes += len(line)
        if raw_bytes > broker.MAX_TRANSCRIPT:
            raise ValueError("Codex app-server event budget exhausted")
        event = json.loads(line)
        raw_events.append(event)
        emit_host_messages()
        return event

    async def reply(ident):
        while True:
            event = await receive()
            if event.get("id") == ident:
                if "error" in event:
                    raise RuntimeError(
                        "Codex app-server: " + json.dumps(event["error"])
                    )
                return event["result"]

    contract = dict(**model, startup=policy.strip(), prompt=prompt, schemas=[])
    thread_start = {}
    try:
        async with AsyncExitStack() as stack:
            streams = await stack.enter_async_context(
                streamable_http_client(next(iter(urls.values())))
            )
            mcp = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await mcp.initialize()
            schemas = [
                t.model_dump(mode="json") for t in (await mcp.list_tools()).tools
            ]
            tools = dynamic_bank_tools(schemas)
            contract = dict(
                **model, startup=policy.strip(), prompt=prompt, schemas=schemas
            )
            guard = broker.ConversationGuard(contract, validate_model_request)
            relay = await stack.enter_async_context(
                broker.InferenceRelay(
                    name,
                    guard,
                    inference_forward
                    if inference_forward is not None
                    else broker.SubscriptionInference(),
                    copy_bytes,
                )
            )
            thread_start = dict(
                model=model["model"],
                cwd="/workspace",
                approvalPolicy="never",
                sandbox="danger-full-access",
                baseInstructions=policy.strip(),
                developerInstructions="",
                ephemeral=True,
                dynamicTools=tools,
                allowProviderModelFallback=False,
            )
            try:
                async with asyncio.timeout(timeout_seconds):
                    process = await asyncio.create_subprocess_exec(
                        "docker",
                        "exec",
                        "-i",
                        name,
                        *actual,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        limit=16 * 1024 * 1024,
                    )
                    stderr_task = asyncio.create_task(read_stderr())
                    await send(
                        dict(
                            id=1,
                            method="initialize",
                            params=dict(
                                clientInfo=dict(name="banking-harness", version="1"),
                                capabilities=dict(experimentalApi=True),
                            ),
                        )
                    )
                    await reply(1)
                    await send(dict(method="initialized", params={}))
                    await send(dict(id=2, method="thread/start", params=thread_start))
                    opened = await reply(2)
                    if (
                        opened.get("model") != model["model"]
                        or opened.get("approvalPolicy") != "never"
                        or opened.get("sandbox", {}).get("type") != "dangerFullAccess"
                    ):
                        raise ValueError(
                            "Codex changed the requested model or access mode"
                        )
                    thread = opened["thread"]
                    if (
                        not thread.get("ephemeral")
                        or thread.get("forkedFromId")
                        or thread.get("turns")
                    ):
                        raise ValueError("Codex thread is not fresh and ephemeral")
                    tid = thread["id"]
                    emit(dict(type="thread.started", thread_id=tid))
                    await send(
                        dict(
                            id=3,
                            method="turn/start",
                            params=dict(
                                threadId=tid,
                                input=[dict(type="text", text=prompt)],
                                effort=model["effort"],
                            ),
                        )
                    )
                    turn_id = (await reply(3))["turn"]["id"]
                    while True:
                        event = await receive()
                        method, params = event.get("method"), event.get("params", {})
                        if "id" in event and method:
                            if (
                                method != "item/tool/call"
                                or params.get("tool") not in BANK_TOOLS
                                or params.get("namespace") not in (None, "functions")
                                or params.get("threadId") != tid
                                or params.get("turnId") != turn_id
                            ):
                                raise ValueError(
                                    "Unexpected Codex tool or approval request"
                                )
                            item = dict(
                                id=params["callId"],
                                type="mcp_tool_call",
                                server=next(iter(urls)),
                                tool=params["tool"],
                                arguments=params["arguments"],
                            )
                            await guard.wait_for_response()
                            emit_host_messages()
                            guard.authorize_tool(
                                params["callId"], params["tool"], params["arguments"]
                            )
                            emit(dict(type="item.started", item=item))
                            result = await mcp.call_tool(
                                params["tool"], params["arguments"]
                            )
                            content = [
                                dict(type="inputText", text=c.text)
                                for c in result.content
                                if c.type == "text"
                            ]
                            if len(content) != len(result.content):
                                raise ValueError(
                                    "Unexpected non-text banking tool result"
                                )
                            output = "\n".join(c["text"] for c in content)
                            emit(
                                dict(
                                    type="item.completed",
                                    item=dict(
                                        **item,
                                        result=dict(
                                            result.model_dump(mode="json"),
                                            isError=result.is_error,
                                        ),
                                        status="completed",
                                    ),
                                )
                            )
                            guard.tool_result(params["callId"], output)
                            await send(
                                dict(
                                    id=event["id"],
                                    result=dict(
                                        contentItems=[
                                            dict(type="inputText", text=output)
                                        ],
                                        success=not result.is_error,
                                    ),
                                )
                            )
                        elif method == "thread/tokenUsage/updated":
                            total = params["tokenUsage"]["total"]
                            usage = {
                                "input_tokens": total["inputTokens"],
                                "cached_input_tokens": total["cachedInputTokens"],
                                "output_tokens": total["outputTokens"],
                            }
                        elif method == "turn/completed":
                            turn = params["turn"]
                            exit_code = 0 if turn["status"] == "completed" else 1
                            if exit_code == 0:
                                await guard.wait_for_response()
                                guard.validate_final()
                            emit(
                                dict(
                                    type="turn.completed"
                                    if not exit_code
                                    else "turn.failed",
                                    error=turn.get("error"),
                                    usage=usage,
                                )
                            )
                            break
            except TimeoutError:
                timed_out, exit_code = True, 1
            except Exception as error:
                exit_code = 1
                emit(dict(type="error", message=str(error)))
            finally:
                if process is not None:
                    if process.returncode is None:
                        process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except TimeoutError:
                        process.kill()
                        await asyncio.wait_for(process.wait(), 5)
    except Exception as error:
        exit_code = 1
        emit(dict(type="error", message=str(error)))
    if stderr_task:
        try:
            await asyncio.wait_for(stderr_task, 5)
        except (TimeoutError, ValueError):
            exit_code = 1
    emit_host_messages()
    stderr = b"".join(stderr_chunks).decode(errors="replace")
    if relay and relay.errors:
        exit_code = 1
        emit(dict(type="error", message="Host inference validation failed"))
    return dict(
        stdout="".join(lines),
        stderr=stderr,
        output_observations=observations,
        timed_out=timed_out,
        exit_code=exit_code,
        elapsed_seconds=time.monotonic() - started,
        app_server_events=raw_events,
        request_contract=contract,
        broker_evidence=dict(
            events=relay.guard.events,
            errors=relay.errors,
            retries=relay.retries,
            upstream_requests=relay.upstream_requests,
            mode="stub" if inference_forward is not None else "subscription",
        )
        if relay
        else {},
        app_server_command=actual,
        thread_start=thread_start,
    )
