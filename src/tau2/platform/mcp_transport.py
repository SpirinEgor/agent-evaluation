"""Capture MCP tool requests and HTTP result emission in shared session order."""

import json

import mcp.types as t
from pydantic import ValidationError

from tau2.platform.session import PlatformSession, SessionStatus


class MCPTransportJournal:
    """ASGI wrapper: no client receipt ACK is inferred from server computation."""

    def __init__(self, app, session: PlatformSession, system: str | None):
        self.app, self.session, self.system = app, session, system

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        body = bytearray()
        attempt = None

        async def read():
            nonlocal attempt
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
                if not message.get("more_body"):
                    try:
                        request = json.loads(body)
                        if (
                            isinstance(request, dict)
                            and request.get("method") == "tools/call"
                        ):
                            params = t.CallToolRequestParams.model_validate(
                                request.get("params")
                            )
                            if self.session.status is SessionStatus.OPEN:
                                attempt = self.session.begin_call(
                                    params.name, params.arguments, self.system
                                )
                                attempt[1]["transport_call_id"] = request.get("id")
                                scope["tau2_platform_attempt"] = attempt
                    except (ValueError, TypeError, ValidationError):
                        pass  # Malformed transport requests remain MCP protocol errors.
            return message

        async def write(message):
            await send(message)
            if (
                attempt is not None
                and message["type"] == "http.response.body"
                and not message.get("more_body")
            ):
                self.session.result_sent(attempt)

        await self.app(scope, read, write)
