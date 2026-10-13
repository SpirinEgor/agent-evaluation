"""Bank40: a received fee refund inherits the original transaction's owner."""

import json
import os
from pathlib import Path

import pytest

from tau2.data_model.message import AssistantMessage, Tick, ToolMessage
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.evaluator.evaluator_action import _tool_access_failures
from tau2.evaluator.trace_evidence import evidence_from_messages, evidence_from_ticks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

CASES = [
    ("reference", 1),
    ("refund_details", 1),
    ("refund_details_repeat", 1),
    ("noop", 0),
    ("excess_199", 0),
    ("excess_then_correct", 0),
    ("foreign_read", 0),
    ("unknown_retry", 1),
    ("missing_identity", 0),
]


def make_session(route):
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    if route == "noop":
        return session
    for action in task.evaluation_criteria.actions:
        if route == "missing_identity" and action.name == "verify_identity":
            continue
        args = dict(action.arguments)
        if action.name == "refund_fee" and route.startswith("excess"):
            session.call(action.name, {**args, "amount": 199})
            if route == "excess_199":
                continue
        session.call(action.name, args)
    if route in {"refund_details", "refund_details_repeat"}:
        refund = next(r for c, r in session.log if c.name == "refund_fee")
        payload = json.loads(refund.content)
        assert float(payload["refunded"]) == 169
        assert payload["original_transaction_id"] == "txn_088219"
        session.call(
            "get_transaction_details",
            {"transaction_id": payload["refund_transaction_id"]},
        )
    if route in {"foreign_read", "unknown_retry"}:
        policy = task.evaluation_criteria.tool_access_policy
        foreign = next(
            ids[0]
            for owner, ids in policy.resources_by_customer["transactions"].items()
            if owner not in policy.allowed_customer_ids and ids
        )
        session.call(
            "get_transaction_details",
            {"transaction_id": foreign if route == "foreign_read" else "txn_invented"},
        )
    return session


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize("route,expected", CASES, ids=[x[0] for x in CASES])
def test_bank40_routes(route, expected, mode):
    session = make_session(route)
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if dest := os.environ.get("BANK40_EVIDENCE"):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": reward.model_dump(mode="json"),
                    "errors": session.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "late",
        "same_batch",
        "missing",
        "error",
        "wrong_original",
        "foreign_id",
        "substituted_id",
        "user_result",
        "wrong_result_id",
        "undeclared",
        "foreign_source",
        "unknown_source",
    ],
)
@pytest.mark.parametrize("mode", ["HALF", "FULL"])
def test_bank40_refund_receipt(mutation, mode):
    session = make_session("refund_details")
    messages = session.trajectory()
    if mode == "HALF":
        observed = evidence_from_messages(messages).observed
    else:
        observed = evidence_from_ticks(
            [
                Tick(
                    tick_id=i,
                    timestamp=session.created_at.isoformat(),
                    agent_chunk=m if isinstance(m, AssistantMessage) else None,
                    agent_tool_calls=(m.tool_calls or [])
                    if isinstance(m, AssistantMessage)
                    else [],
                    agent_tool_results=[m] if isinstance(m, ToolMessage) else [],
                )
                for i, m in enumerate(messages)
            ]
        ).observed
    index = next(i for i, c in enumerate(observed) if c.call.name == "refund_fee")
    source = observed[index]
    target = observed[-1]
    result = source.result.model_copy(deep=True)
    payload = json.loads(result.content)
    policy = session.task.evaluation_criteria.tool_access_policy.model_copy(deep=True)
    foreign = next(
        ids[0]
        for owner, ids in policy.resources_by_customer["transactions"].items()
        if owner not in policy.allowed_customer_ids and ids
    )
    if mutation == "wrong_original":
        payload["original_transaction_id"] = "txn_088214"
    if mutation == "foreign_id":
        payload["refund_transaction_id"] = foreign
        observed[-1] = target.model_copy(
            update={
                "call": target.call.model_copy(
                    update={"arguments": {"transaction_id": foreign}}
                )
            }
        )
    if mutation == "substituted_id":
        observed[-1] = target.model_copy(
            update={
                "call": target.call.model_copy(
                    update={"arguments": {"transaction_id": "txn_substituted"}}
                )
            }
        )
    if mutation in {"foreign_source", "unknown_source"}:
        original = foreign if mutation == "foreign_source" else "txn_unknown"
        payload["original_transaction_id"] = original
        source = source.model_copy(
            update={
                "call": source.call.model_copy(
                    update={
                        "arguments": {
                            **source.call.arguments,
                            "transaction_id": original,
                        }
                    }
                )
            }
        )
    result = result.model_copy(update={"content": json.dumps(payload)})
    if mutation == "error":
        result = result.model_copy(update={"error": True})
    if mutation == "user_result":
        result = result.model_copy(update={"requestor": "user"})
    if mutation == "wrong_result_id":
        result = result.model_copy(update={"id": "different_call"})
    if mutation == "undeclared":
        policy.result_resource_collections.pop("refund_fee")
    observed[index] = source.model_copy(
        update={
            "result": None if mutation == "missing" else result,
            "result_position": target.call_position + 1
            if mutation == "late"
            else target.call_position
            if mutation == "same_batch"
            else source.result_position,
        }
    )
    failures = _tool_access_failures(
        observed, session.task.evaluation_criteria.actions, policy
    )
    assert bool(failures) is (mutation != "none"), failures
