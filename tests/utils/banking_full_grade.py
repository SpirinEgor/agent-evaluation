"""Test-only original FULL/ALL replay of DONE sessions, without a half grade.

Import this module as tests.utils.banking_full_grade. Explicit ticks retain formation and reception order; delayed result-only ticks
are supported and must not be rewritten into immediate responses.
"""

from dataclasses import dataclass

from tau2.data_model.message import AssistantMessage, Tick, ToolMessage
from tau2.data_model.simulation import RewardInfo, SimulationRun
from tau2.data_model.tasks import RewardType
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.orchestrator.modes import CommunicationMode
from tau2.platform.session import PlatformSession, SessionStatus

if __name__ != "tests.utils.banking_full_grade":
    raise ImportError("Use canonical tests.utils.banking_full_grade import")

@dataclass(frozen=True)
class FullGradeResult:
    reward_info: RewardInfo
    simulation: SimulationRun
    invocation: dict


def grade_full_session(
    session: PlatformSession, *, ticks: list[Tick] | None = None
) -> FullGradeResult:
    """Run all original full evaluators once; observers cannot replace the result."""
    if session.status is not SessionStatus.DONE:
        raise ValueError("Full grade requires an actual DONE session")
    criteria = session.task.evaluation_criteria
    if criteria.banking_contract_version is not None:
        # Tasks are mutable in trajectory tests. Revalidate before accepting a
        # copied profile so a stale third basis cannot fabricate a skipped ENV.
        try:
            criteria.validate_banking_contract_version()
        except ValueError as error:
            raise ValueError(
                f"Full grade invalid migrated contract: {error}"
            ) from error
    bases = criteria.reward_basis
    legacy = {RewardType.DB, RewardType.ENV_ASSERTION, RewardType.ACTION}
    migrated = {RewardType.DB, RewardType.ACTION}
    if len(bases) != len(set(bases)) or set(bases) not in (legacy, migrated):
        raise ValueError(
            "Full grade requires declared DB × [ENV_ASSERTION ×] ACTION; no model bases"
        )
    if set(bases) == migrated and (
        getattr(criteria, "banking_contract_version", None) != "db_action_v1"
        or criteria.env_assertions
        or criteria.action_reward_scope != "full_contract"
        or not criteria.require_successful_actions
    ):
        raise ValueError(
            "Two-basis full grade requires explicit full contract and zero residual ENV"
        )
    for call, result in session.log:
        if (
            call.id != result.id
            or call.requestor != "assistant"
            or result.requestor != "assistant"
        ):
            raise ValueError(
                "Actual call/result identity and assistant roles must match"
            )
    if ticks is None:
        # Preserve formation and reception order, including delayed responses.
        ticks = [
            Tick(
                tick_id=i,
                timestamp=session.created_at.isoformat(),
                agent_chunk=message if isinstance(message, AssistantMessage) else None,
                agent_tool_calls=message.tool_calls or []
                if isinstance(message, AssistantMessage)
                else [],
                agent_tool_results=[message]
                if isinstance(message, ToolMessage)
                else [],
            )
            for i, message in enumerate(session.trajectory())
        ]
    if any(t.user_tool_calls or t.user_tool_results for t in ticks):
        raise ValueError(
            "User tool ticks are outside actual assistant session provenance"
        )
    calls = [call.model_dump(mode="json") for t in ticks for call in t.agent_tool_calls]
    results = [
        result.model_dump(mode="json") for t in ticks for result in t.agent_tool_results
    ]
    expected_calls = [
        c.model_dump(mode="json")
        for m in session.trajectory()
        if isinstance(m, AssistantMessage)
        for c in (m.tool_calls or [])
    ]
    expected_results = [
        m.model_dump(mode="json")
        for m in session.trajectory()
        if isinstance(m, ToolMessage)
    ]
    if calls != expected_calls or results != expected_results:
        raise ValueError(
            "Full ticks must preserve actual call and received-result order"
        )
    simulation = SimulationRun(
        id=session.token,
        task_id=session.task.id,
        start_time=session.created_at.isoformat(),
        end_time=session.finished_at.isoformat(),
        duration=(session.finished_at - session.created_at).total_seconds(),
        termination_reason=session.termination_reason(),
        ticks=ticks,
        mode=CommunicationMode.FULL_DUPLEX.value,
        info={"platform": "mcp", "mcp_events": session.mcp_events},
    )
    reward = evaluate_simulation(
        simulation=simulation,
        task=session.task,
        evaluation_type=EvaluationType.ALL,
        solo_mode=True,
        domain=session.env.domain_name,
        mode=CommunicationMode.FULL_DUPLEX,
        strict_replay=True,
        env_kwargs=getattr(session.env, "evaluation_env_kwargs", None),
    )
    invocation = {
        "entrypoint": "tau2.evaluator.evaluator.evaluate_simulation",
        "evaluation_type": "all",
        "requested_mode": "full_duplex",
        "simulation_mode": simulation.mode,
        "solo_mode": True,
        "strict_replay": True,
        "trajectory_scope": "actual_session_calls_results_sequential_or_declared_ticks",
    }
    return FullGradeResult(reward, simulation, invocation)
