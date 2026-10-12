"""Shared trace timing and outgoing utterances, without domain interpretation."""

import json
from collections import defaultdict, deque

from pydantic import BaseModel, ConfigDict, Field

from tau2.data_model.message import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    Tick,
    ToolCall,
    ToolMessage,
    UserMessage,
)


class ObservedCall(BaseModel):
    """One attempt, its received result, and two distinct order coordinates."""

    model_config = ConfigDict(frozen=True)
    call: ToolCall
    result: ToolMessage | None
    call_position: int
    result_position: int | None
    execution_index: int


class TraceEvidence(BaseModel):
    observed: list[ObservedCall] = Field(default_factory=list)
    messages: list[AssistantMessage] = Field(default_factory=list)
    positions: list[int] = Field(default_factory=list)


class UtteranceEntry(BaseModel):
    """Context plus cumulative character ends and their emission positions.

    Channels are ``native_text`` or ``<tool name>.<argument field>``. Tool
    entries retain the call ID, including attempts without successful results.
    """

    text: str
    segments: list[tuple[int, int]]
    channel: str
    call_id: str | None = None


def evidence_from_messages(messages: list[Message]) -> TraceEvidence:
    """Pair each call with its response, even when IDs are reused later."""
    trace = TraceEvidence()
    pending: dict[tuple[str, str], deque[int]] = defaultdict(deque)
    for position, message in enumerate(messages):
        if isinstance(message, AssistantMessage):
            trace.messages.append(message)
            trace.positions.append(position)
        if (
            isinstance(message, (AssistantMessage, UserMessage))
            and message.is_tool_call()
        ):
            for call in message.tool_calls or []:
                pending[(message.role, call.id)].append(len(trace.observed))
                trace.observed.append(
                    ObservedCall(
                        call=call.model_copy(update={"requestor": message.role}),
                        result=None,
                        call_position=position,
                        result_position=None,
                        execution_index=(
                            call.execution_order
                            if call.execution_order is not None
                            else len(trace.observed)
                        ),
                    )
                )
        elif isinstance(message, (ToolMessage, MultiToolMessage)):
            results = (
                message.tool_messages
                if isinstance(message, MultiToolMessage)
                else [message]
            )
            for result in results:
                queue = pending.get((result.requestor, result.id))
                if queue:
                    index = queue.popleft()
                    trace.observed[index] = trace.observed[index].model_copy(
                        update={"result": result, "result_position": position}
                    )
    return trace


def evidence_from_ticks(ticks: list[Tick]) -> TraceEvidence:
    """User tools execute first; same-tick results arrive after formation.

    Source arrays define requestor ownership. Formation/receipt positions use
    the existing two-phase tick clock, independently from execution indices.
    """
    trace = TraceEvidence()
    pending: dict[tuple[str, str], deque[int]] = defaultdict(deque)
    for position, tick in enumerate(ticks):
        trace.messages.append(
            (tick.agent_chunk or AssistantMessage(role="assistant")).model_copy(
                update={"tool_calls": tick.agent_tool_calls}
            )
        )
        trace.positions.append(position * 2)
        for requestor, calls, results in (
            ("user", tick.user_tool_calls, tick.user_tool_results),
            ("assistant", tick.agent_tool_calls, tick.agent_tool_results),
        ):
            for call in calls:
                pending[(requestor, call.id)].append(len(trace.observed))
                trace.observed.append(
                    ObservedCall(
                        call=call.model_copy(update={"requestor": requestor}),
                        result=None,
                        call_position=position * 2,
                        result_position=None,
                        execution_index=(
                            call.execution_order
                            if call.execution_order is not None
                            else len(trace.observed)
                        ),
                    )
                )
            for result in results:
                matching = pending.get((requestor, result.id))
                if matching:
                    index = matching.popleft()
                    trace.observed[index] = trace.observed[index].model_copy(
                        update={"result": result, "result_position": position * 2 + 1}
                    )
    return trace


def outgoing_entries(
    trace: TraceEvidence, tool_arguments: dict[str, list[str]]
) -> list[UtteranceEntry]:
    """Keep utterance context and each fragment's emission position.

    This context grouping is independent from confidentiality's aggregation
    across outgoing channels. Audio bytes need an actual text transcript.
    """
    entries = []
    pending = ""
    pending_ids = set()
    segments = []
    for message, position in zip(trace.messages, trace.positions, strict=True):
        if message.content and not message.is_audio:
            ids = set(message.utterance_ids or [])
            if pending and ids and pending_ids.intersection(ids):
                pending += message.content
                pending_ids.update(ids)
            else:
                if pending:
                    entries.append(
                        UtteranceEntry(
                            text=pending, segments=segments, channel="native_text"
                        )
                    )
                pending = message.content
                pending_ids = ids
                segments = []
            segments.append((len(pending), position))
        if message.tool_calls:
            if pending:
                entries.append(
                    UtteranceEntry(
                        text=pending, segments=segments, channel="native_text"
                    )
                )
            pending = ""
            pending_ids = set()
            segments = []
            for call in message.tool_calls:
                for field in tool_arguments.get(call.name, []):
                    value = call.arguments.get(field)
                    if value is not None:
                        text = (
                            value
                            if isinstance(value, str)
                            else json.dumps(value, ensure_ascii=False, sort_keys=True)
                        )
                        entries.append(
                            UtteranceEntry(
                                text=text,
                                segments=[(len(text), position)],
                                channel=f"{call.name}.{field}",
                                call_id=call.id,
                            )
                        )
    if pending:
        entries.append(
            UtteranceEntry(text=pending, segments=segments, channel="native_text")
        )
    return entries
