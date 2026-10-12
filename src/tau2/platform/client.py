"""Эталонный клиент участника. Используется тестами и как пример в docs.

Участнику достаточно любого MCP-клиента; этот класс лишь оборачивает три
HTTP-вызова платформы и подключение к MCP-эндпоинту сессии.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tau2.data_model.tasks import Action
from tau2.domains.banking_ru.mcp_systems import SINGLE_SERVER, TOOL_OWNERS, MCPLayout
from tau2.platform.mcp_bridge import DONE_TOOL_NAME


@dataclass
class OpenedSession:
    token: str
    task_id: str
    ticket: str
    policy: str
    mcp_url: Optional[str]
    max_calls: int
    max_errors: int
    mcp_urls: dict[str, str] = field(default_factory=dict)
    mcp_layout: MCPLayout = "single"

    def server_for(self, tool_name: str) -> str:
        """Resolve canonical tool ownership without stripping name prefixes."""
        if self.mcp_layout == "single":
            return SINGLE_SERVER
        return TOOL_OWNERS[tool_name]


class PlatformClient:
    def __init__(
        self, base_url: str, *, http_client: Optional[httpx.AsyncClient] = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http_client or httpx.AsyncClient(timeout=60)

    async def list_tasks(self) -> list[dict]:
        response = await self._http.get(f"{self.base_url}/tasks")
        response.raise_for_status()
        return response.json()

    async def open(
        self,
        task_id: str,
        participant: Optional[str] = None,
        *,
        mcp_layout: Optional[MCPLayout] = None,
    ) -> OpenedSession:
        body: dict = {"task_id": task_id}
        if participant is not None:
            body["participant"] = participant
        if mcp_layout is not None:
            body["mcp_layout"] = mcp_layout
        response = await self._http.post(f"{self.base_url}/sessions", json=body)
        response.raise_for_status()
        data = response.json()
        return OpenedSession(
            token=data["token"],
            task_id=data["task_id"],
            ticket=data["ticket"],
            policy=data["policy"],
            mcp_url=(
                f"{self.base_url}{data['mcp_path']}" if data.get("mcp_path") else None
            ),
            mcp_urls={
                name: f"{self.base_url}{path}"
                for name, path in data.get(
                    "mcp_paths", {SINGLE_SERVER: data["mcp_path"]}
                ).items()
            },
            mcp_layout=data.get("mcp_layout", "single"),
            max_calls=data["max_calls"],
            max_errors=data["max_errors"],
        )

    @asynccontextmanager
    async def connect(
        self, opened: OpenedSession, system: Optional[str] = None
    ) -> AsyncIterator[ClientSession]:
        url = opened.mcp_urls[system] if system is not None else opened.mcp_url
        if not url:
            raise ValueError("Choose a banking system or use connect_systems()")
        async with streamable_http_client(url, http_client=self._http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session

    @asynccontextmanager
    async def connect_systems(
        self, opened: OpenedSession
    ) -> AsyncIterator[dict[str, ClientSession]]:
        """Connect every advertised server, closing partial connections on error."""
        async with AsyncExitStack() as stack:
            servers = {}
            for name in opened.mcp_urls:
                servers[name] = await stack.enter_async_context(
                    self.connect(opened, name)
                )
            yield servers

    async def result(self, token: str) -> dict:
        response = await self._http.get(f"{self.base_url}/sessions/{token}/result")
        response.raise_for_status()
        return response.json()

    async def state(self, token: str) -> dict:
        """Return non-reward session diagnostics while a run is in progress."""
        response = await self._http.get(f"{self.base_url}/sessions/{token}")
        response.raise_for_status()
        return response.json()

    async def close(self, token: str) -> None:
        response = await self._http.delete(f"{self.base_url}/sessions/{token}")
        response.raise_for_status()

    async def replay_actions(
        self,
        task_id: str,
        actions: list[Action],
        participant: Optional[str] = None,
        *,
        mcp_layout: Optional[MCPLayout] = None,
    ) -> dict:
        """Прогнать действия tau2 как участник и вернуть результат сессии.

        Серверная сессия закрывается всегда, даже если один из вызовов
        внутри `connect` упадёт (плохое действие, ошибка транспорта): иначе
        она остаётся висеть в `live` до завершения приложения.
        """
        opened = (
            await self.open(task_id, participant, mcp_layout=mcp_layout)
            if mcp_layout is not None
            else await self.open(task_id, participant)
        )
        try:
            async with self.connect_systems(opened) as servers:
                for action in actions:
                    await servers[opened.server_for(action.name)].call_tool(
                        action.name, dict(action.arguments)
                    )
                await servers[opened.server_for(DONE_TOOL_NAME)].call_tool(
                    DONE_TOOL_NAME, {}
                )
            return await self.result(opened.token)
        finally:
            await self.close(opened.token)
