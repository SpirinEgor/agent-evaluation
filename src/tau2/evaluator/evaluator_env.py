import inspect
from itertools import product
from typing import Callable

from loguru import logger

from tau2.data_model.message import (
    AssistantMessage,
    Message,
    Tick,
    UserMessage,
)
from tau2.data_model.simulation import DBCheck, EnvAssertionCheck, RewardInfo
from tau2.data_model.tasks import Action, RewardType, Task
from tau2.environment.environment import Environment
from tau2.evaluator.evaluator_base import EvaluatorBase
from tau2.evaluator.trace_evidence import (
    ObservedCall,
    evidence_from_messages,
    evidence_from_ticks,
)


def _db_action_argument_variants(
    action: Action, observed: list[ObservedCall]
) -> list[dict]:
    """Bind only declared fields; retain the canonical route and all other state."""
    variants = [dict(action.arguments)]
    if not action.db_bind_arguments:
        return variants
    for item in observed:
        result = item.result
        if (
            result is None
            or result.error
            or result.id != item.call.id
            or result.requestor != item.call.requestor
            or item.result_position is None
            or item.result_position <= item.call_position
            or not action.compare_with_tool_call(item.call)
        ):
            continue
        if any(
            name not in item.call.arguments
            or type(item.call.arguments[name]) is not type(action.arguments[name])
            for name in action.db_bind_arguments
        ):
            continue
        arguments = {
            **action.arguments,
            **{name: item.call.arguments[name] for name in action.db_bind_arguments},
        }
        if arguments not in variants:
            variants.append(arguments)
    return variants


def _reference_db_hashes(
    environment_constructor: Callable[[], Environment],
    task: Task,
    initialization_data,
    initialization_actions,
    message_history: list[Message],
    env_kwargs: dict,
    strict_replay: bool,
    observed: list[ObservedCall] | None = None,
) -> set[tuple[str | None, str | None]]:
    """Replay canonical and explicitly declared equivalent exact end states."""
    criteria = task.evaluation_criteria
    assert criteria is not None
    hashes = set()
    for overrides, additions, replacements in product(
        [{}, *criteria.db_alternative_action_overrides],
        [[], *criteria.db_alternative_action_additions],
        [{}, *criteria.db_alternative_action_replacements],
    ):
        actions = [replacements.get(a.action_id, a) for a in criteria.actions or []]
        for addition in additions:
            if addition.db_reference_before_action_id is None:
                actions.append(addition)
            else:
                index = next(
                    i
                    for i, a in enumerate(actions)
                    if a.action_id == addition.db_reference_before_action_id
                )
                actions.insert(index, addition)
        choices = [_db_action_argument_variants(a, observed or []) for a in actions]
        for arguments_route in product(*choices):
            gold_environment = environment_constructor(**env_kwargs)
            gold_environment.set_state(
                initialization_data=initialization_data,
                initialization_actions=initialization_actions,
                message_history=list(message_history),
                strict=strict_replay,
            )
            for action, base_arguments in zip(actions, arguments_route):
                arguments = {**base_arguments, **overrides.get(action.action_id, {})}
                try:
                    gold_environment.make_tool_call(
                        tool_name=action.name,
                        requestor=action.requestor,
                        **arguments,
                    )
                except Exception as error:
                    if overrides or additions or replacements:
                        raise ValueError(
                            f"Invalid DB alternative: {action.name}({arguments})"
                        ) from error
                    logger.warning(
                        f"Error in golden actions {action.name}({arguments}): {error}"
                    )
            hashes.add(
                (gold_environment.get_db_hash(), gold_environment.get_user_db_hash())
            )
    return hashes


def _trajectory_constraints(
    task: Task, trajectory: list, *, full: bool
) -> RewardInfo | None:
    """Preserve semantic/safety guards when ACTION only grades structural steps.

    Successful arguments and received sources are ENV witnesses. Pure call-order
    constraints are graded once in ACTION; only reception dependencies stay here.
    """
    criteria = task.evaluation_criteria
    if criteria is None or criteria.action_reward_scope != "presence_and_order":
        return None
    from tau2.evaluator.evaluator_action import (
        ActionEvaluator,
        FullDuplexActionEvaluator,
    )

    guarded = task.model_copy(deep=True)
    guarded.evaluation_criteria.action_reward_scope = "full_contract"
    guarded.evaluation_criteria.action_order = [
        pair for pair in criteria.action_order if pair.require_result_before_target
    ]
    evaluator = FullDuplexActionEvaluator if full else ActionEvaluator
    result = evaluator.calculate_reward(guarded, trajectory)
    info = dict(result.info or {})
    failed = [
        check.action.action_id
        for check in result.action_checks or []
        if not check.action_match
    ]
    if failed:
        info["semantic_action_witnesses"] = failed
    return result.model_copy(update={"info": info or None})


class EnvironmentEvaluator(EvaluatorBase[Message]):
    """
    Evaluator focuses on endstate of the simulation environment.
    """

    @classmethod
    def calculate_reward(
        cls,
        environment_constructor: Callable[[], Environment],
        task: Task,
        full_trajectory: list[
            Message
        ],  # FIXME: It would be better to be able to get only the messages that are after the initial state
        solo_mode: bool = False,
        env_kwargs: dict = None,
        strict_replay: bool = True,
    ) -> RewardInfo:
        """
        Calculate the reward for the simulation.
        Args:
            environment_constructor: Callable[[], Environment]
            task: Task
            full_trajectory: list[Message] (Must include the message history from task initial state)
            solo_mode: bool
            strict_replay: forwarded to Environment.set_state(strict=...). Set
                False when re-grading historical trajectories whose recorded
                tool outputs may cosmetically differ from current tool code.
        Returns:
            RewardInfo
        """
        if task.evaluation_criteria is None:
            return RewardInfo(
                reward=1.0,
                info={"note": "No evaluation criteria"},
            )
        expected_actions = task.evaluation_criteria.actions
        env_assertions = task.evaluation_criteria.env_assertions
        if expected_actions is None and env_assertions is None:
            return RewardInfo(
                reward=1.0,
                db_check=DBCheck(db_match=True, db_reward=1.0),
                info={"note": "No expected actions or env assertions"},
            )

        initialization_data = None
        if (
            task.initial_state is not None
            and task.initial_state.initialization_data is not None
        ):
            initialization_data = task.initial_state.initialization_data

        initialization_actions = None
        if (
            task.initial_state is not None
            and task.initial_state.initialization_actions is not None
        ):
            initialization_actions = task.initial_state.initialization_actions

        message_history = []
        if (
            task.initial_state is not None
            and task.initial_state.message_history is not None
        ):
            message_history = task.initial_state.message_history

        if env_kwargs is None:
            env_kwargs = {}

        env_kwargs = dict(env_kwargs)
        if "task_id" in inspect.signature(environment_constructor).parameters:
            env_kwargs.setdefault("task_id", task.id)

        predicted_environment = environment_constructor(
            solo_mode=solo_mode, **env_kwargs
        )

        predicted_environment.set_state(
            initialization_data=initialization_data,
            initialization_actions=initialization_actions,
            message_history=list(full_trajectory),
            strict=strict_replay,
        )

        # Comparing the environments with any explicitly equivalent end state.
        reference_hashes = _reference_db_hashes(
            environment_constructor,
            task,
            initialization_data,
            initialization_actions,
            message_history,
            env_kwargs,
            strict_replay,
            evidence_from_messages(full_trajectory).observed,
        )
        predicted_agent_db_hash = predicted_environment.get_db_hash()
        predicted_user_db_hash = predicted_environment.get_user_db_hash()
        if (predicted_agent_db_hash, predicted_user_db_hash) in reference_hashes:
            db_reward = 1.0
            db_match = True
        else:
            db_reward = 0.0
            db_match = False

        db_check = DBCheck(db_match=db_match, db_reward=db_reward)

        # Run env assertions
        env_assertions = task.evaluation_criteria.env_assertions or []
        env_assertion_checks = []
        env_assertion_reward = 1.0
        for env_assertion in env_assertions:
            success = predicted_environment.run_env_assertion(
                env_assertion,
                raise_assertion_error=False,
            )
            res = EnvAssertionCheck(
                env_assertion=env_assertion,
                met=success,
                reward=1.0 if success else 0.0,
            )
            env_assertion_checks.append(res)
            env_assertion_reward *= res.reward

        constraints = _trajectory_constraints(task, full_trajectory, full=False)
        if constraints is not None:
            env_assertion_reward *= constraints.reward
        reward = 1.0
        reward_breakdown = {}
        if RewardType.DB in task.evaluation_criteria.reward_basis:
            reward_breakdown[RewardType.DB] = db_reward
            reward *= db_reward
        if RewardType.ENV_ASSERTION in task.evaluation_criteria.reward_basis:
            reward_breakdown[RewardType.ENV_ASSERTION] = env_assertion_reward
            reward *= env_assertion_reward

        return RewardInfo(
            reward=reward,
            db_check=db_check,
            env_assertions=env_assertion_checks,
            reward_basis=task.evaluation_criteria.reward_basis,
            reward_breakdown=reward_breakdown,
            info=constraints.info if constraints is not None else None,
        )


class FullDuplexEnvironmentEvaluator(EvaluatorBase[Tick]):
    """
    Evaluator focuses on endstate of the simulation environment.
    """

    @classmethod
    def ticks_to_message_history(cls, ticks: list[Tick]) -> list[Message]:
        """
        Convert a list of Ticks to a message history suitable for Environment.set_state().

        The order follows the execution order in FullDuplexOrchestrator:
        - User tool calls are processed before agent tool calls within each tick
        - Each tool call message is followed by its corresponding tool results

        Args:
            ticks: List of Tick objects from full-duplex simulation.

        Returns:
            List of Messages in the format expected by Environment.set_state():
            [UserMessage with tool_calls, ToolMessage results, AssistantMessage with tool_calls, ToolMessage results, ...]
        """
        messages: list[Message] = []

        for tick in ticks:
            # 1. User tool calls first (processed before agent in orchestrator)
            if tick.user_tool_calls:
                user_msg = UserMessage(
                    role="user",
                    content=tick.user_chunk.content if tick.user_chunk else None,
                    tool_calls=tick.user_tool_calls,
                    timestamp=(
                        tick.user_chunk.timestamp if tick.user_chunk else tick.timestamp
                    ),
                    contains_speech=(
                        tick.user_chunk.contains_speech if tick.user_chunk else False
                    ),
                )
                messages.append(user_msg)
            # Results may arrive on a later tick without another request.
            messages.extend(tick.user_tool_results)

            # 2. Agent tool calls second
            if tick.agent_tool_calls:
                agent_msg = AssistantMessage(
                    role="assistant",
                    content=tick.agent_chunk.content if tick.agent_chunk else None,
                    tool_calls=tick.agent_tool_calls,
                    timestamp=(
                        tick.agent_chunk.timestamp
                        if tick.agent_chunk
                        else tick.timestamp
                    ),
                    contains_speech=(
                        tick.agent_chunk.contains_speech if tick.agent_chunk else False
                    ),
                )
                messages.append(agent_msg)
            messages.extend(tick.agent_tool_results)

        return messages

    @classmethod
    def calculate_reward(
        cls,
        environment_constructor: Callable[[], Environment],
        task: Task,
        full_trajectory: list[Tick],
        solo_mode: bool = False,
        env_kwargs: dict = None,
        strict_replay: bool = True,
    ) -> RewardInfo:
        """
        Calculate the reward for the simulation.
        Args:
            environment_constructor: Callable[[], Environment]
            task: Task
            full_trajectory: list[Tick]
            solo_mode: bool
            env_kwargs: dict
            strict_replay: forwarded to Environment.set_state(strict=...). Set
                False when re-grading historical trajectories whose recorded
                tool outputs may cosmetically differ from current tool code.
        Returns:
            RewardInfo
        """
        if env_kwargs is None:
            env_kwargs = {}
        env_kwargs = dict(env_kwargs)
        if "task_id" in inspect.signature(environment_constructor).parameters:
            env_kwargs.setdefault("task_id", task.id)
        if task.evaluation_criteria is None:
            return RewardInfo(
                reward=1.0,
                info={"note": "No evaluation criteria"},
            )
        expected_actions = task.evaluation_criteria.actions
        env_assertions = task.evaluation_criteria.env_assertions
        if expected_actions is None and env_assertions is None:
            return RewardInfo(
                reward=1.0,
                db_check=DBCheck(db_match=True, db_reward=1.0),
                info={"note": "No expected actions or env assertions"},
            )

        initialization_data = None
        if (
            task.initial_state is not None
            and task.initial_state.initialization_data is not None
        ):
            initialization_data = task.initial_state.initialization_data

        initialization_actions = None
        if (
            task.initial_state is not None
            and task.initial_state.initialization_actions is not None
        ):
            initialization_actions = task.initial_state.initialization_actions

        message_history = []
        if (
            task.initial_state is not None
            and task.initial_state.message_history is not None
        ):
            message_history = task.initial_state.message_history

        # Convert ticks to message history for set_state
        # Note: Audio native does not support task history, so we only use the simulation trajectory
        predicted_message_history = cls.ticks_to_message_history(full_trajectory)

        predicted_environment = environment_constructor(
            solo_mode=solo_mode, **env_kwargs
        )
        predicted_environment.set_state(
            initialization_data=initialization_data,
            initialization_actions=initialization_actions,
            message_history=predicted_message_history,
            strict=strict_replay,
        )

        # Comparing the environments with any explicitly equivalent end state.
        reference_hashes = _reference_db_hashes(
            environment_constructor,
            task,
            initialization_data,
            initialization_actions,
            message_history,
            env_kwargs,
            strict_replay,
            evidence_from_ticks(full_trajectory).observed,
        )
        predicted_agent_db_hash = predicted_environment.get_db_hash()
        predicted_user_db_hash = predicted_environment.get_user_db_hash()
        if (predicted_agent_db_hash, predicted_user_db_hash) in reference_hashes:
            db_reward = 1.0
            db_match = True
        else:
            db_reward = 0.0
            db_match = False

        db_check = DBCheck(db_match=db_match, db_reward=db_reward)

        # Run env assertions
        env_assertions = task.evaluation_criteria.env_assertions or []
        env_assertion_checks = []
        env_assertion_reward = 1.0
        for env_assertion in env_assertions:
            success = predicted_environment.run_env_assertion(
                env_assertion,
                raise_assertion_error=False,
            )
            res = EnvAssertionCheck(
                env_assertion=env_assertion,
                met=success,
                reward=1.0 if success else 0.0,
            )
            env_assertion_checks.append(res)
            env_assertion_reward *= res.reward

        constraints = _trajectory_constraints(task, full_trajectory, full=True)
        if constraints is not None:
            env_assertion_reward *= constraints.reward
        reward = 1.0
        reward_breakdown = {}
        if RewardType.DB in task.evaluation_criteria.reward_basis:
            reward_breakdown[RewardType.DB] = db_reward
            reward *= db_reward
        if RewardType.ENV_ASSERTION in task.evaluation_criteria.reward_basis:
            reward_breakdown[RewardType.ENV_ASSERTION] = env_assertion_reward
            reward *= env_assertion_reward

        return RewardInfo(
            reward=reward,
            db_check=db_check,
            env_assertions=env_assertion_checks,
            reward_basis=task.evaluation_criteria.reward_basis,
            reward_breakdown=reward_breakdown,
            info=constraints.info if constraints is not None else None,
        )
