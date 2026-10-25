"""Full grader routes for investigation versus discretionary payout."""

from copy import deepcopy

import pytest

from tau2.data_model.simulation import SimulationRun
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.orchestrator.modes import CommunicationMode
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("reference", 1),
        ("old_rule_payout_attempt", 0),
        ("noop", 0),
        ("skip_authority", 0),
        ("payout_failed_attempt", 0),
        ("legacy_grant_attempt", 0),
        ("without_general_identity", 1),
        ("independent_read_order", 1),
        ("unfinished", 0),
    ],
)
def test_bank2_authority_routes(mode, route, expected):
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    s = PlatformSession(task, get_environment, max_calls=100, max_errors=100)
    actions = list(task.evaluation_criteria.actions)
    if route == "independent_read_order":
        read = [a for a in actions if a.name == "file_read"]
        actions = [a for a in actions if a.name != "file_read"]
        actions = list(reversed(read)) + actions
    if route != "noop":
        for a in actions:
            if (
                route == "skip_authority"
                and a.name == "get_cashback_adjustment_permissions"
            ):
                continue
            if (
                route == "without_general_identity"
                and a.name == "file_read"
                and a.arguments["path"]
                in ["/policies/general.md", "/policies/identity.md"]
            ):
                continue
            if a.name == "create_cashback_investigation":
                if route == "old_rule_payout_attempt":
                    s.call(
                        "file_read",
                        {
                            "path": "/knowledge/kb_cashback_adjustment_authority_previous.md"
                        },
                    )
                if route in {"payout_failed_attempt", "old_rule_payout_attempt"}:
                    s.call(
                        "execute_cashback_adjustment",
                        {
                            "permission_id": "adjustment-other-purchase",
                            "transaction_id": "txn_552290",
                            "amount": 300.0,
                        },
                    )
                if route == "legacy_grant_attempt":
                    s.call(
                        "grant_cashback",
                        {"transaction_id": "txn_552290", "amount": 300.0},
                    )
            s.call(a.name, deepcopy(a.arguments))
    if route != "unfinished":
        s.call("done", {})
    if mode == "full" and route != "unfinished":
        r = grade_full_session(s).reward_info
    else:
        sim = SimulationRun(
            id=s.token,
            task_id=task.id,
            start_time=s.created_at.isoformat(),
            end_time=s.created_at.isoformat(),
            duration=0,
            termination_reason=s.termination_reason(),
            messages=s.trajectory(),
            mode=CommunicationMode.HALF_DUPLEX.value,
        )
        r = evaluate_simulation(
            simulation=sim,
            task=task,
            evaluation_type=EvaluationType.ALL,
            solo_mode=True,
            domain=s.env.domain_name,
            mode=CommunicationMode.HALF_DUPLEX,
            strict_replay=True,
        )
    if mode == "full" and route == "unfinished":
        from tau2.data_model.message import AssistantMessage, Tick, ToolMessage

        ticks = [
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
        ]
        sim = SimulationRun(
            id=s.token,
            task_id=task.id,
            start_time=s.created_at.isoformat(),
            end_time=s.created_at.isoformat(),
            duration=0,
            termination_reason=s.termination_reason(),
            ticks=ticks,
            mode=CommunicationMode.FULL_DUPLEX.value,
        )
        r = evaluate_simulation(
            simulation=sim,
            task=task,
            evaluation_type=EvaluationType.ALL,
            solo_mode=True,
            domain=s.env.domain_name,
            mode=CommunicationMode.FULL_DUPLEX,
            strict_replay=True,
        )
    save_result(r, mode, route, expected, task, s)
    assert r.reward == expected, r.model_dump(mode="json")


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("permitted", 1),
        ("noop", 0),
        ("missing_permission_read", 0),
        ("wrong_program", 0),
        ("expired", 0),
        ("wrong_purchase", 0),
    ],
)
def test_paired_permitted_compensation(mode, route, expected):
    from tau2.data_model.tasks import (
        EnvAssertion,
        InitializationData,
        InitialState,
        ResultArgumentBinding,
    )

    task = next(t for t in get_tasks() if t.id == "Bank 2").model_copy(deep=True)
    task.ticket = "Выплатите дополнительную компенсацию30 по выбранной покупке, если подразделение выдало подходящее разрешение."
    permission = {
        "id": "adjustment-other-purchase",
        "customer_id": "gromov_a_1187",
        "transaction_id": "txn_552290",
        "program": "standard_cashback",
        "max_amount": 30.0,
        "status": "approved",
        "valid_from": "2026-08-01",
        "valid_to": "2026-08-31",
        "used": False,
    }
    task.initial_state = InitialState(
        initialization_data=InitializationData(
            agent_data={
                "cashback_adjustment_permissions": {permission["id"]: permission}
            }
        )
    )
    e = task.evaluation_criteria
    write = next(a for a in e.actions if a.name == "create_cashback_investigation")
    write.name = "execute_cashback_adjustment"
    write.arguments = {
        "permission_id": permission["id"],
        "transaction_id": "txn_552290",
        "amount": 30.0,
    }
    write.compare_args = ["permission_id", "transaction_id", "amount"]
    e.forbidden_actions = [
        a for a in e.forbidden_actions if a.name != "execute_cashback_adjustment"
    ]
    e.env_assertions = [
        EnvAssertion(
            env_type="assistant",
            func_name="assert_cashback_granted",
            arguments={"transaction_id": "txn_552290", "expected_amount": 30.0},
            assert_value=True,
        )
    ]
    witness = next(
        r for r in e.result_evidence if r.evidence_id == "bank2:authority-state"
    )
    witness.expected_fields = deepcopy(permission)
    e.result_argument_bindings.append(
        ResultArgumentBinding(
            binding_id="control-permission",
            source_action_id="get_adjustment_authority_Bank 2",
            target_action_id=write.action_id,
            argument_name="permission_id",
            result_pattern=r"""(?s).*?['"]id['"]\s*:\s*['"](?P<value>adjustment-other-purchase)['"].*""",
        )
    )
    # Required predecessor anchors enforce actual received authority before execution.
    source = next(
        a for a in e.actions if a.action_id == "get_adjustment_authority_Bank 2"
    )
    source.required_for_reward = True
    if route in {"wrong_program", "expired", "wrong_purchase"}:
        actual = task.initial_state.initialization_data.agent_data[
            "cashback_adjustment_permissions"
        ][permission["id"]]
        actual.update(
            {
                "wrong_program": {"program": "N40"},
                "expired": {"valid_to": "2026-08-27"},
                "wrong_purchase": {"transaction_id": "txn_552291"},
            }[route]
        )
    s = PlatformSession(task, get_environment, max_calls=100, max_errors=100)
    if route != "noop":
        for a in e.actions:
            if (
                route == "missing_permission_read"
                and a.name == "get_cashback_adjustment_permissions"
            ):
                continue
            s.call(a.name, deepcopy(a.arguments))
    s.call("done", {})
    if mode == "full":
        r = grade_full_session(s).reward_info
    else:
        sim = SimulationRun(
            id=s.token,
            task_id=task.id,
            start_time=s.created_at.isoformat(),
            end_time=s.created_at.isoformat(),
            duration=0,
            termination_reason=s.termination_reason(),
            messages=s.trajectory(),
            mode=CommunicationMode.HALF_DUPLEX.value,
        )
        r = evaluate_simulation(
            simulation=sim,
            task=task,
            evaluation_type=EvaluationType.ALL,
            solo_mode=True,
            domain=s.env.domain_name,
            mode=CommunicationMode.HALF_DUPLEX,
            strict_replay=True,
        )
    save_result(r, mode, route, expected, task, s)
    assert r.reward == expected, r.model_dump(mode="json")


def save_result(reward, mode, route, expected, task, session):
    """Persist compact component evidence without client identity secrets."""
    import hashlib
    import json
    import os
    from pathlib import Path

    destination = os.environ.get("BANK2_DECISIONS_EVIDENCE")
    if not destination:
        return
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    control = task.ticket.startswith("Выплатите дополнительную")
    output = {
        "scenario": "permitted-control" if control else "main",
        "route": route,
        "mode": mode,
        "expected": expected,
        "reward": reward.reward,
        "DB": reward.db_check.model_dump(mode="json") if reward.db_check else None,
        "ENV": [
            {"name": a.env_assertion.func_name, "met": a.met}
            for a in reward.env_assertions or []
        ],
        "ACTION": [
            {"id": a.action.action_id, "met": a.action_match}
            for a in reward.action_checks or []
        ],
        "info": reward.info,
        "task_sha256": hashlib.sha256(task.model_dump_json().encode()).hexdigest(),
        "session_fingerprint": hashlib.sha256(session.token.encode()).hexdigest(),
        "done": session.status.value == "done",
        "calls": [{"name": c.name, "error": r.error} for c, r in session.log],
    }
    (root / f"{output['scenario']}-{route}-{mode}.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    )


# Explicitly retain the historical scenario version; composite tests live separately.
pytestmark = pytest.mark.usefixtures("fees_rewards_parent_scenarios")
