"""Run a Banking.ru A2A exchange with a scripted agent and a synthetic session."""

import asyncio
import json
from uuid import uuid4

import httpx

from tau2.platform.a2a import Exchange, build_app
from tau2.platform.bank_directory import BankDirectory, directory_tools
from tau2.platform.mcp_bridge import STOP_TOKEN, done_tool


async def agent(exchange: Exchange) -> None:
    """The agent receives instructions, the ticket, and only the advertised tools."""
    print("Инструкция:", exchange.setup["instructions"])
    print("Тикет:", exchange.setup["opening"])
    available = await exchange.list_tools()
    print("Инструменты:", ", ".join(tool.name for tool in available.tools))

    opened = await exchange.call_tool(
        "bank_directory",
        {"operation": "open_tool", "system_id": "support", "tool_name": "done"},
    )
    operation = json.loads(opened.content[0].text)
    result = await exchange.call_tool(
        "bank_call",
        {
            "system_id": operation["system_id"],
            "tool_name": operation["tool_name"],
            "arguments": {},
        },
    )
    print("Квитанция у агента:", result.content[0].text)


async def send_turn(client, endpoint, parts, *, previous=None):
    message = {"role": "user", "messageId": uuid4().hex, "parts": parts}
    if previous is not None:
        message.update(taskId=previous["taskId"], contextId=previous["contextId"])

    async with client.stream(
        "POST",
        endpoint,
        json={
            "jsonrpc": "2.0",
            "id": uuid4().hex,
            "method": "message/stream",
            "params": {"message": message},
        },
    ) as response:
        response.raise_for_status()
        if "text/event-stream" not in response.headers.get("content-type", ""):
            raise RuntimeError((await response.aread()).decode())
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                event = json.loads(line[6:])["result"]
                if event["final"]:
                    return event
    raise RuntimeError("A2A stream ended without a final status")


async def coordinator(client: httpx.AsyncClient) -> None:
    """Send the ticket, execute tool calls, and close the A2A run."""
    response = await client.get("/.well-known/agent-card.json")
    response.raise_for_status()
    card = response.json()
    print("A2A", card["protocolVersion"])

    setup = {
        "kind": "run_setup",
        "instructions": "Для этого демонстрационного тикета завершите сессию.",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in directory_tools()
        ],
        "rules": {
            "max_turns": 2,
            "run_timeout": "10s",
            "channels": ["tools"],
            "parallel_tool_calls": "reject",
        },
    }
    event = await send_turn(
        client,
        card["url"],
        [
            {"kind": "data", "data": setup},
            {"kind": "text", "text": "Проверьте связь и завершите обращение."},
        ],
    )

    directory = BankDirectory({"support": [done_tool()]}, token=event["taskId"])
    for expected_tool in ("bank_directory", "bank_call"):
        status = event["status"]
        if status["state"] != "input-required":
            raise RuntimeError(f"Expected a tool call, got {status['state']}")
        payload = status["message"]["parts"][0]["data"]
        if payload["kind"] != "tool_calls":
            raise RuntimeError("Expected a tool_calls DataPart")
        (call,) = payload["calls"]
        if call["name"] != expected_tool:
            raise RuntimeError(f"Unexpected tool: {call['name']}")
        print("Вызов у координатора:", call["name"], call["arguments"])

        if expected_tool == "bank_directory":
            content = json.dumps(
                directory.handle(call["arguments"]), ensure_ascii=False
            )
        else:
            directory.validate_call(call["arguments"])
            content = STOP_TOKEN
        parts = [
            {
                "kind": "data",
                "data": {
                    "kind": "tool_results",
                    "results": [{"call_id": call["id"], "content": content}],
                },
            }
        ]
        if expected_tool == "bank_call":
            parts.append(
                {"kind": "data", "data": {"kind": "run_end", "reason": "done"}}
            )
        event = await send_turn(client, card["url"], parts, previous=event)

    if event["status"]["state"] != "completed":
        raise RuntimeError(f"Unexpected final status: {event['status']}")
    print("Статус A2A:", event["status"]["state"])


async def main() -> None:
    base_url = "http://banking-demo.local"
    app = build_app(endpoint=f"{base_url}/a2a", engine=agent)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=base_url
        ) as client,
    ):
        async with asyncio.timeout(10):
            await coordinator(client)


if __name__ == "__main__":
    asyncio.run(main())
