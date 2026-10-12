"""Сервис сессий: FastAPI поверх PlatformSession с MCP-сервером на сессию.

Каждая сессия монтируется в /s/{token}; её session_manager.run() живёт в
отдельной задаче — при монтировании Starlette-приложения FastAPI не запускает
его lifespan, а cancel scope anyio должен закрываться в той же задаче, где
был открыт.
"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from loguru import logger
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from starlette.routing import Mount

from tau2.config import PLATFORM_DATA_DIR
from tau2.data_model.tasks import Task
from tau2.domains.banking_ru.mcp_systems import SINGLE_SERVER, MCPLayout
from tau2.platform.mcp_transport import MCPTransportJournal
from tau2.platform.session import PlatformSession, SessionStatus
from tau2.registry import registry

MCP_MOUNT_PREFIX = "/s"


class PlatformSettings(BaseModel):
    domain: str = "banking_ru"
    split: str = "base"
    max_calls: int = 60
    max_errors: int = 5
    reveal_checks: bool = False
    results_dir: Path = PLATFORM_DATA_DIR
    mcp_layout: Optional[MCPLayout] = None


class SessionCreate(BaseModel):
    task_id: str
    participant: Optional[str] = None
    mcp_layout: Optional[MCPLayout] = None


class TaskCard(BaseModel):
    task_id: str
    ticket: str


class SessionInfo(BaseModel):
    token: str
    task_id: str
    ticket: str
    policy: str
    mcp_path: Optional[str]
    mcp_paths: dict[str, str]
    mcp_layout: MCPLayout
    max_calls: int
    max_errors: int


class SessionState(BaseModel):
    token: str
    task_id: str
    status: SessionStatus
    calls: int
    errors: int


class SessionResult(BaseModel):
    token: str
    task_id: str
    status: SessionStatus
    reward: float | None
    grading_status: str | None = None
    reward_breakdown: Optional[dict[str, float]] = None
    env_assertions: Optional[list[dict]] = None


class _LiveSession:
    """Сессия вместе с её MCP-монтом и задачей, держащей session_manager."""

    def __init__(self, session: PlatformSession) -> None:
        self.session = session
        self.mounts: list[Mount] = []
        self.paths: dict[str, str] = {}
        self.stop = asyncio.Event()
        self.runner: Optional[asyncio.Task] = None


def _persist(results_dir: Path):
    def write(session: PlatformSession) -> None:
        # Оценка (пересборка среды + повтор траектории) и запись на диск не
        # должны ронять вызывающего: done-вызов участника и остановка сессии
        # обязаны завершиться, даже если сохранение результата не удалось.
        try:
            results_dir.mkdir(parents=True, exist_ok=True)
            path = results_dir / f"{session.token}.json"
            path.write_text(
                session.to_simulation_run().model_dump_json(indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Не удалось сохранить результат сессии {session.token}: {e}")

    return write


def build_app(settings: PlatformSettings) -> FastAPI:
    env_constructor = registry.get_env_constructor(settings.domain)
    load_tasks = registry.get_tasks_loader(settings.domain)
    tasks: dict[str, Task] = {
        task.id: task for task in load_tasks(task_split_name=settings.split)
    }
    live: dict[str, _LiveSession] = {}
    persist = _persist(settings.results_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        # при остановке сервиса закрываем все живые сессии в той же задаче;
        # одна упавшая сессия (при сохранении или остановке) не должна
        # помешать остановить остальные
        for token in list(live):
            item = live.pop(token)
            try:
                persist(item.session)
            except Exception as e:
                logger.error(
                    f"Ошибка при сохранении сессии {item.session.token} "
                    f"во время остановки сервиса: {e}"
                )
            finally:
                try:
                    await _stop(item)
                except Exception as e:
                    logger.error(
                        f"Ошибка при остановке сессии {item.session.token}: {e}"
                    )

    app = FastAPI(title=f"tau2 platform: {settings.domain}", lifespan=lifespan)

    async def _start(item: _LiveSession) -> None:
        started = asyncio.Event()
        systems = (
            list(item.session.system_tools)
            if item.session.mcp_layout == "systems"
            else [None]
        )
        servers = []
        for system in systems:
            server = item.session.build_server(system)
            mcp_app = server.streamable_http_app(
                streamable_http_path="/mcp",
                json_response=True,
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=False
                ),
            )
            prefix = f"{MCP_MOUNT_PREFIX}/{item.session.token}"
            if system is not None:
                prefix += f"/{system}"
            item.paths[system or SINGLE_SERVER] = f"{prefix}/mcp"
            item.mounts.append(
                Mount(prefix, app=MCPTransportJournal(mcp_app, item.session, system))
            )
            servers.append(server)

        async def serve() -> None:
            async with AsyncExitStack() as stack:
                for server in servers:
                    await stack.enter_async_context(server.session_manager.run())
                started.set()
                await item.stop.wait()

        item.runner = asyncio.create_task(serve())
        started_task = asyncio.create_task(started.wait())
        try:
            await asyncio.wait(
                {started_task, item.runner}, return_when=asyncio.FIRST_COMPLETED
            )
            if not started.is_set():
                # serve() завершилась (или упала), не успев дойти до
                # started.set() — без этого POST /sessions завис бы навсегда
                # на started.wait().
                exc = item.runner.exception() if item.runner.done() else None
                detail = (
                    f"Не удалось запустить сессию: {exc}"
                    if exc
                    else (
                        "Не удалось запустить сессию: серверная задача "
                        "завершилась раньше времени"
                    )
                )
                raise HTTPException(status_code=500, detail=detail)
        finally:
            started_task.cancel()
        app.router.routes.extend(item.mounts)

    async def _stop(item: _LiveSession) -> None:
        for mount in item.mounts:
            if mount in app.router.routes:
                app.router.routes.remove(mount)
        item.stop.set()
        if item.runner is not None:
            try:
                await item.runner
            except Exception as e:
                logger.error(
                    f"Ошибка в задаче MCP-сервера сессии "
                    f"{item.session.token} при остановке: {e}"
                )

    def _get(token: str) -> _LiveSession:
        item = live.get(token)
        if item is None:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        return item

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/tasks")
    async def list_tasks() -> list[TaskCard]:
        return [TaskCard(task_id=t.id, ticket=t.ticket or "") for t in tasks.values()]

    @app.post("/sessions", status_code=201)
    async def create_session(body: SessionCreate) -> SessionInfo:
        task = tasks.get(body.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        layout = (
            body.mcp_layout
            or settings.mcp_layout
            or ("systems" if settings.domain == "banking_ru" else "single")
        )
        if layout == "systems" and settings.domain != "banking_ru":
            raise HTTPException(status_code=422, detail="systems requires banking_ru")
        session = PlatformSession(
            task,
            env_constructor,
            max_calls=settings.max_calls,
            max_errors=settings.max_errors,
            participant=body.participant,
            on_finish=persist,
            mcp_layout=layout,
        )
        item = _LiveSession(session)
        try:
            await _start(item)
        except BaseException:
            await _stop(item)
            raise
        live[session.token] = item
        return SessionInfo(
            token=session.token,
            task_id=task.id,
            ticket=task.ticket or "",
            policy=session.policy,
            mcp_path=item.paths.get(SINGLE_SERVER),
            mcp_paths=item.paths,
            mcp_layout=layout,
            max_calls=settings.max_calls,
            max_errors=settings.max_errors,
        )

    @app.get("/sessions/{token}")
    async def session_state(token: str) -> SessionState:
        s = _get(token).session
        return SessionState(
            token=s.token,
            task_id=s.task.id,
            status=s.status,
            calls=len(s.log),
            errors=s.errors,
        )

    @app.get("/sessions/{token}/result", response_model_exclude_unset=True)
    async def session_result(token: str) -> SessionResult:
        s = _get(token).session
        if s.status is SessionStatus.OPEN or not s.transport_complete:
            # Пока сессия открыта, результат — это оракул награды: участник
            # мог бы дергать /result после каждого вызова и подбирать
            # действия по сигналу. Результат отдаём только для терминальной
            # сессии.
            raise HTTPException(
                status_code=409, detail="Сессия ещё открыта: результат недоступен"
            )
        reward = s.grade()
        checks = None
        if settings.reveal_checks and reward.env_assertions is not None:
            checks = [c.model_dump(mode="json") for c in reward.env_assertions]
        breakdown = None
        if reward.reward_breakdown is not None:
            breakdown = {str(k.value): v for k, v in reward.reward_breakdown.items()}
        return SessionResult(
            token=s.token,
            task_id=s.task.id,
            status=s.status,
            reward=reward.final_reward,
            **(
                {"grading_status": "needs_review"}
                if reward.grading_status == "needs_review"
                else {}
            ),
            reward_breakdown=breakdown,
            env_assertions=checks,
        )

    @app.delete("/sessions/{token}")
    async def close_session(token: str) -> dict[str, bool]:
        item = live.pop(token, None)
        if item is None:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        persist(item.session)
        await _stop(item)
        return {"closed": True}

    return app
