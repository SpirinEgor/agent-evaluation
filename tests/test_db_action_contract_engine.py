"""Selective two-component contracts must not reinterpret legacy tasks."""

import pytest
from pydantic import ValidationError

from tau2.data_model.tasks import (
    EvaluationCriteria,
    RewardType,
    Task,
    UserScenario,
)


def migrated_criteria(**updates):
    return EvaluationCriteria(
        **{
            "banking_contract_version": "db_action_v1",
            "business_process_only": True,
            "completion_mode": "completion_only",
            "action_reward_scope": "full_contract",
            "require_successful_actions": True,
            "reward_basis": ["DB", "ACTION"],
            "env_assertions": [],
            **updates,
        }
    )


def test_explicit_two_component_contract_loads():
    task = Task(
        id="arbitrary-id",
        user_scenario=UserScenario(instructions="test"),
        evaluation_criteria=migrated_criteria(),
    )
    assert task.evaluation_criteria.banking_contract_version == "db_action_v1"


@pytest.mark.parametrize(
    "updates",
    [
        {"reward_basis": ["DB", "ENV_ASSERTION", "ACTION"]},
        {"reward_basis": ["DB"]},
        {"require_successful_actions": False},
        {
            "env_assertions": [
                {"env_type": "user", "func_name": "assert_something", "arguments": {}}
            ]
        },
        {"business_process_only": False},
        {"reward_basis": ["DB", "ACTION", "ACTION"]},
        {"banking_business_contract": {}},
        {"banking_business_contract": {"fee_refunds": {"t": -1}}},
        {"banking_business_contract": {"fee_refunds": {"t": 1}, "ignored": True}},
    ],
)
def test_migration_rejects_invalid_contract(updates):
    with pytest.raises(ValidationError):
        migrated_criteria(**updates)


def test_legacy_env_contract_still_loads():
    criteria = EvaluationCriteria(
        completion_mode="completion_only",
        action_reward_scope="presence_and_order",
        reward_basis=["DB", "ENV_ASSERTION", "ACTION"],
    )
    task = Task(
        id="old",
        user_scenario=UserScenario(instructions="x"),
        evaluation_criteria=criteria,
    )
    assert RewardType.ENV_ASSERTION in task.evaluation_criteria.reward_basis


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("migrated", [False, True])
def test_env_diagnostics_run_only_for_legacy_contract(full, migrated):
    from tau2.data_model.tasks import EnvAssertion
    from tau2.domains.mock.environment import get_environment
    from tau2.evaluator.evaluator_env import (
        EnvironmentEvaluator,
        FullDuplexEnvironmentEvaluator,
    )

    criteria = (
        migrated_criteria(actions=[])
        if migrated
        else EvaluationCriteria(actions=[], reward_basis=["DB"])
    )
    # Stale in-memory diagnostics must not become a hidden third evaluator.
    criteria.env_assertions = [
        EnvAssertion(
            env_type="assistant",
            func_name="assert_number_of_tasks",
            arguments={"user_id": "user_1", "expected_number": 999},
        )
    ]
    task = Task.model_construct(
        id="any", evaluation_criteria=criteria, initial_state=None
    )
    evaluator = FullDuplexEnvironmentEvaluator if full else EnvironmentEvaluator
    result = evaluator.calculate_reward(get_environment, task, [])
    assert result.reward == 1.0
    assert result.db_check.db_match
    assert len(result.env_assertions) == (0 if migrated else 1)
    if not migrated:
        assert result.env_assertions[0].met is False


def test_terminal_state_checks_are_typed_and_migration_scoped():
    raw = {
        "check_id": "closed",
        "collection": "cases",
        "where": {"customer_id": "own"},
        "field": "reservations",
        "equals": [],
    }
    criteria = migrated_criteria(db_state_checks=[raw])
    assert criteria.db_state_checks[0].check_id == "closed"
    with pytest.raises(ValidationError):
        EvaluationCriteria(db_state_checks=[raw], reward_basis=["DB"])


@pytest.mark.parametrize("full", [False, True])
def test_extra_terminal_predicate_gates_db_component(full):
    from tau2.domains.mock.environment import get_environment
    from tau2.evaluator.evaluator_env import (
        EnvironmentEvaluator,
        FullDuplexEnvironmentEvaluator,
    )

    criteria = migrated_criteria(
        actions=[],
        db_state_checks=[
            {
                "check_id": "missing",
                "collection": "tasks",
                "where": {},
                "field": "status",
                "equals": "impossible",
            }
        ],
    )
    task = Task(
        id="any",
        user_scenario=UserScenario(instructions="x"),
        evaluation_criteria=criteria,
    )
    evaluator = FullDuplexEnvironmentEvaluator if full else EnvironmentEvaluator
    result = evaluator.calculate_reward(get_environment, task, [])
    assert result.reward == 0.0
    assert result.db_check.db_match is False
    assert result.info["db_state_checks"] == ["missing"]


def test_terminal_predicate_runs_without_reference_actions_or_env_assertions():
    from tau2.domains.mock.environment import get_environment
    from tau2.evaluator.evaluator_env import EnvironmentEvaluator

    criteria = migrated_criteria(
        actions=None,
        env_assertions=None,
        db_state_checks=[
            {
                "check_id": "terminal",
                "collection": "tasks",
                "where": {},
                "field": "status",
                "equals": "impossible",
            }
        ],
    )
    task = Task(
        id="no-reference",
        user_scenario=UserScenario(instructions="x"),
        evaluation_criteria=criteria,
    )
    result = EnvironmentEvaluator.calculate_reward(get_environment, task, [])
    assert result.reward == 0.0

@pytest.mark.parametrize(
    'legacy',
    [
        {'message_claim_contract': {'version': 'banking-claims-v1', 'families': ['card_block'], 'tool_arguments': {}}},
        {'numeric_message_claims': [{'rule_id': 'legacy'}]},
    ],
)
def test_removed_message_claim_contracts_fail_explicitly(legacy):
    with pytest.raises(ValidationError, match='message claim.*no longer supported'):
        EvaluationCriteria(**legacy)
