"""Требования ACTION отделены от полной эталонной траектории."""

import json

import pytest

from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    Tick,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.data_model.tasks import Action, EvaluationCriteria, Task, UserScenario
from tau2.evaluator.evaluator_action import ActionEvaluator, FullDuplexActionEvaluator


def _task_with_actions(actions):
    # Exercise the generic full contract, independent of any production case's
    # scoring profile, corpus, or additional banking guards.
    return Task(
        id="action-contract",
        user_scenario=UserScenario(instructions="Synthetic evaluator contract"),
        evaluation_criteria=EvaluationCriteria(
            actions=actions,
            reward_basis=["ACTION"],
            action_reward_scope="full_contract",
        ),
    )


def _call(name, arguments):
    return AssistantMessage(
        role="assistant",
        content=None,
        tool_calls=[ToolCall(id="0", name=name, arguments=arguments)],
    )


def _result(content, *, error=False):
    return ToolMessage(id="0", role="tool", content=content, error=error)


def _credential_binding_task():
    task = _with_success_requirement(
        _task_with_actions(
            [
                Action(
                    action_id="client-secret",
                    name="ask_client",
                    arguments={"customer_id": "customer_1"},
                    compare_args=["customer_id"],
                ),
                Action(
                    action_id="verify",
                    name="verify_identity",
                    arguments={"customer_id": "customer_1"},
                    compare_args=["customer_id"],
                ),
            ]
        )
    )
    return task.model_copy(
        update={
            "evaluation_criteria": task.evaluation_criteria.model_copy(
                update={
                    "result_argument_bindings": [
                        {
                            "binding_id": "identity-secret",
                            "source_action_id": "client-secret",
                            "target_action_id": "verify",
                            "argument_name": "credential",
                            "result_pattern": r"Client: (?P<value>[^\n]+)",
                            "case_insensitive": True,
                        }
                    ],
                }
            )
        }
    )


@pytest.mark.parametrize(
    "mode,variant,want",
    [
        ("half", "received", 1.0),
        ("full", "received", 1.0),
        ("half", "parallel", 0.0),
        ("full", "parallel", 0.0),
        ("half", "text_only", 0.0),
        ("half", "missing_path", 0.0),
        ("half", "wrong_receipt", 0.0),
    ],
)
def test_structured_binding_uses_received_json_fields(mode, variant, want):
    task = _credential_binding_task()
    task.evaluation_criteria.result_argument_bindings = [
        {
            "binding_id": "structured-secret",
            "source_action_id": "client-secret",
            "target_action_id": "verify",
            "argument_name": "credential",
            "result_path": ["data", "credential"],
        },
        {
            "binding_id": "structured-receipt",
            "source_action_id": "client-secret",
            "target_action_id": "verify",
            "argument_name": "answer_id",
            "result_path": ["answer_id"],
        },
    ]
    source = ToolCall(
        id="source", name="ask_client", arguments={"customer_id": "customer_1"}
    )
    payload = {
        "data": {"credential": "granite"},
        "answer_id": "answer-1",
        "text": "Client: granite",
    }
    if variant == "text_only":
        payload["data"]["credential"] = "1970-01-01"
    elif variant == "missing_path":
        payload.pop("data")
    response = ToolMessage(id="source", role="tool", content=json.dumps(payload))
    target = ToolCall(
        id="target",
        name="verify_identity",
        arguments={
            "customer_id": "customer_1",
            "credential": "granite",
            "answer_id": "wrong" if variant == "wrong_receipt" else "answer-1",
        },
    )
    result = ToolMessage(id="target", role="tool", content="verified")
    batches = (
        [([source, target], [response, result])]
        if variant == "parallel"
        else [([source], [response]), ([target], [result])]
    )
    assert _evaluate_batches(task, batches, mode).reward == want


@pytest.mark.parametrize(
    "mode,supplied,credential,want",
    [("half", "granite", "granite", 1.0), ("half", "granite", "1970-01-01", 0.0)],
)
def test_credential_must_match_received_client_value(mode, supplied, credential, want):
    source = ToolCall(
        id="source", name="ask_client", arguments={"customer_id": "customer_1"}
    )
    response = ToolMessage(id="source", role="tool", content="Client: " + supplied)
    target = ToolCall(
        id="target",
        name="verify_identity",
        arguments={"customer_id": "customer_1", "credential": credential},
    )
    result = ToolMessage(id="target", role="tool", content="verified")
    r = _evaluate_batches(
        _credential_binding_task(), [([source], [response]), ([target], [result])], mode
    )
    assert r.reward == want


def _with_success_requirement(task):
    criteria = task.evaluation_criteria.model_copy(
        update={"require_successful_actions": True}
    )
    return task.model_copy(update={"evaluation_criteria": criteria})


def _evaluate_batches(task, batches, mode):
    if mode == "full":
        ticks = [
            Tick(
                tick_id=index,
                timestamp="2026-08-28T00:00:00",
                agent_tool_calls=calls,
                agent_tool_results=results,
            )
            for index, (calls, results) in enumerate(batches)
        ]
        return FullDuplexActionEvaluator.calculate_reward(task, ticks)
    history = []
    for calls, results in batches:
        history.append(AssistantMessage(role="assistant", tool_calls=calls))
        if mode == "half-multi":
            history.append(MultiToolMessage(role="tool", tool_messages=results))
        else:
            history.extend(results)
    return ActionEvaluator.calculate_reward(task, history)


@pytest.mark.parametrize("mode", ["half", "full"])
def test_state_order_uses_execution_order_instead_of_request_order(mode):
    actions = [
        Action(action_id="block", name="block_card", arguments={}),
        Action(action_id="case", name="create_case", arguments={}),
    ]
    task = _task_with_actions(actions)
    task.evaluation_criteria.action_order = [
        {"before_action_id": "block", "after_action_id": "case"}
    ]
    block = ToolCall(id="block", name="block_card", arguments={}, execution_order=1)
    case = ToolCall(id="case", name="create_case", arguments={}, execution_order=0)
    reward = _evaluate_batches(
        task,
        [
            ([block], [ToolMessage(id="block", role="tool", content="ok")]),
            ([case], [ToolMessage(id="case", role="tool", content="ok")]),
        ],
        mode,
    )
    assert reward.reward == 0.0


def _source_and_write():
    return (
        ToolCall(id="source", name="get_limits", arguments={"card_id": "card_1"}),
        ToolMessage(
            id="source", role="tool", content='{"card_id":"card_1","max":150000}'
        ),
        ToolCall(
            id="write",
            name="set_limit",
            arguments={"card_id": "card_1", "amount": 150000},
        ),
        ToolMessage(id="write", role="tool", content="accepted"),
    )


def _task_with_limit_evidence():
    task = _with_success_requirement(
        _task_with_actions(
            [
                Action(
                    action_id="write",
                    name="set_limit",
                    arguments={"card_id": "card_1", "amount": 150000},
                ),
            ]
        )
    )
    criteria = task.evaluation_criteria.model_copy(
        update={
            "result_evidence": [
                {
                    "evidence_id": "tariff",
                    "tool_names": ["get_limits"],
                    "record_id": "card_1",
                    "expected_fields": {"max": 150000},
                    "before_action_id": "write",
                }
            ],
        }
    )
    return task.model_copy(update={"evaluation_criteria": criteria})


@pytest.mark.parametrize("mode", ["half", "full"])
def test_source_result_from_same_batch_is_not_known_before_write(mode):
    read, read_result, write, write_result = _source_and_write()
    reward = _evaluate_batches(
        _task_with_limit_evidence(),
        [([read, write], [read_result, write_result])],
        mode,
    )
    assert reward.reward == 0.0
    assert reward.info["result_evidence"] == ["tariff"]


@pytest.mark.parametrize("mode", ["half", "full"])
def test_independent_reads_in_one_batch_before_write_are_valid(mode):
    read, read_result, write, write_result = _source_and_write()
    another_read = ToolCall(id="other", name="get_accounts", arguments={})
    another_result = ToolMessage(id="other", role="tool", content="[]")
    batches = [
        ([read, another_read], [read_result, another_result]),
        ([write], [write_result]),
    ]
    assert _evaluate_batches(_task_with_limit_evidence(), batches, mode).reward == 1.0


def _with_order(task, before="ask-secret", after="verify"):
    criteria = task.evaluation_criteria.model_copy(
        update={
            "action_order": [{"before_action_id": before, "after_action_id": after}]
        }
    )
    return task.model_copy(update={"evaluation_criteria": criteria})


def _identity_actions():
    return [
        Action(
            action_id="ask-secret",
            name="ask_client",
            arguments={"customer_id": "customer_1"},
            compare_args=["customer_id"],
        ),
        Action(
            action_id="verify",
            name="verify_identity",
            arguments={"customer_id": "customer_1"},
            compare_args=["customer_id"],
        ),
    ]


def test_forbidden_argument_pattern_catches_outgoing_text_even_on_tool_error():
    task = _task_with_actions([])
    criteria = EvaluationCriteria.model_validate(
        {
            "actions": [],
            "forbidden_argument_patterns": [
                {
                    "rule_id": "private_scoring",
                    "tool_name": "ask_client",
                    "argument_name": "question",
                    "pattern": r"(?<!\d)612(?!\d)|sc\s*[-–]\s*17",
                }
            ],
        }
    )
    task = task.model_copy(update={"evaluation_criteria": criteria})
    bad = [
        _call("ask_client", {"question": "Ваш балл 612 — согласны?"}),
        _result("ERR", error=True),
    ]
    assert ActionEvaluator.calculate_reward(task, bad).reward == 0.0
    safe = [_call("ask_client", {"question": "Вы получили уведомление?"})]
    assert ActionEvaluator.calculate_reward(task, safe).reward == 1.0
    user_call = [
        UserMessage(
            role="user",
            content=None,
            tool_calls=[
                ToolCall(
                    id="0",
                    name="ask_client",
                    arguments={"question": "Мой балл 612?"},
                    requestor="user",
                )
            ],
        )
    ]
    assert ActionEvaluator.calculate_reward(task, user_call).reward == 1.0


def test_required_action_is_not_witnessed_by_an_error_result():
    task = _with_success_requirement(
        _task_with_actions(
            [
                Action(
                    action_id="verify",
                    name="verify_identity",
                    arguments={"customer_id": "customer_1"},
                    compare_args=["customer_id"],
                )
            ]
        )
    )
    history = [
        _call("verify_identity", {"customer_id": "customer_1"}),
        _result("ERR_IDENTITY_MISMATCH", error=True),
    ]
    reward = ActionEvaluator.calculate_reward(task, history)
    assert reward.reward == 0.0


def test_late_identity_question_cannot_satisfy_action_order():
    task = _with_order(
        _with_success_requirement(_task_with_actions(_identity_actions()))
    )
    history = [
        _call("verify_identity", {"customer_id": "customer_1"}),
        _result("Личность подтверждена"),
        _call("ask_client", {"customer_id": "customer_1"}),
        _result("Кодовое слово"),
    ]
    reward = ActionEvaluator.calculate_reward(task, history)
    assert all(check.action_match for check in reward.action_checks)
    assert reward.reward == 0.0


def test_successful_identity_question_before_verification_satisfies_order():
    task = _with_order(
        _with_success_requirement(_task_with_actions(_identity_actions()))
    )
    history = [
        _call("ask_client", {"customer_id": "customer_1"}),
        _result("Кодовое слово"),
        _call("verify_identity", {"customer_id": "customer_1"}),
        _result("Личность подтверждена"),
    ]
    reward = ActionEvaluator.calculate_reward(task, history)
    assert reward.reward == 1.0


def _evaluate_in_mode(task, history, mode):
    if mode == "half":
        return ActionEvaluator.calculate_reward(task, history)
    ticks = [
        Tick(
            tick_id=index,
            timestamp="2026-08-28T00:00:00",
            agent_tool_calls=history[index * 2].tool_calls,
            agent_tool_results=[history[index * 2 + 1]],
        )
        for index in range(len(history) // 2)
    ]
    return FullDuplexActionEvaluator.calculate_reward(task, ticks)


@pytest.mark.parametrize("mode", ["half"])
def test_successful_retry_after_prerequisite_is_valid(mode):
    actions = [
        Action(
            action_id="verify",
            name="verify_identity",
            arguments={"customer_id": "customer_1"},
        ),
        Action(
            action_id="write",
            name="set_limit",
            arguments={"card_id": "card_1", "amount": 150000},
        ),
    ]
    task = _with_order(
        _with_success_requirement(_task_with_actions(actions)),
        before="verify",
        after="write",
    )
    history = [
        _call("verify_identity", {"customer_id": "customer_1"}),
        _result("verified"),
        _call("set_limit", {"card_id": "card_1", "amount": 150000}),
        _result("ERR_RETRY", error=True),
        _call("set_limit", {"card_id": "card_1", "amount": 150000}),
        _result("accepted"),
    ]
    assert _evaluate_in_mode(task, history, mode).reward == 1.0


def test_forbidden_action_fails_even_when_required_action_succeeded():
    task = _task_with_actions(
        [
            Action(
                action_id="unblock-temporary",
                name="unblock_card",
                arguments={"card_id": "card_7714"},
                compare_args=["card_id"],
            )
        ]
    )
    forbidden = Action(
        action_id="unblock-lost",
        name="unblock_card",
        arguments={"card_id": "card_7712"},
        compare_args=["card_id"],
    )
    criteria = task.evaluation_criteria.model_copy(
        update={"forbidden_actions": [forbidden]}
    )
    task = task.model_copy(update={"evaluation_criteria": criteria})
    history = [
        _call("unblock_card", {"card_id": "card_7714"}),
        _result("Активна"),
        _call("unblock_card", {"card_id": "card_7712"}),
        _result("ERR_NOT_ALLOWED", error=True),
    ]
    reward = ActionEvaluator.calculate_reward(task, history)
    assert reward.reward == 0.0


def test_forbidden_action_does_not_block_different_arguments():
    task = _task_with_actions([])
    forbidden = Action(
        action_id="unblock-lost",
        name="unblock_card",
        arguments={"card_id": "card_7712"},
        compare_args=["card_id"],
    )
    criteria = task.evaluation_criteria.model_copy(
        update={"forbidden_actions": [forbidden]}
    )
    task = task.model_copy(update={"evaluation_criteria": criteria})
    history = [_call("unblock_card", {"card_id": "card_7714"})]
    reward = ActionEvaluator.calculate_reward(task, history)
    assert reward.reward == 1.0
