"""Bank40 deterministic partial consent and processing settlement boundaries."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.data_model.message import AssistantMessage, Tick, ToolMessage
from tau2.data_model.simulation import SimulationRun
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.orchestrator.modes import CommunicationMode
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def payload(result):
    return json.loads(result.content[0].text)


def test_bank40_partial_then_current_state_then_exact_consent():
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    s = PlatformSession(task, get_environment, max_errors=50)
    for action in task.evaluation_criteria.actions:
        if action.action_id == "bank40:consent":
            break
        s.call(action.name, action.arguments)
    initial = payload(
        s.call(
            "ask_client",
            {
                "customer_id": "solovyova_a_7734",
                "request": {
                    "type": "fee_refund_confirmation",
                    "card_id": "card_7735",
                    "refunds": [
                        {"transaction_id": "txn_088214", "amount": 199},
                        {"transaction_id": "txn_088219", "amount": 169},
                    ],
                    "total_amount": 368,
                },
            },
        )
    )
    assert initial["status"] == "partial_confirmed"
    assert initial["data"]["refunds"] == [
        {"transaction_id": "txn_088219", "amount": 169}
    ]
    assert initial["update_available"] is True
    state = payload(s.call("get_fee_refund_state", {"transaction_id": "txn_088219"}))
    assert state["remaining"] == 139 and state["posted_refunds"] == 110
    assert state == payload(
        s.call("get_fee_refund_state", {"transaction_id": "txn_088219"})
    )
    stale = s.call(
        "refund_fee",
        {
            "transaction_id": "txn_088219",
            "amount": 139,
            "request": {"type": "fee_correction"},
            "confirmation_id": initial["confirmation_id"],
        },
    )
    assert stale.is_error
    consent = payload(
        s.call(
            "ask_client",
            {
                "customer_id": "solovyova_a_7734",
                "request": {
                    "type": "fee_refund_confirmation",
                    "card_id": "card_7735",
                    "refunds": [{"transaction_id": "txn_088219", "amount": 139}],
                    "total_amount": 139,
                    "state_version": state["state_version"],
                },
            },
        )
    )
    assert consent["status"] == "confirmed"
    result = s.call(
        "refund_fee",
        {
            "transaction_id": "txn_088219",
            "amount": 139,
            "request": {"type": "fee_correction"},
            "confirmation_id": consent["confirmation_id"],
        },
    )
    assert not result.is_error
    assert s.env.tools.db.accounts["acc_7734"].balance == 3951
    assert s.env.tools.db.transactions["txn_088218"].status == "posted"
    assert not s.env.tools.db.transactions["txn_088214"].fee_refunded


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("reference", 1),
        ("subset_initial_consent", 1),
        ("repeated_state", 1),
        ("details_alternative", 1),
        ("technical_error_retry", 1),
        ("noop", 0),
        ("368", 0),
        ("169_after_update", 0),
        ("deferred199", 0),
        ("right_amount_wrong_operation", 0),
        ("stale_consent", 0),
        ("consent_after_attempt", 0),
        ("state_result_delayed", 0),
        ("consent_result_delayed", 0),
        ("duplicate", 0),
        ("without_done", 0),
    ],
)
def test_bank40_sequential_whole_grade(route, expected, mode):
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    s = PlatformSession(task, get_environment, max_calls=100, max_errors=100)
    initial = s.env.tools.db.model_dump()
    delayed = None
    for action in task.evaluation_criteria.actions:
        args = deepcopy(action.arguments)
        if route == "noop":
            break
        if route == "subset_initial_consent" and action.action_id == "bank40:consent":
            args["request"]["refunds"] = [
                {"transaction_id": "txn_088219", "amount": 169}
            ]
            args["request"]["total_amount"] = 169
        if route == "details_alternative" and action.name == "get_transactions":
            for ident in [
                "txn_088219",
                "txn_088218",
                "txn_088217",
                "txn_088215",
                "txn_088214",
            ]:
                s.call("get_transaction_details", {"transaction_id": ident})
            continue
        if action.action_id == "bank40:state":
            if route == "state_result_delayed":
                attempt = s.begin_call(action.name, args, None)
                s.call(action.name, args, attempt=attempt)
                delayed = attempt
                continue
            s.call(action.name, args)
            if route == "repeated_state":
                s.call(action.name, args)
            continue
        if action.action_id == "bank40:final-consent":
            if route == "consent_after_attempt":
                s.call(
                    "refund_fee",
                    {
                        "transaction_id": "txn_088219",
                        "amount": 139,
                        "request": {"type": "fee_correction"},
                        "confirmation_id": "fee-refund-confirmation-2",
                    },
                )
            if route == "consent_result_delayed":
                attempt = s.begin_call(action.name, args, None)
                s.call(action.name, args, attempt=attempt)
                delayed = attempt
                continue
        if action.name == "refund_fee":
            if route == "368":
                args["amount"] = 368
            if route == "169_after_update":
                args["amount"] = 169
            if route == "deferred199":
                args.update(transaction_id="txn_088214", amount=199)
            if route == "right_amount_wrong_operation":
                args["transaction_id"] = "txn_088215"
            if route == "stale_consent":
                args["confirmation_id"] = "fee-refund-confirmation-1"
            if route == "technical_error_retry":
                # Transport rejection before execution has no monetary effect.
                attempt = s.begin_call(action.name, args, None)
                s.result_sent(attempt)
        s.call(action.name, args)
        if action.name == "refund_fee" and route in {
            "368",
            "169_after_update",
            "deferred199",
            "right_amount_wrong_operation",
            "stale_consent",
        }:
            s.call(action.name, deepcopy(action.arguments))
        if action.name == "refund_fee" and route == "duplicate":
            s.call(action.name, args)
    if delayed:
        s.result_sent(delayed)
    if route != "without_done":
        s.call("done", {})
    if mode == "FULL" and route != "without_done":
        reward = grade_full_session(s).reward_info
    else:
        trace = {"messages": s.trajectory()}
        if mode == "FULL":
            trace = {
                "mode": CommunicationMode.FULL_DUPLEX.value,
                "ticks": [
                    Tick(
                        tick_id=i,
                        timestamp=s.created_at.isoformat(),
                        agent_chunk=m if isinstance(m, AssistantMessage) else None,
                        agent_tool_calls=(m.tool_calls or [])
                        if isinstance(m, AssistantMessage)
                        else [],
                        agent_tool_results=[m] if isinstance(m, ToolMessage) else [],
                    )
                    for i, m in enumerate(s.trajectory())
                ],
            }
        sim = SimulationRun(
            id=s.token,
            task_id=task.id,
            start_time=s.created_at.isoformat(),
            end_time=(s.finished_at or s.created_at).isoformat(),
            duration=0,
            termination_reason=s.termination_reason(),
            **trace,
            info={"platform": "mcp", "mcp_events": s.mcp_events},
        )
        reward = evaluate_simulation(
            simulation=sim,
            task=task,
            evaluation_type=EvaluationType.ALL,
            solo_mode=True,
            domain=s.env.domain_name,
            mode=CommunicationMode.FULL_DUPLEX
            if mode == "FULL"
            else CommunicationMode.HALF_DUPLEX,
            strict_replay=True,
            env_kwargs=s.env.evaluation_env_kwargs,
        )
    if dest := os.environ.get("BANK40_DECISIONS_EVIDENCE"):
        p = Path(dest)
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": reward.model_dump(mode="json"),
                    "errors": s.errors,
                    "trajectory": [m.model_dump(mode="json") for m in s.trajectory()],
                    "mcp_events": s.mcp_events,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info
    if expected:
        final = s.env.tools.db.model_dump()
        assert final["accounts"]["acc_7734"]["balance"] == 3951
        for key, old in initial["transactions"].items():
            actual = deepcopy(final["transactions"][key])
            if key == "txn_088219":
                actual["fee_refunded"] = old["fee_refunded"]
            if key == "txn_088218":
                actual["status"] = old["status"]
            assert actual == old, key
        new = [
            row
            for key, row in final["transactions"].items()
            if key not in initial["transactions"]
        ]
        assert len(new) == 1 and (
            new[0]["original_transaction_id"],
            new[0]["amount"],
        ) == ("txn_088219", 139)
        for key in initial:
            if key not in {"accounts", "transactions", "tool_calls_log"}:
                assert final[key] == initial[key], key


def test_fee_refund_state_requires_identification():
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    session = PlatformSession(task, get_environment)
    before = session.env.tools.db.model_dump()
    result = session.call("get_fee_refund_state", {"transaction_id": "txn_088219"})
    assert result.is_error
    assert "ERR_NOT_VERIFIED" in result.content[0].text
    after = session.env.tools.db.model_dump()
    for key in before:
        if key != "tool_calls_log":
            assert after[key] == before[key]


# Explicitly retain the historical scenario version; composite tests live separately.
pytestmark = pytest.mark.usefixtures("fees_rewards_parent_scenarios")
