"""Synthetic timing/channel contracts; no banking outcome is inferred here."""

import pytest

from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    Tick,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.evaluator.trace_evidence import (
    evidence_from_messages,
    evidence_from_ticks,
    outgoing_entries,
)


def call(id="same", *, requestor="assistant", name="read", **arguments):
    return ToolCall(id=id, name=name, arguments=arguments, requestor=requestor)


def result(content, *, id="same", requestor="assistant", error=False):
    return ToolMessage(
        id=id, role="tool", content=content, requestor=requestor, error=error
    )


def tick(index, **events):
    return Tick(tick_id=index, timestamp="2026-09-30T00:00:00", **events)


@pytest.mark.parametrize("mode", ["messages", "ticks"])
def test_reused_ids_pair_results_fifo_and_keep_unanswered_attempt(mode):
    # A map keyed only by ID would overwrite the first call or its result.
    if mode == "messages":
        trace = evidence_from_messages(
            [
                AssistantMessage(role="assistant", tool_calls=[call(), call()]),
                MultiToolMessage(
                    role="tool", tool_messages=[result("first"), result("second")]
                ),
                AssistantMessage(role="assistant", tool_calls=[call()]),
            ]
        )
        expected_positions = [(0, 1), (0, 1), (2, None)]
    else:
        trace = evidence_from_ticks(
            [
                tick(0, agent_tool_calls=[call(), call()]),
                tick(1, agent_tool_results=[result("first"), result("second")]),
                tick(2, agent_tool_calls=[call()]),
            ]
        )
        expected_positions = [(0, 3), (0, 3), (4, None)]
    assert [
        item.result.content if item.result else None for item in trace.observed
    ] == ["first", "second", None]
    assert [
        (item.call_position, item.result_position) for item in trace.observed
    ] == expected_positions
    assert [item.execution_index for item in trace.observed] == [0, 1, 2]


def test_message_pairing_separates_roles_with_same_id():
    trace = evidence_from_messages(
        [
            AssistantMessage(role="assistant", tool_calls=[call()]),
            UserMessage(role="user", tool_calls=[call()]),
            result("user response", requestor="user"),
            result("agent response"),
        ]
    )
    assert [
        (item.call.requestor, item.result.content, item.result_position)
        for item in trace.observed
    ] == [("assistant", "agent response", 3), ("user", "user response", 2)]


@pytest.mark.parametrize("mode", ["messages"])
def test_delayed_result_does_not_satisfy_an_earlier_dependent_request(mode):
    if mode == "messages":
        trace = evidence_from_messages(
            [
                AssistantMessage(role="assistant", tool_calls=[call("source")]),
                AssistantMessage(role="assistant", tool_calls=[call("target")]),
                result("late", id="source"),
            ]
        )
        expected = [(0, 2, 0), (1, None, 1)]
    else:
        trace = evidence_from_ticks(
            [
                tick(0, agent_tool_calls=[call("source")]),
                tick(1, agent_tool_calls=[call("target")]),
                tick(2, agent_tool_results=[result("late", id="source")]),
            ]
        )
        expected = [(0, 5, 0), (2, None, 1)]
    assert [
        (item.call_position, item.result_position, item.execution_index)
        for item in trace.observed
    ] == expected
    assert trace.observed[0].result_position > trace.observed[1].call_position


@pytest.mark.parametrize("mode", ["messages"])
def test_failed_outgoing_tool_arguments_still_expose_configured_values(mode):
    question = call(
        "ask",
        name="ask_client",
        question="secret",
        payload={"z": 1, "a": "текст"},
        omitted=None,
    )
    if mode == "messages":
        trace = evidence_from_messages(
            [
                AssistantMessage(role="assistant", tool_calls=[question]),
                result("failed", id="ask", error=True),
            ]
        )
    else:
        trace = evidence_from_ticks(
            [
                tick(
                    0,
                    agent_tool_calls=[question],
                    agent_tool_results=[result("failed", id="ask", error=True)],
                )
            ]
        )
    assert [
        (entry.text, entry.segments, entry.channel, entry.call_id)
        for entry in outgoing_entries(
            trace, {"ask_client": ["question", "payload", "missing", "omitted"]}
        )
    ] == [
        ("secret", [(6, 0)], "ask_client.question", "ask"),
        ('{"a": "текст", "z": 1}', [(22, 0)], "ask_client.payload", "ask"),
    ]
    assert trace.observed[0].result.error is True
