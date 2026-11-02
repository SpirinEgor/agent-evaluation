"""Host-only inference: a credential-free container may replay only its own turn."""

import asyncio
import base64
import json
import threading
from copy import deepcopy
from pathlib import Path

import httpx

UPSTREAM = "https://chatgpt.com/backend-api/codex/responses"
MAX_BODY = 2 * 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
MAX_REQUESTS = 512
MAX_TRANSCRIPT = 128 * 1024 * 1024


class BrokerFailure(ValueError):
    """Known failure metadata only; never retain upstream text or credentials."""

    def __init__(self, code, status=None, upstream_reason=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.upstream_reason = upstream_reason


# Only these known literals can be emitted from an untrusted provider event.
SAFE_UPSTREAM_REASONS = frozenset(
    {
        "context_length_exceeded",
        "rate_limit_exceeded",
        "insufficient_quota",
        "server_error",
        "overloaded",
        "invalid_request_error",
        "authentication_error",
        "model_not_found",
        "content_filter",
        "max_output_tokens",
        "timeout",
    }
)


def upstream_reason(event):
    response = event.get("response")
    response = response if isinstance(response, dict) else {}
    nested = event.get("error")
    nested = nested if isinstance(nested, dict) else {}
    response_error = response.get("error")
    response_error = response_error if isinstance(response_error, dict) else {}
    incomplete = response.get("incomplete_details")
    incomplete = incomplete if isinstance(incomplete, dict) else {}
    for value in (
        event.get("code"),
        nested.get("code"),
        response_error.get("code"),
        incomplete.get("reason"),
    ):
        if isinstance(value, str) and value in SAFE_UPSTREAM_REASONS:
            return value
    return "unknown"


def diagnostic(error, stage):
    known = {
        "Incomplete model response": "upstream_sse_failure",
        "Missing complete authoritative model response": "stream_missing_completion",
        "Expected SSE, not HTML/JSON": "invalid_sse_format",
        "Model response exceeded byte limit": "response_limit",
        "Invalid response completion": "invalid_completion",
        "Completed response differs from streamed output": "response_mismatch",
        "Continuation is not the exact host-owned trajectory": "history_mismatch",
        "Sensitive upstream output": "sensitive_output_blocked",
        "Unsupported model output type": "unsupported_output",
        "Parallel function calls are forbidden": "parallel_calls_blocked",
    }
    code = (
        error.code
        if isinstance(error, BrokerFailure)
        and error.code
        in {
            "upstream_http_status",
            "unexpected_media_type",
            "upstream_response_failed",
            "upstream_response_incomplete",
            "upstream_error_event",
        }
        else known.get(str(error), "unclassified")
    )
    # Unknown messages may contain secrets: only fixed allowlisted codes leave here.
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        code = "timeout"
    elif isinstance(error, httpx.TransportError):
        code = "transport_error"
    elif isinstance(error, json.JSONDecodeError) and stage == "response_stream":
        code = "invalid_sse_json"
    result = {"stage": stage, "code": code}
    if isinstance(
        error, BrokerFailure
    ) and error.upstream_reason in SAFE_UPSTREAM_REASONS | {"unknown"}:
        result["upstream_reason"] = error.upstream_reason
    if (
        isinstance(error, BrokerFailure)
        and isinstance(error.status, int)
        and 100 <= error.status <= 599
    ):
        result["http_status"] = error.status
    return result


def canonical(items, *, model_output=False):
    result = deepcopy(items)
    for item in result:
        item.pop("id", None)
        # Codex does not persist response-item delivery status in prompt history.
        item.pop("status", None)
        if model_output:
            # Subscription responses carry delivery metadata that Codex does
            # not serialize into continuation history. Strip it only on the
            # trusted response side; guest-supplied extra fields still fail.
            item.pop("metadata", None)
            item.pop("internal_chat_message_metadata_passthrough", None)
        # Codex omits empty response annotations when serializing history.
        # Nonempty annotations remain significant and must match exactly.
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("annotations") == []:
                    content.pop("annotations")
    return result


def same_json(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            same_json(left[k], right[k]) for k in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            same_json(a, b) for a, b in zip(left, right)
        )
    return left == right and (
        type(left) is type(right)
        or isinstance(left, (int, float))
        and isinstance(right, (int, float))
    )


class ConversationGuard:
    """The authoritative ledger is filled on the host, never from CLI history."""

    def __init__(self, contract, validate_initial, max_requests=MAX_REQUESTS):
        self.contract = contract
        self.validate_initial = validate_initial
        self.max_requests = max_requests
        self.requests = 0
        self.request_bytes = 0
        self.inflight = False
        self.terminal = False
        self.failed = False
        self.prefix = None
        self.envelope = None
        self.history = []
        self.current = []
        self.response_complete = False
        self.trusted_messages = []
        self.calls = {}
        self.events = []
        self.lock = threading.RLock()
        self.response_ready = asyncio.Event()

    def check(self, body):
        with self.lock:
            if self.inflight or self.terminal or self.requests >= self.max_requests:
                raise ValueError("Inference is in flight, finished or over budget")
            size = len(json.dumps(body, ensure_ascii=False).encode())
            if (
                body.get("stream") is not True
                or size > MAX_BODY
                or self.request_bytes + size > MAX_TRANSCRIPT
            ):
                raise ValueError("Bounded streaming inference required")
            inputs = body.get("input", [])
            initial = dict(body, input=inputs[:3])
            self.validate_initial(initial, self.contract)
            envelope = {
                k: v
                for k, v in body.items()
                if k not in {"input", "client_metadata", "prompt_cache_key"}
            }
            if self.prefix is None:
                if len(inputs) != 3:
                    raise ValueError("Inherited history in the initial request")
            else:
                if (
                    envelope != self.envelope
                    or canonical(inputs) != self.prefix + self.history
                ):
                    raise ValueError(
                        "Continuation is not the exact host-owned trajectory"
                    )
                if any(not c.get("result_recorded") for c in self.calls.values()):
                    raise ValueError("MCP result not yet received")
            self.prefix = canonical(inputs[:3])
            self.envelope = envelope
            self.inflight = True
            self.response_ready.clear()
            self.current = []
            self.response_complete = False
            self.requests += 1
            self.request_bytes += size
            self.events.append({"event": "request", "body": deepcopy(body)})
            return self.requests

    def observe_item(self, item):
        with self.lock:
            if not self.inflight or self.failed or self.response_complete:
                raise ValueError("Model item outside a response")
            row = canonical([item], model_output=True)[0]
            if row.get("type") not in {"message", "reasoning", "function_call"}:
                raise ValueError("Unsupported model output type")
            if row["type"] == "message" and row.get("role") != "assistant":
                raise ValueError("Model cannot introduce a developer/user message")
            if row["type"] == "message" and any(
                not (
                    c.get("type") in {"output_text", "refusal"}
                    and isinstance(
                        c.get("text" if c["type"] == "output_text" else "refusal"), str
                    )
                )
                for c in row.get("content", [])
            ):
                raise ValueError("Only authoritative assistant text is supported")
            if row["type"] == "function_call":
                if row.get("name") not in {"bank_directory", "bank_call"} or row.get(
                    "namespace"
                ) not in (None, "functions"):
                    raise ValueError("Non-banking model tool call")
                if any(x["type"] == "function_call" for x in self.current):
                    raise ValueError("Parallel function calls are forbidden")
                call_id = row.get("call_id")
                if not isinstance(call_id, str) or call_id in self.calls:
                    raise ValueError("Duplicate or invalid model call ID")
                arguments = json.loads(row["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError("Bank tool arguments must be an object")
                self.calls[call_id] = dict(
                    tool=row["name"],
                    arguments=arguments,
                    authorized=False,
                    result_recorded=False,
                )
            self.current.append(row)
            self.events.append({"event": "model_item", "item": deepcopy(item)})

    def complete(self, output):
        with self.lock:
            if (
                not self.inflight
                or self.response_complete
                or not isinstance(output, list)
            ):
                raise ValueError("Unexpected response completion")
            rows = canonical(output, model_output=True)
            if not self.current:
                for item in output:
                    self.observe_item(item)
            if rows != self.current:
                raise ValueError("Completed response differs from streamed output")
            self.history.extend(rows)
            for row in rows:
                if row["type"] == "function_call":
                    call = self.calls[row["call_id"]]
                    if call.get("result_recorded"):
                        self.history.append(
                            dict(
                                type="function_call_output",
                                call_id=row["call_id"],
                                output=call["output"],
                            )
                        )
            self.response_complete = True
            self.terminal = not any(x["type"] == "function_call" for x in rows)
            self.events.append(
                {"event": "response_completed", "output": deepcopy(output)}
            )

    def finish_response(self):
        """Do not authorize mutations until the complete SSE stream passes."""
        with self.lock:
            if not self.inflight or not self.response_complete or self.failed:
                raise ValueError("Missing valid complete model stream")
            self.inflight = False
            self.trusted_messages.extend(
                deepcopy(row) for row in self.current if row["type"] == "message"
            )
            self.events.append({"event": "response_trusted"})
            self.response_ready.set()

    def validate_final(self):
        with self.lock:
            if (
                self.inflight
                or self.failed
                or not self.terminal
                or any(not c.get("result_recorded") for c in self.calls.values())
            ):
                raise ValueError("Guest completion has no terminal host model response")

    async def wait_for_response(self):
        await self.response_ready.wait()

    def authorize_tool(self, call_id, tool, arguments):
        with self.lock:
            call = self.calls.get(call_id)
            if (
                self.inflight
                or self.failed
                or not call
                or call["tool"] != tool
                or not same_json(call["arguments"], arguments)
                or call["authorized"]
            ):
                raise ValueError("Tool RPC is not an unconsumed received model call")
            call["authorized"] = True
            self.events.append(
                dict(
                    event="tool_authorized",
                    call_id=call_id,
                    tool=tool,
                    arguments=deepcopy(arguments),
                )
            )

    def tool_result(self, call_id, output):
        with self.lock:
            call = self.calls.get(call_id)
            if (
                self.failed
                or not call
                or not call["authorized"]
                or call["result_recorded"]
                or not isinstance(output, str)
            ):
                raise ValueError("Unbound or duplicate MCP result")
            call.update(result_recorded=True, output=output)
            if not self.inflight:
                self.history.append(
                    dict(type="function_call_output", call_id=call_id, output=output)
                )
            self.events.append(
                dict(event="tool_result", call_id=call_id, output=output)
            )

    def fail(self):
        with self.lock:
            self.inflight, self.terminal = False, True
            self.failed = True
            self.events.append({"event": "upstream_failed"})
            self.response_ready.set()

    def close(self):
        with self.lock:
            self.inflight, self.terminal, self.failed = False, True, True
            self.events.append({"event": "broker_closed"})
            self.response_ready.set()


class SecretFilter:
    def __init__(self, values):
        self.values = [v for v in values if v]
        self.keep = max([len(v) for v in self.values] + [1]) - 1
        self.pending = b""

    def feed(self, chunk, final=False):
        data = self.pending + chunk
        if any(value in data for value in self.values):
            raise ValueError("Sensitive upstream output")
        split = len(data) if final else max(0, len(data) - self.keep)
        self.pending = data[split:]
        return data[:split]


class SubscriptionInference:
    """No upstream destination or auth headers are accepted from the container."""

    async def __call__(self, body):
        auth = json.loads((Path.home() / ".codex/auth.json").read_text())
        if auth.get("auth_mode") != "chatgpt":
            raise ValueError("Subscription authorization required; no API fallback")
        tokens = auth["tokens"]
        headers = {
            "Authorization": "Bearer " + tokens["access_token"],
            "ChatGPT-Account-Id": tokens["account_id"],
            "Accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "User-Agent": "codex_cli_rs/0.162.0-alpha.17.2",
        }
        filt = SecretFilter([v.encode() for v in tokens.values() if isinstance(v, str)])
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=120
        ) as client:
            async with client.stream(
                "POST", UPSTREAM, headers=headers, json=body
            ) as response:
                if response.status_code != 200:
                    raise BrokerFailure("upstream_http_status", response.status_code)
                media = (
                    response.headers.get("Content-Type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                if media and media != "text/event-stream":
                    raise BrokerFailure("unexpected_media_type")
                async for chunk in response.aiter_bytes():
                    clean = filt.feed(chunk)
                    if clean:
                        yield clean
                tail = filt.feed(b"", final=True)
                if tail:
                    yield tail


class ResponseStream:
    """Inspect authoritative SSE items before forwarding them into the container."""

    def __init__(self, guard):
        self.guard = guard
        self.buffer = b""
        self.data = []
        self.size = 0
        self.completed = False
        self.first_line = True

    def feed(self, chunk):
        self.size += len(chunk)
        if self.size > MAX_RESPONSE:
            raise ValueError("Model response exceeded byte limit")
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if self.first_line:
                if not line.lstrip().startswith((b"event:", b"data:", b":")):
                    raise ValueError("Expected SSE, not HTML/JSON")
                self.first_line = False
            if line.startswith(b"data:"):
                self.data.append(line[5:].strip())
            elif not line:
                self.dispatch()

    def dispatch(self):
        if not self.data:
            return
        raw = b"\n".join(self.data)
        self.data = []
        if raw == b"[DONE]":
            return
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "response.output_item.done":
            self.guard.observe_item(event["item"])
        elif kind == "response.completed":
            if self.completed or event["response"].get("status") != "completed":
                raise ValueError("Invalid response completion")
            self.guard.complete(event["response"].get("output"))
            self.completed = True
        elif kind in {"response.failed", "response.incomplete", "error"}:
            raise BrokerFailure(
                {
                    "response.failed": "upstream_response_failed",
                    "response.incomplete": "upstream_response_incomplete",
                    "error": "upstream_error_event",
                }[kind],
                upstream_reason=upstream_reason(event),
            )

    def finish(self):
        if self.buffer:
            self.feed(b"\n\n")
        self.dispatch()
        if not self.completed:
            raise ValueError("Missing complete authoritative model response")
        self.guard.finish_response()


# No host address, credential, upstream URL, bind mount or socket enters the container.
RELAY_JS = r"""
const http=require('http'),readline=require('readline');let next=0,active=0,pending=new Map();
const server=http.createServer((req,res)=>{
 if(req.method!=='POST'||req.url!=='/v1/responses'){res.writeHead(403);res.end('DENIED');return}
 if(active>=1){res.writeHead(429);res.end('BUSY');return}active++;
 let chunks=[],size=0,done=false;
 const finish=()=>{if(!done){done=true;active--}};res.on('close',finish);res.on('finish',finish);
 req.on('error',()=>{finish();res.destroy()});req.setTimeout(15000,()=>{finish();req.destroy()});
 req.on('data',b=>{size+=b.length;if(size>2097152){res.writeHead(413);res.end();req.destroy()}else chunks.push(b)});
 req.on('end',()=>{if(size>2097152)return;let body;try{body=JSON.parse(Buffer.concat(chunks))}catch{res.writeHead(400);res.end();return}
  const id=++next;pending.set(id,res);res.on('close',()=>pending.delete(id));
  process.stdout.write(JSON.stringify({id,method:req.method,path:req.url,body})+'\n');
 });
});
readline.createInterface({input:process.stdin}).on('line',l=>{try{const m=JSON.parse(l),res=pending.get(m.id);if(!res)return;
 if(m.status)res.writeHead(m.status,{'Content-Type':m.status===200?'text/event-stream':'application/json','Connection':'close'});
 if(m.chunk)res.write(Buffer.from(m.chunk,'base64'));if(m.end){res.end();pending.delete(m.id)}}catch{process.exit(2)}});
server.listen(8765,'127.0.0.1',()=>process.stdout.write(JSON.stringify({ready:true})+'\n'));
"""


class InferenceRelay:
    def __init__(self, name, guard, forward, copy_bytes):
        self.name, self.guard, self.forward, self.copy_bytes = (
            name,
            guard,
            forward,
            copy_bytes,
        )
        self.process = self.task = None
        self.errors = []
        self.closing = False

    async def __aenter__(self):
        await asyncio.to_thread(
            self.copy_bytes, self.name, "/tmp/banking-relay.js", RELAY_JS.encode()
        )
        self.process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            self.name,
            "node",
            "/tmp/banking-relay.js",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=MAX_BODY * 2 + 1024,
        )
        try:
            ready = await asyncio.wait_for(self.process.stdout.readline(), 10)
            if json.loads(ready) != {"ready": True}:
                raise ValueError("Inference relay not ready")
            self.task = asyncio.create_task(self.serve())
            return self
        except BaseException:
            await self.close()
            raise

    async def send(self, obj):
        self.process.stdin.write((json.dumps(obj) + "\n").encode())
        await self.process.stdin.drain()

    async def serve(self):
        received = 0
        try:
            while line := await self.process.stdout.readline():
                received += 1
                event, ident, started, sent = {}, None, False, False
                stage = "relay_envelope"
                try:
                    if received > MAX_REQUESTS + 16:
                        raise ValueError("Relay envelope budget exhausted")
                    event = json.loads(line)
                    if not isinstance(event, dict) or type(event.get("id")) is not int:
                        raise ValueError("Malformed relay envelope")
                    ident = event["id"]
                    if (
                        event.get("method") != "POST"
                        or event.get("path") != "/v1/responses"
                    ):
                        raise ValueError("Only fixed inference route is allowed")
                    stage = "request_validation"
                    self.guard.check(event["body"])
                    started = True
                    stream = ResponseStream(self.guard)
                    async with asyncio.timeout(120):
                        stage = "upstream_transport"
                        async for chunk in self.forward(event["body"]):
                            stage = "response_stream"
                            stream.feed(chunk)
                            if not sent:
                                stage = "relay_delivery"
                                await self.send(dict(id=ident, status=200))
                                sent = True
                            stage = "relay_delivery"
                            await self.send(
                                dict(id=ident, chunk=base64.b64encode(chunk).decode())
                            )
                            stage = "upstream_transport"
                        stage = "response_stream"
                        stream.finish()
                        stage = "relay_delivery"
                        await self.send(dict(id=ident, end=True))
                except Exception as error:
                    self.errors.append(diagnostic(error, stage))
                    self.guard.fail()
                    if ident is None:
                        raise ValueError("Invalid untrusted relay protocol") from None
                    # Never echo exception strings, auth or upstream headers.
                    await self.send(
                        dict(
                            id=ident,
                            status=None if sent else 403,
                            chunk=base64.b64encode(b"\n").decode(),
                            end=True,
                        )
                    )
                    if not started:
                        raise ValueError(
                            "Untrusted inference request rejected"
                        ) from None
        finally:
            # EOF or cancellation revokes every pending tool authorization.
            if not self.closing:
                self.guard.fail()

    async def close(self):
        self.closing = True
        self.guard.close()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        if self.process:
            if self.process.returncode is None:
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await asyncio.wait_for(self.process.wait(), 5)

    async def __aexit__(self, *args):
        await self.close()


def verify_evidence(evidence, contract, validate_initial):
    """Replay host provenance instead of trusting a positive receipt flag."""
    if evidence.get("mode") != "stub" or evidence.get("errors"):
        raise ValueError("Physical proof must use a successful local stub")
    guard = ConversationGuard(contract, validate_initial)
    finished = False
    results = 0
    events = evidence.get("events", [])
    for index, event in enumerate(events):
        kind = event.get("event")
        if kind == "request":
            guard.check(event["body"])
        elif kind == "model_item":
            guard.observe_item(event["item"])
        elif kind == "response_completed":
            guard.complete(event["output"])
        elif kind == "response_trusted":
            guard.finish_response()
            finished = guard.terminal
        elif kind == "tool_authorized":
            guard.authorize_tool(event["call_id"], event["tool"], event["arguments"])
        elif kind == "tool_result":
            guard.tool_result(event["call_id"], event["output"])
            results += 1
        elif kind == "broker_closed" and index == len(events) - 1:
            guard.close()
        else:
            raise ValueError("Unexpected or failed inference provenance event")
    if (
        not events
        or events[-1].get("event") != "broker_closed"
        or not finished
        or guard.requests < 3
        or results < 2
    ):
        raise ValueError("Missing complete multi-step model/MCP proof")
    return dict(
        passed=True,
        model_requests=guard.requests,
        tool_results=results,
        network="none",
        host_only_auth=True,
    )
