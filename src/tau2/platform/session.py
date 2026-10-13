"""Сессия платформы: изолированная среда, MCP-сервер, журнал и бюджеты.

Одна сессия — одна копия БД в памяти. Участник видит только инструменты;
эталон и проверки задачи в сессии есть, но наружу не отдаются.
"""

import hashlib
import inspect
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Optional

import mcp.types as t
from mcp.server.lowlevel import Server

from tau2.data_model.message import AssistantMessage, Message, ToolCall, ToolMessage
from tau2.data_model.simulation import RewardInfo, SimulationRun, TerminationReason
from tau2.data_model.tasks import Action, Task
from tau2.domains.banking_ru.mcp_systems import (
    SINGLE_SERVER,
    SYSTEMS,
    MCPLayout,
    partition_tools,
    startup_policy,
)
from tau2.environment.environment import Environment
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.platform.mcp_bridge import (
    DONE_TOOL_NAME,
    STOP_TOKEN,
    done_tool,
    text_result,
    to_call_result,
    to_mcp_tool,
)


class SessionStatus(str, Enum):
    OPEN = "open"
    DONE = "done"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TOO_MANY_ERRORS = "too_many_errors"


class PlatformSession:
    """Среда одной задачи, выставленная участнику через MCP."""

    def __init__(
        self,
        task: Task,
        environment_constructor: Callable[..., Environment],
        *,
        max_calls: int = 60,
        max_errors: int = 5,
        participant: Optional[str] = None,
        on_finish: Optional[Callable[["PlatformSession"], None]] = None,
        mcp_layout: MCPLayout = "single",
    ) -> None:
        self.token = uuid.uuid4().hex
        self.task = task
        self._environment_constructor = environment_constructor
        env_kwargs = {"solo_mode": True}
        if "task_id" in inspect.signature(environment_constructor).parameters:
            env_kwargs["task_id"] = task.id
        self.env = environment_constructor(**env_kwargs)
        # Scenario data varies; the startup, tools and policies remain shared.
        # Apply the same declared initialization used by the original graders.
        initial = task.initial_state
        if initial is not None:
            self.env.set_state(
                initialization_data=initial.initialization_data,
                initialization_actions=initial.initialization_actions,
                message_history=list(initial.message_history or []),
                strict=True,
            )
        if hasattr(self.env, "run_metadata"):
            task_snapshot = self.task.model_dump(mode="json")
            self.env.run_metadata["task_snapshot_sha256"] = hashlib.sha256(
                json.dumps(task_snapshot, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            self.env.run_metadata["task_snapshot"] = task_snapshot
        self.max_calls = max_calls
        self.max_errors = max_errors
        self.participant = participant
        self._on_finish = on_finish
        self.status = SessionStatus.OPEN
        self.log: list[tuple[ToolCall, ToolMessage]] = []
        self.errors = 0
        self._reward_info: Optional[RewardInfo] = None
        self.created_at = datetime.now(timezone.utc)
        self.finished_at: Optional[datetime] = None
        self.mcp_layout = mcp_layout
        self._call_lock = RLock()
        self.mcp_events: list[dict] = []
        self._messages: list[Message] = []
        self._results: dict[str, ToolMessage] = {}
        self._event_order = 0
        self._finish_notified = False
        self.mcp_tools: list[t.Tool] = [
            to_mcp_tool(tool) for tool in self.env.get_tools()
        ] + [done_tool()]
        self.system_tools = (
            partition_tools(self.mcp_tools)
            if self.env.domain_name == "banking_ru"
            else {}
        )
        if mcp_layout == "systems" and not self.system_tools:
            raise ValueError("System MCP layout is supported only for banking_ru")
        self.policy = self.env.get_policy()
        if self.system_tools:
            # Give both comparison arms exactly the same visible startup.
            self.policy = startup_policy()
        if hasattr(self.env, "run_metadata") and self.system_tools:
            self.env.run_metadata["corpus_startup"] = self.env.run_metadata.get(
                "startup"
            )
            self.env.run_metadata["startup"] = {
                "path": "/AGENTS.md",
                "origin": "platform-system-map",
                "content": self.policy,
                "sha256": hashlib.sha256(self.policy.encode()).hexdigest(),
            }

    # ------------------------------------------------------------- вызовы
    def call(
        self,
        name: str,
        arguments: Optional[dict],
        *,
        system: Optional[str] = None,
        attempt: Optional[tuple] = None,
    ) -> t.CallToolResult:
        """Исполнить вызов участника на среде сессии и записать его в журнал."""
        with self._call_lock:
            result = self._call(name, arguments, system=system, attempt=attempt)
            return result

    def begin_call(
        self, name: str, arguments: Optional[dict], system: Optional[str]
    ) -> tuple:
        """Record request formation independently of execution and HTTP output."""
        # A previously computed grade describes the old trace only.
        self._reward_info = None
        call = ToolCall(
            id=str(len(self.mcp_events)), name=name, arguments=arguments or {}
        )
        event = {
            "sequence": len(self.mcp_events),
            "request_order": self._event_order,
            "call_id": call.id,
            "system": system or SINGLE_SERVER,
            "original_name": name,
            "canonical_name": name,
            "role": "assistant",
            "arguments": call.arguments,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        self._event_order += 1
        self.mcp_events.append(event)
        event["trace_call"] = (
            name != DONE_TOOL_NAME
            or not self._allowed(name, system)
            or bool(call.arguments)
        )
        if event["trace_call"]:
            self._messages.append(
                AssistantMessage(role="assistant", content=None, tool_calls=[call])
            )
        return call, event

    def _allowed(self, name: str, system: Optional[str]) -> bool:
        return system is None or name in {
            tool.name for tool in self.system_tools.get(system, [])
        }

    def result_sent(self, attempt: tuple) -> None:
        """Record completed HTTP emission, not merely a computed tool result."""
        call, event = attempt
        if "response_order" in event:
            return
        self._reward_info = None
        event.update(
            response_order=self._event_order,
            transmitted_at=datetime.now(timezone.utc).isoformat(),
        )
        self._event_order += 1
        if call.id not in self._results and "response" not in event:
            message = ToolMessage(
                id=call.id,
                role="tool",
                error=True,
                execution_rejected=True,
                content="Error: ERR_MCP_PROTOCOL: request rejected before domain execution",
            )
            self._results[call.id] = message
            event.update(
                response=message.content,
                error=True,
                transport_error=True,
                execution_rejected=True,
            )
        if call.id in self._results:
            # A protocol-rejected done was not admitted into the domain trace.
            if event["trace_call"]:
                self._messages.append(self._results[call.id])
        self._notify_finish()

    @property
    def transport_complete(self) -> bool:
        """Whether every admitted request has emitted its HTTP response."""
        return all("response_order" in event for event in self.mcp_events)

    def _notify_finish(self) -> None:
        if (
            self.status is SessionStatus.OPEN
            or self._finish_notified
            or not self.transport_complete
        ):
            return
        self._finish_notified = True
        if self._on_finish is not None:
            self._on_finish(self)

    def _call(
        self,
        name: str,
        arguments: Optional[dict],
        *,
        system: Optional[str],
        attempt: Optional[tuple],
    ) -> t.CallToolResult:
        if self.status is not SessionStatus.OPEN:
            return text_result(
                f"Сессия завершена ({self.status.value}): вызовы больше не принимаются.",
                is_error=True,
            )
        direct = attempt is None
        attempt = attempt or self.begin_call(name, arguments, system)
        call, event = attempt
        allowed = self._allowed(name, system)
        invalid_done = name == DONE_TOOL_NAME and allowed and bool(call.arguments)
        if name == DONE_TOOL_NAME and allowed and not invalid_done:
            # A close cannot precede receipt of a previously formed request.
            # Receiving the outstanding result then retrying done is valid.
            if any(
                previous is not event and "response_order" not in previous
                for previous in self.mcp_events
            ):
                event.update(
                    response="ERR_PENDING_RESULTS: receive outstanding tool results before done",
                    error=True,
                    returned_at=datetime.now(timezone.utc).isoformat(),
                )
                if direct:
                    self.result_sent(attempt)
                return text_result(event["response"], is_error=True)
            event.update(
                response=STOP_TOKEN,
                error=False,
                returned_at=datetime.now(timezone.utc).isoformat(),
            )
            if direct:
                self.result_sent(attempt)
            self.finish(SessionStatus.DONE)
            return text_result(STOP_TOKEN)
        call.execution_order = len(self.log)
        event["execution_order"] = call.execution_order
        if invalid_done:
            # The declared done schema has no arguments. Keep rejected outgoing
            # arguments in the real trace so secrecy checks see this attempt.
            message = ToolMessage(
                id=call.id,
                role="tool",
                requestor="assistant",
                error=True,
                execution_rejected=True,
                content="Error: ERR_INVALID_ARGUMENT: done accepts no arguments",
            )
        elif allowed:
            message = self.env.get_response(call)
        else:
            message = ToolMessage(
                id=call.id,
                role="tool",
                requestor="assistant",
                error=True,
                execution_rejected=True,
                content=f"Error: ERR_WRONG_SYSTEM: {system} does not provide {name}",
            )
        self.log.append((call, message))
        self._results[call.id] = message
        event.update(
            response=message.content,
            error=message.error,
            execution_rejected=message.execution_rejected,
            returned_at=datetime.now(timezone.utc).isoformat(),
        )
        if direct:
            self.result_sent(attempt)
        if message.error:
            self.errors += 1
        if self.errors >= self.max_errors:
            self.finish(SessionStatus.TOO_MANY_ERRORS)
        elif len(self.log) >= self.max_calls:
            self.finish(SessionStatus.BUDGET_EXHAUSTED)
        return to_call_result(message)

    def finish(self, status: SessionStatus) -> None:
        """Перевести сессию в терминальный статус ровно один раз."""
        if self.status is not SessionStatus.OPEN:
            return
        self._reward_info = None
        self.status = status
        self.finished_at = datetime.now(timezone.utc)
        self._notify_finish()

    # ------------------------------------------------------------- MCP
    def build_server(self, system: Optional[str] = None) -> Server:
        """Низкоуровневый MCP-сервер, отдающий инструменты этой сессии."""
        if system is not None and system not in self.system_tools:
            raise ValueError(f"Unknown banking system: {system}")
        purpose = (
            SYSTEMS[system]
            if system is not None
            else "Банковские системы: API операций и доступ к корпусу документов."
        )
        server = Server(
            f"{system or SINGLE_SERVER}:{self.task.id}:{self.token[:8]}",
            description=purpose,
            instructions=purpose,
        )

        async def list_tools(ctx, params: t.PaginatedRequestParams):
            return t.ListToolsResult(
                tools=self.mcp_tools if system is None else self.system_tools[system]
            )

        async def call_tool(ctx, params: t.CallToolRequestParams):
            attempt = (
                ctx.request.scope.get("tau2_platform_attempt") if ctx.request else None
            )
            return self.call(
                params.name, params.arguments, system=system, attempt=attempt
            )

        server.add_request_handler("tools/list", t.PaginatedRequestParams, list_tools)
        server.add_request_handler("tools/call", t.CallToolRequestParams, call_tool)
        return server

    # ------------------------------------------------------------- оценка
    def trajectory(self) -> list[Message]:
        """Журнал в форме траектории tau2: вызов, затем его результат."""
        return list(self._messages)

    def replay_actions(self, actions: Optional[list[Action]]) -> None:
        """Прогнать список действий tau2 через call — эталон как участник."""
        for action in actions or []:
            self.call(action.name, dict(action.arguments))

    def grade(self) -> RewardInfo:
        """Оценка всеми основаниями задачи по журналу.

        Результат кэшируется на сессии: полная оценка (пересборка среды,
        повтор траектории, все проверки) выполняется один раз для неизменного
        журнала. Новый запрос, полученный результат или завершение сбрасывает
        кэш; повторные grade()/to_simulation_run() используют готовую оценку.
        """
        if self._reward_info is None:
            end = self.finished_at or datetime.now(timezone.utc)
            simulation = SimulationRun(
                id=self.token,
                task_id=self.task.id,
                start_time=self.created_at.isoformat(),
                end_time=end.isoformat(),
                duration=(end - self.created_at).total_seconds(),
                termination_reason=self.termination_reason(),
                messages=self.trajectory(),
                info={"platform": "mcp", "mcp_events": self.mcp_events},
            )
            reward = evaluate_simulation(
                simulation=simulation,
                task=self.task,
                evaluation_type=EvaluationType.ALL,
                solo_mode=True,
                domain=self.env.domain_name,
                env_kwargs=getattr(self.env, "evaluation_env_kwargs", None),
            )
            if self.status is not SessionStatus.OPEN and self.transport_complete:
                self._reward_info = reward
            return reward
        return self._reward_info

    def termination_reason(self) -> TerminationReason:
        return {
            SessionStatus.OPEN: TerminationReason.USER_STOP,
            SessionStatus.DONE: TerminationReason.AGENT_STOP,
            SessionStatus.BUDGET_EXHAUSTED: TerminationReason.MAX_STEPS,
            SessionStatus.TOO_MANY_ERRORS: TerminationReason.TOO_MANY_ERRORS,
        }[self.status]

    def to_simulation_run(self) -> SimulationRun:
        """Запись в формате results.json: её понимают tau2 view и evaluate_trajectories."""
        end = self.finished_at or datetime.now(timezone.utc)
        return SimulationRun(
            id=self.token,
            task_id=self.task.id,
            timestamp=self.created_at.isoformat(),
            start_time=self.created_at.isoformat(),
            end_time=end.isoformat(),
            duration=(end - self.created_at).total_seconds(),
            termination_reason=self.termination_reason(),
            messages=self.trajectory(),
            reward_info=self.grade() if self.transport_complete else None,
            trial=0,
            info={
                "participant": self.participant,
                "platform": "mcp",
                "mcp_layout": self.mcp_layout,
                "mcp_events": self.mcp_events,
                "transport_complete": self.transport_complete,
                **getattr(self.env, "run_metadata", {}),
            },
        )
