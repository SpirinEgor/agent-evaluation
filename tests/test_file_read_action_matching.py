"""Full-file source matching keeps received evidence and non-file guards intact."""

import pytest

from tau2.data_model.message import AssistantMessage, Tick, ToolCall, ToolMessage
from tau2.data_model.tasks import Action, EvaluationCriteria, Task, UserScenario
from tau2.evaluator.evaluator_action import ActionEvaluator, FullDuplexActionEvaluator


@pytest.mark.parametrize(
    "compare_args,arguments,want",
    [
        (None, {"path": "policies/cards.md"}, True),
        ([], {"path": "policies/cards.md"}, True),
        (["path"], {"path": "policies/other.md"}, False),
        (["path", "start_line"], {"path": "policies/cards.md", "start_line": 1}, True),
        (None, {}, False),
    ],
)
def test_file_read_matches_required_canonical_path(compare_args, arguments, want):
    action = Action(
        action_id="source",
        name="file_read",
        arguments={"path": "policies/cards.md"},
        compare_args=compare_args,
    )
    assert (
        action.compare_with_tool_call(
            ToolCall(id="read", name="file_read", arguments=arguments)
        )
        is want
    )


def test_file_read_keeps_declared_extra_constraints():
    action = Action(
        action_id="source",
        name="file_read",
        arguments={"path": "policies/cards.md"},
        argument_values={"revision": "current"},
    )
    assert not action.compare_with_tool_call(
        ToolCall(id="read", name="file_read", arguments={"path": "policies/cards.md"})
    )


def test_other_tools_keep_existing_argument_comparison():
    action = Action(action_id="write", name="set_limit", arguments={"amount": 500})
    assert not action.compare_with_tool_call(
        ToolCall(
            id="write", name="set_limit", arguments={"amount": 500, "channel": "cash"}
        )
    )
    assert action.model_copy(
        update={"compare_args": ["amount"]}
    ).compare_with_tool_call(
        ToolCall(
            id="write", name="set_limit", arguments={"amount": 500, "channel": "cash"}
        )
    )


@pytest.mark.parametrize(
    "mode,variant,want",
    [
        ("half", "received", 1.0),
        ("full", "received", 1.0),
        ("half", "optional_kwargs", 1.0),
        ("half", "wrong_revision", 0.0),
        ("half", "wrong_body", 0.0),
        ("half", "no_result", 0.0),
        ("half", "error", 0.0),
        ("half", "late_result", 0.0),
        ("full", "late_result", 0.0),
        ("half", "parallel", 0.0),
        ("full", "parallel", 0.0),
    ],
)
def test_file_read_source_requires_current_received_body_before_write(
    mode, variant, want
):
    task = Task(
        id="file-read-contract",
        user_scenario=UserScenario(instructions="Synthetic source dependency"),
        evaluation_criteria=EvaluationCriteria(
            reward_basis=["ACTION"],
            action_reward_scope="full_contract",
            require_successful_actions=True,
            actions=[
                Action(
                    action_id="source",
                    name="file_read",
                    arguments={"path": "policies/cards.md"},
                    result_contains=['"revision": "current"', "Applicable card rule"],
                ),
                Action(action_id="write", name="set_limit", arguments={"amount": 500}),
            ],
            action_order=[
                {
                    "before_action_id": "source",
                    "after_action_id": "write",
                    "require_result_before_target": True,
                }
            ],
        ),
    )
    args = {"path": "policies/cards.md"}
    if variant == "optional_kwargs":
        # Matcher compatibility does not authorize old range arguments in the API.
        args.update(start_line=1, end_line=200)
    elif variant == "different_path":
        args["path"] = "policies/other.md"
    elif variant == "missing_path":
        args = {}
    source = ToolCall(id="source", name="file_read", arguments=args)
    content = '{"revision": "current", "body": "Applicable card rule"}'
    if variant == "wrong_revision":
        content = '{"revision": "old", "body": "Applicable card rule"}'
    elif variant == "wrong_body":
        content = '{"revision": "current", "body": "Unrelated rule"}'
    source_result = ToolMessage(
        id="source", role="tool", content=content, error=variant == "error"
    )
    write = ToolCall(id="write", name="set_limit", arguments={"amount": 500})
    write_result = ToolMessage(id="write", role="tool", content="ok")
    if variant == "parallel":
        batches = [([source, write], [source_result, write_result])]
    elif variant == "late_result":
        batches = [([source], []), ([write], [write_result]), ([], [source_result])]
    else:
        batches = [
            ([source], [] if variant == "no_result" else [source_result]),
            ([write], [write_result]),
        ]
    if mode == "full":
        history = [
            Tick(
                tick_id=i,
                timestamp="2026-10-12T00:00:00",
                agent_tool_calls=calls,
                agent_tool_results=results,
            )
            for i, (calls, results) in enumerate(batches)
        ]
        reward = FullDuplexActionEvaluator.calculate_reward(task, history)
    else:
        history = []
        for calls, results in batches:
            history.append(AssistantMessage(role="assistant", tool_calls=calls))
            history.extend(results)
        reward = ActionEvaluator.calculate_reward(task, history)
    assert reward.reward == want
