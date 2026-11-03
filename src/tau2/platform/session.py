"""Сессия платформы: изолированная среда, MCP-сервер, журнал и бюджеты.

Одна сессия — одна копия БД в памяти. Участник видит только инструменты;
эталон и проверки задачи в сессии есть, но наружу не отдаются.
"""

import hashlib
import inspect
import json
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Optional

import mcp.types as t
from mcp.server.lowlevel import Server

from tau2.config import platform_budgets
from tau2.data_model.message import AssistantMessage, Message, ToolCall, ToolMessage
from tau2.data_model.simulation import RewardInfo, SimulationRun, TerminationReason
from tau2.data_model.tasks import Action, Task
from tau2.domains.banking_ru.mcp_systems import (
    SINGLE_SERVER,
    SYSTEMS,
    MCPLayout,
    partition_tools,
)
from tau2.environment.environment import Environment
from tau2.environment.reference_bindings import reference_arguments
from tau2.platform.bank_directory import BankDirectory, DirectoryError, directory_tools
from tau2.platform.knowledge import TOOL_NAMES as KNOWLEDGE_TOOLS
from tau2.platform.knowledge import KnowledgeMCP, knowledge_tools
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
    ABORTED = "aborted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TOO_MANY_ERRORS = "too_many_errors"


class PlatformSession:
    """Среда одной задачи, выставленная участнику через MCP."""

    def __init__(
        self,
        task: Task,
        environment_constructor: Callable[..., Environment],
        *,
        max_calls: int | None = None,
        max_errors: int | None = None,
        max_discovery_calls: int | None = None,
        participant: Optional[str] = None,
        on_finish: Optional[Callable[["PlatformSession"], None]] = None,
        mcp_layout: MCPLayout = "single",
        token: str | None = None,
        now: Callable[[], datetime] | None = None,
        cursor_secret: bytes | None = None,
    ) -> None:
        self.token = token if token is not None else uuid.uuid4().hex
        self._now = now or (lambda: datetime.now(timezone.utc))
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
        self.max_calls, self.max_errors = platform_budgets(
            self.env.domain_name, max_calls, max_errors
        )
        if self.env.domain_name == "banking_ru" and task.max_bank_calls is not None:
            self.max_calls = min(self.max_calls, task.max_bank_calls)
        if max_discovery_calls is not None and (
            type(max_discovery_calls) is not int or max_discovery_calls < 1
        ):
            raise ValueError("max_discovery_calls must be positive or None")
        self.max_discovery_calls = max_discovery_calls
        self.discovery_calls = 0
        self.tool_call_errors: list[dict] = []
        self.environment_errors: list[dict] = []
        self.participant = participant
        self._on_finish = on_finish
        self.status = SessionStatus.OPEN
        self.log: list[tuple[ToolCall, ToolMessage]] = []
        self.errors = 0
        self._reward_info: Optional[RewardInfo] = None
        self.created_at = self._now()
        self.finished_at: Optional[datetime] = None
        self.mcp_layout = mcp_layout
        self._call_lock = RLock()
        self.mcp_events: list[dict] = []
        self._messages: list[Message] = []
        self._results: dict[str, ToolMessage] = {}
        self._canonical_trace_calls: dict[str, ToolCall] = {}
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
        self.directory = None
        self.knowledge = None
        if mcp_layout == "directory":
            if self.env.domain_name != "banking_ru":
                raise ValueError(
                    "Directory MCP layout is supported only for banking_ru"
                )
            from tau2.domains.banking_ru.discovery_workflow import SPECIAL_TOOLS
            from tau2.environment.tool import as_tool

            native = {tool.name: tool for tool in self.mcp_tools}
            for name in SPECIAL_TOOLS:
                if name not in native:
                    native[name] = to_mcp_tool(as_tool(getattr(self.env.tools, name)))
            for name in (
                "unlock_discoverable_agent_tool",
                "call_discoverable_agent_tool",
            ):
                native.pop(name, None)
            self.knowledge = KnowledgeMCP(
                self.env.tools.corpus, token=self.token, cursor_secret=cursor_secret
            )
            native.update({tool.name: tool for tool in knowledge_tools()})
            self.system_tools = partition_tools(list(native.values()))
            self.directory = BankDirectory(
                self.system_tools, token=self.token, cursor_secret=cursor_secret
            )
            self.mcp_tools = directory_tools()
        self.policy = self.env.get_policy()

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
            dispatch = (
                self._directory_call if self.directory is not None else self._call
            )
            result = dispatch(name, arguments, system=system, attempt=attempt)
            return result

    def begin_call(
        self, name: str, arguments: Optional[dict], system: Optional[str]
    ) -> tuple:
        """Record request formation independently of execution and HTTP output."""
        # A previously computed grade describes the old trace only.
        self._reward_info = None
        call = ToolCall(
            id=str(len(self.mcp_events)), name=name, arguments=deepcopy(arguments or {})
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
            "requested_at": self._now().isoformat(),
        }
        self._event_order += 1
        self.mcp_events.append(event)
        event["trace_call"] = (
            name != DONE_TOOL_NAME
            or not self._allowed(name, system)
            or bool(call.arguments)
        )
        trace_call = call
        if self.directory is not None:
            if name == "bank_directory":
                event["trace_call"] = False
            elif name == "bank_call":
                target = call.arguments.get("tool_name")
                values = call.arguments.get("arguments")
                if isinstance(target, str) and isinstance(values, dict):
                    trace_call = call.model_copy(
                        update={"name": target, "arguments": deepcopy(values)}
                    )
                    event["canonical_name"] = target
                    event["canonical_arguments"] = deepcopy(values)
                    event["system"] = call.arguments.get("system_id")
                    if target == DONE_TOOL_NAME and not values:
                        event["trace_call"] = False
        if (
            name == "call_discoverable_agent_tool"
            and self.env.domain_name == "banking_ru"
        ):
            from tau2.domains.banking_ru.discovery_workflow import SPECIAL_TOOLS

            nested_name = call.arguments.get("name")
            nested_args = call.arguments.get("arguments")
            if (
                set(call.arguments) == {"name", "arguments"}
                and isinstance(nested_name, str)
                and nested_name in SPECIAL_TOOLS
                and isinstance(nested_args, dict)
            ):
                trace_call = call.model_copy(
                    update={"name": nested_name, "arguments": nested_args}
                )
                event["canonical_name"] = nested_name
                event["canonical_arguments"] = nested_args
        if self.directory is not None:
            event["budget"] = "banking"
            if name == "bank_directory" or (
                name == "bank_call"
                and call.arguments.get("system_id") == "knowledge"
                and isinstance(call.arguments.get("tool_name"), str)
                and call.arguments.get("tool_name") in KNOWLEDGE_TOOLS
                and self.knowledge.is_discovery(
                    call.arguments["tool_name"],
                    call.arguments.get("arguments")
                    if isinstance(call.arguments.get("arguments"), dict)
                    else {},
                )
            ):
                event["budget"] = "discovery"
                event["trace_call"] = False
        self._canonical_trace_calls[call.id] = trace_call
        if event["trace_call"]:
            self._messages.append(
                AssistantMessage(
                    role="assistant", content=None, tool_calls=[trace_call]
                )
            )
        return call, event

    def _directory_call(self, name, arguments, *, system, attempt):
        if self.status is not SessionStatus.OPEN:
            return text_result(
                f"Сессия завершена ({self.status.value}).", is_error=True
            )
        direct = attempt is None
        attempt = attempt or self.begin_call(name, arguments, system)
        call, event = attempt
        try:
            if system is not None:
                raise DirectoryError(
                    "WRONG_SYSTEM", "Используйте общий интерфейс каталога"
                )
            if event.get("budget") == "discovery":
                self.discovery_calls += 1
                if (
                    self.max_discovery_calls is not None
                    and self.discovery_calls > self.max_discovery_calls
                ):
                    raise DirectoryError(
                        "DISCOVERY_BUDGET_EXHAUSTED",
                        "Лимит discovery исчерпан; уже открытые операции остаются доступны",
                    )
            if name == "bank_directory":
                payload = self.directory.handle(call.arguments)
                result = text_result(json.dumps(payload, ensure_ascii=False))
                event.update(
                    response=result.content[0].text,
                    error=False,
                    returned_at=self._now().isoformat(),
                )
            elif name == "bank_call":
                owner, target, values = self.directory.validate_call(call.arguments)
                # Computed schemas are not yet received schemas. Reopening an
                # already received schema never revokes the earlier receipt.
                if not any(
                    e.get("original_name") == "bank_directory"
                    and e.get("arguments", {}).get("operation") == "open_tool"
                    and e["arguments"].get("system_id") == owner
                    and e["arguments"].get("tool_name") == target
                    and "response_order" in e
                    and e["response_order"] < event["request_order"]
                    and not e.get("error")
                    for e in self.mcp_events
                ):
                    raise DirectoryError(
                        "TOOL_SCHEMA_NOT_OPENED", "Получите результат открытия схемы"
                    )
                canonical = self._canonical_trace_calls[call.id]
                if owner == "knowledge" and target in KNOWLEDGE_TOOLS:
                    payload = self.knowledge.call(target, values)
                    message = ToolMessage(
                        id=call.id,
                        role="tool",
                        requestor="assistant",
                        content=json.dumps(payload, ensure_ascii=False),
                    )
                    self._results[call.id] = message
                    event.update(
                        response=message.content,
                        error=False,
                        returned_at=self._now().isoformat(),
                    )
                    if event.get("budget") != "discovery":
                        canonical.execution_order = len(self.log)
                        event["execution_order"] = canonical.execution_order
                        self.log.append((canonical, message))
                        if len(self.log) >= self.max_calls:
                            self.finish(SessionStatus.BUDGET_EXHAUSTED)
                    result = to_call_result(message)
                else:
                    result = self._call(
                        target, values, system=owner, attempt=(canonical, event)
                    )
            else:
                raise DirectoryError(
                    "UNKNOWN_TOOL", "Доступны только bank_directory и bank_call"
                )
        except DirectoryError as error:
            origin = getattr(error, "origin", "request")
            diagnostics = (
                self.environment_errors
                if origin in {"corpus", "infrastructure"}
                else self.tool_call_errors
            )
            diagnostics.append(
                {
                    "call_id": call.id,
                    "surface": name,
                    "code": error.code,
                    "origin": origin,
                }
            )
            event["error_origin"] = origin
            message = ToolMessage(
                id=call.id,
                role="tool",
                requestor="assistant",
                error=True,
                execution_rejected=True,
                content=str(error),
            )
            self._results[call.id] = message
            event.update(
                response=message.content,
                error=True,
                execution_rejected=True,
                diagnostic_code=error.code,
                returned_at=self._now().isoformat(),
            )
            if event.get("budget") != "discovery":
                canonical = self._canonical_trace_calls[call.id]
                canonical.execution_order = len(self.log)
                event["execution_order"] = canonical.execution_order
                self.log.append((canonical, message))
                if len(self.log) >= self.max_calls:
                    self.finish(SessionStatus.BUDGET_EXHAUSTED)
            result = to_call_result(message)
        if direct:
            self.result_sent(attempt)
        return result

    def discovery_metrics(self):
        counts = {}
        for error in self.tool_call_errors:
            counts[error["code"]] = counts.get(error["code"], 0) + 1
        return {
            "calls": self.discovery_calls,
            "max_calls": self.max_discovery_calls,
            "tool_call_errors": counts,
            **(
                {
                    "environment_errors": {
                        code: sum(e["code"] == code for e in self.environment_errors)
                        for code in sorted({e["code"] for e in self.environment_errors})
                    }
                }
                if self.environment_errors
                else {}
            ),
        }

    def _allowed(self, name: str, system: Optional[str]) -> bool:
        return (
            name in {tool.name for tool in self.mcp_tools}
            if system is None
            else name in {tool.name for tool in self.system_tools.get(system, [])}
        )

    def result_sent(self, attempt: tuple) -> None:
        """Record completed HTTP emission, not merely a computed tool result."""
        call, event = attempt
        if "response_order" in event:
            return
        self._reward_info = None
        event.update(
            response_order=self._event_order,
            transmitted_at=self._now().isoformat(),
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
                    returned_at=self._now().isoformat(),
                )
                if direct:
                    self.result_sent(attempt)
                return text_result(event["response"], is_error=True)
            event.update(
                response=STOP_TOKEN,
                error=False,
                returned_at=self._now().isoformat(),
            )
            if direct:
                self.result_sent(attempt)
            self.finish(SessionStatus.DONE)
            return text_result(STOP_TOKEN)
        call.execution_order = len(self.log)
        if call.id in self._canonical_trace_calls:
            self._canonical_trace_calls[call.id].execution_order = call.execution_order
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
            if name == "call_discoverable_agent_tool":
                self.env.tools.__dict__["_discovery_dispatch_entered"] = False
            message = self.env.get_response(call)
            if (
                name == "call_discoverable_agent_tool"
                and message.error
                and not self.env.tools.__dict__.get(
                    "_discovery_dispatch_entered", False
                )
            ):
                message.execution_rejected = True
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
            returned_at=self._now().isoformat(),
        )
        if direct:
            self.result_sent(attempt)
        if message.error:
            self.errors += 1
        if self.max_errors > 0 and self.errors >= self.max_errors:
            self.finish(SessionStatus.TOO_MANY_ERRORS)
        elif len(self.log) >= self.max_calls:
            self.finish(SessionStatus.BUDGET_EXHAUSTED)
        result = to_call_result(message)
        result.content.append(
            t.TextContent(
                type="text",
                text=f"Ресурсный бюджет: использовано {len(self.log)}/{self.max_calls}, осталось {max(0, self.max_calls - len(self.log))}; ошибок {self.errors}"
                + (
                    f"/{self.max_errors}."
                    if self.max_errors > 0
                    else "; без отдельного лимита."
                ),
            )
        )
        return result

    def finish(self, status: SessionStatus) -> None:
        """Перевести сессию в терминальный статус ровно один раз."""
        if self.status is not SessionStatus.OPEN:
            return
        self._reward_info = None
        self.status = status
        self.finished_at = self._now()
        self._notify_finish()

    # ------------------------------------------------------------- MCP
    def build_server(self, system: Optional[str] = None) -> Server:
        """Низкоуровневый MCP-сервер, отдающий инструменты этой сессии."""
        if self.directory is not None and system is not None:
            raise ValueError("Directory mode exposes only the shared gateway")
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
        criteria = self.task.evaluation_criteria
        bindings = criteria.result_argument_bindings if criteria else []
        received = {}
        for action in actions or []:
            arguments = reference_arguments(
                action, action.arguments, bindings, received
            )
            if self.directory is not None:
                from tau2.domains.banking_ru.mcp_systems import TOOL_OWNERS

                owner = TOOL_OWNERS[action.name]
                self.call(
                    "bank_directory",
                    {
                        "operation": "open_tool",
                        "system_id": owner,
                        "tool_name": action.name,
                    },
                )
                result = self.call(
                    "bank_call",
                    {
                        "system_id": owner,
                        "tool_name": action.name,
                        "arguments": arguments,
                    },
                )
            else:
                result = self.call(action.name, arguments)
            if not result.is_error:
                received[action.action_id] = result.content[0].text

    def grade(self) -> RewardInfo:
        """Оценка всеми основаниями задачи по журналу.

        Результат кэшируется на сессии: полная оценка (пересборка среды,
        повтор траектории, все проверки) выполняется один раз для неизменного
        журнала. Новый запрос, полученный результат или завершение сбрасывает
        кэш; повторные grade()/to_simulation_run() используют готовую оценку.
        """
        from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation

        if self._reward_info is None:
            end = self.finished_at or self._now()
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
            SessionStatus.ABORTED: TerminationReason.USER_STOP,
            SessionStatus.DONE: TerminationReason.AGENT_STOP,
            SessionStatus.BUDGET_EXHAUSTED: TerminationReason.MAX_STEPS,
            SessionStatus.TOO_MANY_ERRORS: TerminationReason.TOO_MANY_ERRORS,
        }[self.status]

    def attempt_metrics(self) -> dict:
        from tau2.environment.toolkit import ToolType
        from tau2.platform.attempt_metrics import tool_metrics

        tools = self.env.tools
        names = tools.get_tools()
        result = tool_metrics(
            self.mcp_events,
            read_tools={
                n
                for n in names
                if tools.tool_type(n) == ToolType.READ
                and not tools.tool_mutates_state(n)
            }
            | set(KNOWLEDGE_TOOLS)
            | {"bank_directory"},
            mutating_tools={n for n in names if tools.tool_mutates_state(n)},
        )
        end = self.finished_at or self._now()
        result.update(
            version="banking_metrics_v1",
            protocol_completion=self.status is SessionStatus.DONE,
            resolution_seconds=(end - self.created_at).total_seconds(),
        )
        return result

    def to_simulation_run(self) -> SimulationRun:
        """Запись в формате results.json: её понимают tau2 view и evaluate_trajectories."""
        end = self.finished_at or self._now()
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
                "discovery": self.discovery_metrics(),
                "metrics": self.attempt_metrics(),
                "tool_call_errors": deepcopy(self.tool_call_errors),
                **(
                    {"environment_errors": deepcopy(self.environment_errors)}
                    if self.environment_errors
                    else {}
                ),
                **getattr(self.env, "run_metadata", {}),
            },
        )
