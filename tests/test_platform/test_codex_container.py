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
    import pytest

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


def test_inspection_rejects_mounts_even_if_named_not_bind():
    module = load_module()
    base = {
        "Mounts": [],
        "HostConfig": {
            "Privileged": False,
            "NetworkMode": "bridge",
            "PidMode": "",
            "IpcMode": "private",
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
        },
    }
    module.validate_inspection(base)
    import pytest

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

    import pytest

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


@pytest.mark.parametrize("upstream_failure", [False, True])
def test_app_server_wire_and_mcp_roundtrip_with_local_stub(upstream_failure):
    """Real Codex must consume MCP output, with no native tools or model call."""
    import asyncio
    import json
    import os
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import pytest

    image = os.getenv("BANKING_CODEX_TEST_IMAGE")
    if not image:
        pytest.skip("Explicit Docker image required; local HTTP stub only")
    module = load_module()
    payloads, calls = [], []
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
                        "content": [
                            {"type": "text", "text": "SYSTEM_FIXTURE_RECEIVED"}
                        ],
                        "isError": False,
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
                        namespace="functions",
                        arguments='{"operation":"list_systems"}',
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
    name = "banking-runtime-test-" + uuid.uuid4().hex
    try:
        module.prepare_container(image, name, "BANK STARTUP")
        run = asyncio.run(
            module.invoke_app_server(name, command, "BANK TICKET", "BANK STARTUP", 30)
        )
        assert run["exit_code"] == (1 if upstream_failure else 0), (
            run["stderr"],
            run["app_server_events"],
        )
        assert len(payloads) >= 2
        module.validate_model_request(payloads[0], run["request_contract"])
        assert [(c["name"], c["arguments"]) for c in calls] == [
            ("bank_directory", {"operation": "list_systems"})
        ]
        assert "SYSTEM_FIXTURE_RECEIVED" in json.dumps(payloads[1])
        assert "mcp_tool_call" in run["stdout"]
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
            Path(os.environ["ISOLATION_DIAGNOSTICS"]).write_text(
                json.dumps(
                    {"requests": payloads, "run": run}, ensure_ascii=False, indent=2
                )
            )
    finally:
        module.remove_container(name)
        server.shutdown()
        server.server_close()
        worker.join(5)
