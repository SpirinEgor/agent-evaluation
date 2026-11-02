"""Banking Codex runs may never mount host paths into the model container."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/banking_ru/codex_container.py"


def request_contract():
    schemas = [
        {"name": name, "description": name, "inputSchema": {"type": "object"}}
        for name in ["bank_directory", "bank_call"]
    ]
    contract = {
        "model": "gpt-6.1-sol",
        "effort": "low",
        "startup": "BANK STARTUP",
        "prompt": "BANK TICKET",
        "schemas": schemas,
    }
    request = {
        "model": "gpt-6.1-sol",
        "reasoning": {"effort": "low"},
        "store": False,
        "parallel_tool_calls": False,
        "input": [
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "functions",
                        "description": "",
                        "tools": [
                            {
                                "type": "function",
                                "name": name,
                                "description": name,
                                "strict": False,
                                "parameters": {"type": "object"},
                            }
                            for name in ["bank_directory", "bank_call"]
                        ],
                    }
                ],
            },
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "BANK STARTUP"}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "BANK TICKET"}],
            },
        ],
    }
    return contract, request


def test_whole_request_rejects_context_tools_and_continuation_injection():
    contract, request = request_contract()
    module = load_module()
    module.validate_model_request(request, contract)
    for mutation in [
        lambda r: r["input"].append(
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "HOST INSTRUCTIONS"}],
            }
        ),
        lambda r: r["input"][0]["tools"][0]["tools"].append(
            {"type": "function", "name": "apply_patch"}
        ),
        lambda r: r.update(previous_response_id="old-thread"),
        lambda r: r.update(instructions="hidden extra instructions"),
        lambda r: r.update(tools=[{"type": "web_search"}]),
        lambda r: r.update(parallel_tool_calls=True),
        lambda r: r["input"][1]["content"].append(
            {"type": "input_text", "text": "EXTRA"}
        ),
        lambda r: r["input"][0]["tools"][0]["tools"][0].update(
            description="AUTHOR SOLUTION"
        ),
        lambda r: r["input"].append(
            {"type": "reasoning", "encrypted_content": "old-context"}
        ),
    ]:
        polluted = deepcopy(request)
        mutation(polluted)
        with pytest.raises(ValueError):
            module.validate_model_request(polluted, contract)


def test_catalog_removes_native_tools_without_changing_model_limits():
    catalog = {
        "models": [
            {
                "slug": "gpt-6.1-sol",
                "context_window": 272000,
                "use_responses_lite": True,
                "tool_mode": "code_mode_only",
                "apply_patch_tool_type": "freeform",
                "experimental_supported_tools": ["clock"],
            }
        ]
    }
    cleaned = load_module().banking_model_catalog(catalog, "gpt-6.1-sol")
    assert cleaned["models"][0]["context_window"] == 272000
    assert cleaned["models"][0]["tool_mode"] == "direct"
    assert cleaned["models"][0]["apply_patch_tool_type"] is None
    assert cleaned["models"][0]["experimental_supported_tools"] == []
    assert cleaned["models"][0]["truncation_policy"] == {
        "mode": "tokens",
        "limit": 500000,
    }
    assert catalog["models"][0]["apply_patch_tool_type"] == "freeform"


def load_module():
    spec = importlib.util.spec_from_file_location("codex_container", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_command_has_no_host_mount_or_host_namespace():
    command = load_module().create_command("sha256:image", "banking-one")
    assert command[:2] == ["docker", "create"]
    assert "--cap-drop" in command and command[command.index("--cap-drop") + 1] == "ALL"
    assert "--privileged" not in command
    assert not {"--volume", "-v", "--network=host", "--pid=host", "--ipc=host"} & set(
        command
    )
    assert not any("type=bind" in arg or "docker.sock" in arg for arg in command)
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command


def test_inspection_rejects_mounts_even_if_named_not_bind():
    module = load_module()
    base = {
        "Mounts": [],
        "HostConfig": {
            "Privileged": False,
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "PidMode": "",
            "IpcMode": "private",
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
        },
    }
    module.validate_inspection(base)
    with pytest.raises(ValueError):
        module.validate_inspection(
            {**base, "HostConfig": {**base["HostConfig"], "NetworkMode": "bridge"}}
        )

    for mount in [
        {"Type": "bind", "Source": "/Users"},
        {"Type": "volume", "Source": "wiki"},
    ]:
        with pytest.raises(ValueError):
            module.validate_inspection({**base, "Mounts": [mount]})
    with pytest.raises(ValueError):
        module.validate_inspection(
            {**base, "HostConfig": {**base["HostConfig"], "Privileged": True}}
        )


def test_file_delivery_reaches_running_tmpfs_when_image_is_available():
    import os
    import subprocess
    import uuid

    image = os.getenv("BANKING_CODEX_TEST_IMAGE")
    if not image:
        pytest.skip("Explicit local Docker test image required; no model calls")
    module = load_module()
    name = "banking-copy-test-" + uuid.uuid4().hex
    try:
        subprocess.run(
            module.create_command(image, name), check=True, capture_output=True
        )
        subprocess.run(["docker", "start", name], check=True, capture_output=True)
        module.copy_bytes(name, "/workspace/canary.txt", b"runtime-file-visible")
        result = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "node",
                "-e",
                "process.stdout.write(require('fs').readFileSync('/workspace/canary.txt'))",
            ],
            capture_output=True,
            check=True,
        )
        assert result.stdout == b"runtime-file-visible"
    finally:
        module.remove_container(name)


@pytest.mark.parametrize(
    "case", ["success", "upstream_failure", "tool_error", "large_result"]
)
def test_app_server_wire_and_mcp_roundtrip_with_local_stub(case):
    """Real Codex must consume MCP output, with no native tools or model call."""
    import asyncio
    import json
    import os
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    image = os.getenv("BANKING_CODEX_TEST_IMAGE")
    if not image:
        pytest.skip("Explicit Docker image required; local HTTP stub only")
    module = load_module()
    payloads, calls = [], []
    upstream_failure = case == "upstream_failure"
    fixture_result = "SYSTEM_FIXTURE_RECEIVED" + (
        "покупка_abcdef0123456789" * 3000 if case == "large_result" else ""
    )
    schemas = [
        dict(
            name="bank_directory",
            description="Directory fixture",
            inputSchema={
                "type": "object",
                "properties": {"operation": {"type": "string"}},
                "required": ["operation"],
            },
        ),
        dict(
            name="bank_call",
            description="Call fixture",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]

    class Stub(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/mcp":
                method = request.get("method")
                if "id" not in request:
                    self.send_response(202)
                    self.end_headers()
                    return
                if method == "initialize":
                    result = dict(
                        protocolVersion=request["params"]["protocolVersion"],
                        capabilities={"tools": {}},
                        serverInfo={"name": "fixture", "version": "1"},
                    )
                elif method == "tools/list":
                    result = {"tools": schemas}
                elif method == "tools/call":
                    calls.append(request["params"])
                    result = {
                        "content": [{"type": "text", "text": fixture_result}],
                        "isError": case == "tool_error" and len(calls) == 1,
                    }
                else:
                    result = {}
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": request["id"], "result": result}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            else:
                payloads.append(request)
                if upstream_failure and len(payloads) > 1:
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if len(payloads) == 1:
                    item = dict(
                        type="function_call",
                        id="fc_fixture",
                        call_id="call_fixture",
                        name="bank_directory",
                        metadata={"turn_id": "own-turn"},
                        internal_chat_message_metadata_passthrough={
                            "turn_id": "own-turn",
                            "create_time": 123.5,
                        },
                        arguments='{"operation":"list_systems"}',
                    )
                elif len(payloads) == 2:
                    item = dict(
                        type="function_call",
                        id="fc_second",
                        call_id="call_second",
                        name="bank_call",
                        namespace="functions",
                        arguments="{}",
                    )
                else:
                    item = dict(
                        type="message",
                        id="msg_fixture",
                        role="assistant",
                        status="completed",
                        content=[
                            dict(
                                type="output_text",
                                text="Fixture finished",
                                annotations=[],
                            )
                        ],
                    )
                response = dict(
                    id="resp_fixture_" + str(len(payloads)),
                    object="response",
                    status="completed",
                    output=[item],
                    usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
                )
                events = [
                    (
                        "response.created",
                        {"response": dict(response, status="in_progress", output=[])},
                    ),
                    ("response.output_item.added", {"output_index": 0, "item": item}),
                    ("response.output_item.done", {"output_index": 0, "item": item}),
                    ("response.completed", {"response": response}),
                ]
                if len(payloads) == 1:
                    message = dict(
                        type="message",
                        id="msg_before_call",
                        role="assistant",
                        status="completed",
                        content=[
                            dict(type="output_text", text="Before MCP", annotations=[])
                        ],
                    )
                    response["output"].insert(0, message)
                    events[1:1] = [
                        (
                            "response.output_item.added",
                            {"output_index": 0, "item": message},
                        ),
                        (
                            "response.output_item.done",
                            {"output_index": 0, "item": message},
                        ),
                    ]
                    for kind, value in events:
                        if (
                            kind
                            in (
                                "response.output_item.added",
                                "response.output_item.done",
                            )
                            and value["item"] is item
                        ):
                            value["output_index"] = 1
                body = "".join(
                    "event: "
                    + kind
                    + "\ndata: "
                    + json.dumps(dict(type=kind, **value))
                    + "\n\n"
                    for kind, value in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send_response(405)
            self.end_headers()

        def do_DELETE(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Stub)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    command = ["codex", "exec", "--model", "gpt-6.1-sol"]
    settings = {
        "model_reasoning_effort": "low",
        "model_provider": "fixture",
        "model_providers.fixture.name": "Local test",
        "model_providers.fixture.base_url": f"http://host.docker.internal:{server.server_port}/v1",
        "model_providers.fixture.wire_api": "responses",
        "model_providers.fixture.requires_openai_auth": False,
        "model_providers.fixture.request_max_retries": 0,
        "model_providers.fixture.stream_max_retries": 0,
        "mcp_servers.fixture.url": f"http://127.0.0.1:{server.server_port}/mcp",
        "project_doc_max_bytes": 0,
        "web_search": "disabled",
        **{
            "features." + f: False
            for f in [
                "shell_tool",
                "apps",
                "plugins",
                "browser_use",
                "computer_use",
                "multi_agent",
                "hooks",
                "memories",
                "view_image",
                "image_generation",
                "workspace_dependencies",
            ]
        },
    }
    for key, value in settings.items():
        command.extend(["--config", key + "=" + json.dumps(value)])

    async def forward(body):
        import httpx

        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            async with client.stream(
                "POST", f"http://127.0.0.1:{server.server_port}/v1/responses", json=body
            ) as response:
                if response.status_code != 200:
                    raise ValueError("Local upstream failure")
                async for chunk in response.aiter_bytes():
                    yield chunk
                await asyncio.sleep(0.1)  # RPC may arrive before the trusted EOF.

    name = "banking-runtime-test-" + uuid.uuid4().hex
    try:
        module.prepare_container(image, name, "BANK STARTUP")
        run = asyncio.run(
            module.invoke_app_server(
                name,
                command,
                "BANK TICKET",
                "BANK STARTUP",
                30,
                inference_forward=forward,
            )
        )
        assert run["exit_code"] == (1 if upstream_failure else 0), (
            run["stderr"],
            run["stdout"],
            run["broker_evidence"],
        )
        assert len(payloads) >= 2
        module.validate_model_request(payloads[0], run["request_contract"])
        expected_calls = [("bank_directory", {"operation": "list_systems"})]
        if not upstream_failure:
            expected_calls.append(("bank_call", {}))
            assert len(payloads) == 3
        assert [(c["name"], c["arguments"]) for c in calls] == expected_calls
        assert any(
            item.get("output") == fixture_result for item in payloads[1]["input"]
        )
        assert "mcp_tool_call" in run["stdout"]
        assert run["stdout"].index("Before MCP") < run["stdout"].index("mcp_tool_call")
        assert run["app_server_events"]
        terminal = [
            json.loads(line)
            for line in run["stdout"].splitlines()
            if json.loads(line)["type"] in ("turn.completed", "turn.failed")
        ]
        assert terminal[-1]["usage"]["input_tokens"] >= 20
        if not upstream_failure:
            assert "Fixture finished" in run["stdout"]
        if os.getenv("ISOLATION_DIAGNOSTICS"):
            Path(os.environ["ISOLATION_DIAGNOSTICS"] + "." + case + ".json").write_text(
                json.dumps(
                    {"requests": payloads, "run": run}, ensure_ascii=False, indent=2
                )
            )
    finally:
        module.remove_container(name)
        server.shutdown()
        server.server_close()
        worker.join(5)


def test_physical_network_credentials_and_apply_patch_boundary(tmp_path):
    import hashlib
    import json
    import os
    import subprocess
    import uuid

    image = os.getenv("BANKING_CODEX_TEST_IMAGE")
    if not image:
        pytest.skip("Explicit Docker image required; no model calls")
    module = load_module()
    name = "banking-boundary-test-" + uuid.uuid4().hex
    canary = tmp_path / "host-canary.txt"
    canary.write_text("HOST_UNCHANGED\n")
    digest = hashlib.sha256(canary.read_bytes()).hexdigest()
    try:
        inspected = module.prepare_container(image, name, "BANK STARTUP")
        probe_js = r"""
const fs=require('fs'),net=require('net');
function tcp(h,p){return new Promise(r=>{let s=net.connect({host:h,port:p});s.setTimeout(1000);s.on('connect',()=>{s.destroy();r(true)});s.on('error',()=>r(false));s.on('timeout',()=>{s.destroy();r(false)})})}
(async()=>{console.log(JSON.stringify({auth:fs.existsSync('/root/.codex/auth.json'),host:fs.existsSync(process.argv[1]),socket:fs.existsSync('/var/run/docker.sock'),routes:fs.readFileSync('/proc/net/route','utf8'),interfaces:fs.readdirSync('/sys/class/net').filter(n=>fs.existsSync('/sys/class/net/'+n+'/flags')&&(parseInt(fs.readFileSync('/sys/class/net/'+n+'/flags','utf8'),16)&1)),host_tcp:await tcp('192.168.65.254',80),lan_tcp:await tcp('192.168.1.1',80),public_tcp:await tcp('1.1.1.1',443),host_alias:await tcp('host.docker.internal',80),secret_env:Object.keys(process.env).filter(k=>/TOKEN|KEY|SECRET|CODEX|OPENAI|MCP/i.test(k))}))})();
"""
        result = subprocess.run(
            ["docker", "exec", name, "node", "-e", probe_js, str(canary)],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        probes = json.loads(result.stdout)
        assert probes["interfaces"] == ["lo"]
        assert len(probes["routes"].strip().splitlines()) == 1
        assert not any(
            probes[k]
            for k in [
                "auth",
                "host",
                "socket",
                "host_tcp",
                "lan_tcp",
                "public_tcp",
                "host_alias",
                "secret_env",
            ]
        )
        patch = f"*** Begin Patch\n*** Update File: {canary}\n@@\n-HOST_UNCHANGED\n+CHANGED\n*** End Patch"
        denied = subprocess.run(
            ["docker", "exec", name, "codex", "--codex-run-as-apply-patch", patch],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert denied.returncode != 0
        patch = "*** Begin Patch\n*** Add File: /workspace/canary.txt\n+CONTAINER_ONLY\n*** End Patch"
        subprocess.run(
            ["docker", "exec", name, "codex", "--codex-run-as-apply-patch", patch],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        assert hashlib.sha256(canary.read_bytes()).hexdigest() == digest
        local = subprocess.check_output(
            [
                "docker",
                "exec",
                name,
                "node",
                "-e",
                "process.stdout.write(require('fs').readFileSync('/workspace/canary.txt'))",
            ]
        )
        assert local == b"CONTAINER_ONLY\n"
    finally:
        module.remove_container(name)
    if os.getenv("ISOLATION_PHYSICAL_DIAGNOSTICS"):
        Path(os.environ["ISOLATION_PHYSICAL_DIAGNOSTICS"]).write_text(
            json.dumps(
                dict(
                    inspection=inspected,
                    network=probes,
                    host_canary_unchanged=True,
                    container_patch_executed=True,
                    cleanup_verified=True,
                    model_calls=0,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
