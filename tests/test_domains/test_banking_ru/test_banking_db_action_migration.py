"""Addressed whole-grade evidence for the three-task DB × ACTION migration."""

from copy import deepcopy

import pytest

from tau2.data_model.message import AssistantMessage, Tick, ToolCall, ToolMessage
from tau2.data_model.tasks import EvaluationCriteria, RewardType, Task
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.environment.environment import Environment
from tau2.evaluator.evaluator_action import ActionEvaluator, FullDuplexActionEvaluator
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

MIGRATED = ("Bank 2", "Bank 40", "Bank 41")


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "task_id,action_id,amount,want",
    [
        ("Bank 40", "bank40:receipt-B", "139", 1),
        ("Bank 40", "bank40:receipt-B", "139.0", 1),
        ("Bank 40", "bank40:receipt-B", '"139.000"', 1),
        ("Bank 40", "bank40:receipt-B", "139.5", 0),
        ("Bank 40", "bank40:receipt-B", "139.01", 0),
        ("Bank 40", "bank40:receipt-B", '"139.5"', 0),
        ("Bank 40", "bank40:receipt-B", "1390", 0),
    ],
)
def test_shipped_numeric_result_patterns_compare_complete_amounts(
    task_id, action_id, amount, want, mode
):
    """Exercise the shipped ACTION witness with a controlled backend result.

    This tests result matching, not the DB consequences of a forged response.
    """
    source = next(t for t in get_tasks() if t.id == task_id)
    action = deepcopy(
        next(a for a in source.evaluation_criteria.actions if a.action_id == action_id)
    )
    task = Task(
        id="numeric-result-witness",
        user_scenario=source.user_scenario,
        evaluation_criteria=EvaluationCriteria(
            actions=[action],
            reward_basis=[RewardType.ACTION],
            require_successful_actions=True,
        ),
    )
    content = (
        '{"status":"posted","original_transaction_id":"txn_088219",'
        '"refunded": ' + amount + ', "balance": 0}'
    )
    call = ToolCall(id="numeric", name=action.name, arguments=action.arguments)
    result = ToolMessage(id=call.id, role="tool", content=content, error=False)
    if mode == "half":
        reward = ActionEvaluator.calculate_reward(
            task, [AssistantMessage(role="assistant", tool_calls=[call]), result]
        )
    else:
        reward = FullDuplexActionEvaluator.calculate_reward(
            task,
            [
                Tick(
                    tick_id=0,
                    timestamp="2026-10-27T00:00:00Z",
                    agent_tool_calls=[call],
                    agent_tool_results=[result],
                )
            ],
        )
    assert reward.reward == want, reward.info


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize("task_id", MIGRATED)
def test_migrated_rejected_format_probes_then_reference_are_recoverable(task_id, mode):
    """Rejected old reason/request/question formats cannot cancel a correct result."""
    task = next(t for t in get_tasks() if t.id == task_id)
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)
    for action in deepcopy(task.evaluation_criteria.actions):
        session.call(action.name, action.arguments)
        if action.name != "verify_identity":
            continue
        customer_id = action.arguments["customer_id"]
        transaction_id = next(
            t.id
            for t in session.env.tools.db.transactions.values()
            if t.customer_id == customer_id
        )
        # Each obsolete argument/request is rejected before a banking effect.
        probes = [
            (
                "escalate_to_human",
                {
                    "customer_id": customer_id,
                    "request": {"type": "app_access_support"},
                    "reason": "Ошибка формата",
                },
            ),
            (
                "cancel_dispute",
                {
                    "dispute_id": "nonexistent-recoverable-id",
                    "request": {"type": "customer_withdrawal"},
                    "reason": "Ошибка формата",
                },
            ),
            (
                "cancel_dispute",
                {
                    "dispute_id": "nonexistent-recoverable-id",
                    "request": {"type": "typo"},
                },
            ),
            (
                "refund_fee",
                {
                    "transaction_id": transaction_id,
                    "request": {"type": "fee_correction"},
                    "reason": "Ошибка формата",
                },
            ),
            (
                "refund_fee",
                {"transaction_id": transaction_id, "request": {"type": "typo"}},
            ),
            (
                "refund_transaction",
                {
                    "transaction_id": transaction_id,
                    "request": {"type": "purchase_refund"},
                    "reason": "Ошибка формата",
                },
            ),
            (
                "refund_transaction",
                {"transaction_id": transaction_id, "request": {"type": "typo"}},
            ),
            (
                "ask_client",
                {
                    "customer_id": customer_id,
                    "request": {"type": "identity", "method": "code_word"},
                    "question": "Подтвердите данные",
                },
            ),
        ]
        for name, arguments in probes:
            assert session.call(name, arguments).is_error, name
    session.call("done", {})
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert reward.reward_breakdown == {RewardType.DB: 1, RewardType.ACTION: 1}, (
        reward.info
    )
    assert reward.final_reward == 1, reward.info


@pytest.mark.parametrize("task_id", MIGRATED)
@pytest.mark.parametrize("route,want", [("reference", 1), ("noop", 0)])
def test_migrated_half_grade_never_runs_env(task_id, route, want, monkeypatch):
    task = next(t for t in get_tasks() if t.id == task_id)
    criteria = task.evaluation_criteria
    assert criteria.reward_basis == [RewardType.DB, RewardType.ACTION]
    assert criteria.banking_contract_version == "db_action_v1"
    assert not criteria.env_assertions
    session = PlatformSession(task, get_environment)
    session.replay_actions(deepcopy(criteria.actions) if route == "reference" else [])
    session.call("done", {})

    def forbidden_env(*args, **kwargs):
        raise AssertionError("Migrated task executed ENV")

    monkeypatch.setattr(Environment, "run_env_assertion", forbidden_env)
    reward = session.grade()
    assert reward.final_reward == want
    assert set(reward.reward_breakdown) == {RewardType.DB, RewardType.ACTION}
    assert not reward.env_assertions


@pytest.mark.parametrize("route,want", [("reference", 1), ("noop", 0)])
def test_newly_migrated_bank4_keeps_whole_half_grade(route, want):
    task = next(t for t in get_tasks() if t.id == "Bank 4")
    session = PlatformSession(task, get_environment)
    session.replay_actions(
        deepcopy(task.evaluation_criteria.actions) if route == "reference" else []
    )
    session.call("done", {})
    reward = session.grade()
    assert reward.final_reward == want
    assert set(reward.reward_breakdown) == {RewardType.DB, RewardType.ACTION}


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize("task_id", MIGRATED)
@pytest.mark.parametrize(
    "route,want",
    [
        ("idempotent_retry", 1),
        ("missing_write", 0),
        ("wrong_amount", 0),
        ("repaired_unknown_read", 1),
        ("foreign_read_then_reference", 0),
        ("rejected_foreign_read_then_reference", 1),
        ("rejected_foreign_resource_then_reference", 1),
        ("rejected_foreign_parameters_then_reference", 1),
        ("privacy_then_reference", 0),
        ("missing_received_basis", 0),
    ],
)
def test_migrated_business_routes(task_id, mode, route, want):
    task = next(t for t in get_tasks() if t.id == task_id)
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)
    actions = deepcopy(task.evaluation_criteria.actions)
    write = {
        "Bank 2": "bank2:v5:pay",
        "Bank 40": "refund_fee_Bank 40",
        "Bank 41": "bank41:document-delivery",
    }[task_id]
    basis = {
        "Bank 2": "bank2:v5:consent",
        "Bank 40": "bank40:final-consent",
        "Bank 41": "bank41:case-documents",
    }[task_id]
    delayed = None
    for action in actions:
        if route == "missing_write" and action.action_id == write:
            continue
        if route == "missing_received_basis" and action.action_id == basis:
            delayed = session.begin_call(action.name, dict(action.arguments), None)
            session.call(action.name, dict(action.arguments), attempt=delayed)
            continue
        arguments = deepcopy(action.arguments)
        if route == "repaired_unknown_read" and action.name == "verify_identity":
            session.call(action.name, arguments)
            session.call(
                "get_transaction_details",
                {"transaction_id": "nonexistent-recoverable-id"},
            )
            continue
        if route == "wrong_amount":
            if action.action_id == write and task_id == "Bank 40":
                arguments["amount"] -= 1
            elif action.action_id == write and task_id == "Bank 2":
                arguments["total_amount"] -= 1
            elif action.action_id == "bank41:investigation":
                arguments["items"][0]["amount"] += 1
        session.call(action.name, arguments)
        if action.name == "verify_identity":
            if route == "rejected_foreign_resource_then_reference":
                foreign = next(
                    p
                    for p in session.env.tools.db.promotions.values()
                    if p.customer_id != arguments["customer_id"]
                )
                session.call(
                    "get_promotion_activations",
                    {
                        "customer_id": arguments["customer_id"],
                        "promotion_id": foreign.id,
                    },
                )
            elif route == "rejected_foreign_parameters_then_reference":
                foreign = next(
                    c.id
                    for c in session.env.tools.db.customers.values()
                    if c.id != arguments["customer_id"]
                )
                session.call("get_transactions", {"customer_id": foreign, "limit": 0})
            elif route == "rejected_foreign_read_then_reference":
                session.call(
                    "get_reward_statement_status",
                    {
                        "customer_id": "egorova_n_3358"
                        if task_id != "Bank 41"
                        else "gromov_a_1187",
                        "statement_id": "nonexistent-statement",
                    },
                )
            elif route == "foreign_read_then_reference":
                session.call(
                    "get_accounts",
                    {
                        "customer_id": "egorova_n_3358"
                        if task_id != "Bank 41"
                        else "gromov_a_1187"
                    },
                )
            elif route == "privacy_then_reference":
                session.call(
                    "ask_client",
                    {
                        "customer_id": arguments["customer_id"],
                        "question": "OTP: "
                        + session.env.tools.db.customers[
                            arguments["customer_id"]
                        ].otp_code,
                    },
                )
        if route == "idempotent_retry" and action.action_id == write:
            session.call(action.name, dict(action.arguments))
    if delayed:
        session.result_sent(delayed)
    session.call("done", {})
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert reward.final_reward == want, reward.info
    assert set(reward.reward_breakdown) == {RewardType.DB, RewardType.ACTION}
    if route in {
        "foreign_read_then_reference",
        "privacy_then_reference",
        "missing_received_basis",
    }:
        assert reward.reward_breakdown == {RewardType.DB: 1, RewardType.ACTION: 0}, (
            reward.info
        )
        info = reward.info["action"]
        if route == "foreign_read_then_reference":
            assert any(
                failure["tool"] == "get_accounts"
                and failure["reason"] == "foreign_owner"
                for failure in info["tool_access_policy"]
            ), info
        elif route == "privacy_then_reference":
            assert not info.get("tool_access_policy"), info
            secret_rules = set(info.get("forbidden_argument_patterns", [])) | set(
                info.get("forbidden_message_patterns", [])
            )
            assert {"common_otp_ask_client", "common_otp_message"} & secret_rules, info
        else:
            assert not info.get("tool_access_policy"), info
            assert not info.get("forbidden_argument_patterns"), info
            assert not info.get("forbidden_message_patterns"), info
            bindings = info["result_argument_bindings"]
            receipt_failures = {
                failure["binding_id"]
                for failure in bindings
                if isinstance(failure, dict)
                and failure["reason"] == "source_value_not_received_or_mismatched"
            }
            business_failures = {
                failure.split(":", 1)[1]
                for failure in bindings
                if isinstance(failure, str)
            }
            expected_receipts = {
                "Bank 2": {"bank2:v5:received-consent"},
                "Bank 40": {
                    "bank40:consent:refund_fee_Bank 40",
                    "bank40:consent:bank40:refund-C",
                },
                "Bank 41": set(),
            }[task_id]
            expected_business = {
                "Bank 2": {"resolution_packet_not_confirmed"},
                "Bank 40": {"fee_current_consent_not_received"},
                "Bank 41": {"document_metadata_not_received"},
            }[task_id]
            assert expected_receipts <= receipt_failures, info
            assert expected_business <= business_failures, info


@pytest.mark.parametrize("mode", ["half", "full"])
def test_telecom_env_assertions_still_execute(mode):
    from tau2.domains.telecom.environment import get_environment as telecom_environment
    from tau2.domains.telecom.environment import get_tasks as telecom_tasks
    from tau2.evaluator.evaluator_env import (
        EnvironmentEvaluator,
        FullDuplexEnvironmentEvaluator,
    )

    task = next(t for t in telecom_tasks() if t.evaluation_criteria.env_assertions)
    evaluator = (
        EnvironmentEvaluator if mode == "half" else FullDuplexEnvironmentEvaluator
    )
    kwargs = {"full_trajectory": []}
    reward = evaluator.calculate_reward(telecom_environment, task, **kwargs)
    assert len(reward.env_assertions) == 2
    assert not all(check.met for check in reward.env_assertions)
    assert reward.reward_breakdown[RewardType.ENV_ASSERTION] == 0


@pytest.mark.parametrize(
    "invalid", ["unmarked_two_basis", "residual_env", "model_basis", "duplicate_basis"]
)
def test_full_helper_rejects_undeclared_or_mixed_contract(invalid):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 4"))
    criteria = task.evaluation_criteria
    if invalid == "unmarked_two_basis":
        criteria.reward_basis = [RewardType.DB, RewardType.ACTION]
        criteria.env_assertions = []
        criteria.banking_contract_version = None
        criteria.banking_source_read_contract = None
    elif invalid == "residual_env":
        criteria.reward_basis = [RewardType.DB, RewardType.ACTION]
        criteria.banking_contract_version = "db_action_v1"
        from tau2.data_model.tasks import EnvAssertion

        criteria.env_assertions = [
            EnvAssertion(env_type="assistant", func_name="unused", arguments={})
        ]
    elif invalid == "model_basis":
        criteria.reward_basis = [
            RewardType.DB,
            RewardType.NL_ASSERTION,
            RewardType.ACTION,
        ]
    else:
        criteria.reward_basis = [RewardType.DB, RewardType.ACTION, RewardType.ACTION]
    session = PlatformSession(task, get_environment)
    session.call("done", {})
    with pytest.raises(ValueError, match="Full grade|Two-basis"):
        grade_full_session(session)


@pytest.mark.parametrize("mutation", ["phantom_env", "missing_business_profile"])
def test_full_helper_revalidates_mutable_migration_profile(mutation):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 40"))
    if mutation == "phantom_env":
        task.evaluation_criteria.reward_basis = [
            RewardType.DB,
            RewardType.ENV_ASSERTION,
            RewardType.ACTION,
        ]
    else:
        task.evaluation_criteria.business_process_only = False
    session = PlatformSession(task, get_environment)
    session.call("done", {})
    with pytest.raises(ValueError, match="db_action_v1|Full grade"):
        grade_full_session(session)


@pytest.mark.parametrize("mode", ["half", "full"])
def test_bank40_rejected_overamount_then_correct_is_recoverable(mode):
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    session = PlatformSession(task, get_environment, max_errors=0)
    for action in deepcopy(task.evaluation_criteria.actions):
        if action.action_id == "refund_fee_Bank 40":
            bad = {**action.arguments, "amount": 140}
            response = session.call(action.name, bad)
            assert response.is_error
        session.call(action.name, dict(action.arguments))
    session.call("done", {})
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert reward.final_reward == 1, reward.info


def test_bank41_author_fixture_has_reached_n40_payout_date():
    """A due payout is an authoring precondition, not a runtime ENV component."""
    from datetime import date

    task = next(t for t in get_tasks() if t.id == "Bank 41")
    session = PlatformSession(task, get_environment)
    db = session.env.tools.db
    assert date.fromisoformat(db.today) >= date(2026, 9, 1)
    case = db.cases["case-n40-current"]
    assert case.customer_id == "egorova_n_3358"
    assert case.reward_program == "N40"
    assert case.reward_period == "2026-08"


@pytest.mark.parametrize("mode", ["half", "full"])
def test_bank41_explicit_optional_null_document_version_is_allowed(mode):
    task = next(t for t in get_tasks() if t.id == "Bank 41")
    session = PlatformSession(task, get_environment)
    for action in deepcopy(task.evaluation_criteria.actions):
        arguments = deepcopy(action.arguments)
        if action.name == "deliver_case_document":
            arguments["expected_version"] = None
        session.call(action.name, arguments)
    session.call("done", {})
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert reward.final_reward == 1, reward.info


@pytest.mark.parametrize("mode", ["half", "full"])
def test_bank41_equivalent_numeric_item_amount_is_allowed(mode):
    task = next(t for t in get_tasks() if t.id == "Bank 41")
    session = PlatformSession(task, get_environment)
    actions = deepcopy(task.evaluation_criteria.actions)
    for action in actions:
        if action.name == "amend_cashback_investigation":
            for item in action.arguments["items"]:
                item["amount"] = float(item["amount"])
    session.replay_actions(actions)
    session.call("done", {})
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert reward.final_reward == 1, reward.info


@pytest.mark.parametrize("mode", ["half", "full"])
def test_bank2_retry_accepts_equivalent_payment_packet(mode):
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    session = PlatformSession(task, get_environment)
    session.replay_actions(deepcopy(task.evaluation_criteria.actions))
    arguments = deepcopy(
        next(
            a.arguments
            for a in task.evaluation_criteria.actions
            if a.name == "execute_reward_resolution"
        )
    )
    arguments["payouts"].reverse()
    for item in arguments["payouts"]:
        item["amount"] = int(item["amount"])
    arguments["component_ids"].reverse()
    assert not session.call("execute_reward_resolution", arguments).is_error
    session.call("done", {})
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert reward.final_reward == 1, reward.info


@pytest.mark.parametrize(
    "route,want",
    [
        ("api", 1),
        ("article", 1),
        ("cat", 1),
        ("read_document", 1),
        ("missing", 0),
        ("wrong_article", 0),
        ("late_article", 0),
        ("missing_activation", 0),
        ("foreign_read", 0),
        ("disclosure", 0),
    ],
)
def test_bank2_promotion_conditions_accept_equivalent_received_sources(route, want):
    """Whole HALF/FULL grades; only the static promotion terms are interchangeable."""
    from tau2.evaluator.evaluator_action import _result_evidence_calls
    from tau2.evaluator.trace_evidence import evidence_from_messages

    task = next(t for t in get_tasks() if t.id == "Bank 2")
    s = PlatformSession(task, get_environment, mcp_layout="directory")
    path = "/knowledge/reward_partner_offer.md"
    actions = deepcopy(task.evaluation_criteria.actions)
    if route != "api":
        actions = [a for a in actions if a.name != "get_promotions"]
    if route == "missing_activation":
        actions = [a for a in actions if a.name != "get_promotion_activations"]
    source = next(
        a for a in actions if a.name == "file_read" and a.arguments.get("path") == path
    )
    if route in {"missing", "late_article"}:
        actions.remove(source)
    elif route == "wrong_article":
        source.arguments["path"] = "/knowledge/reward_resolution_rights.md"
    elif route == "cat":
        source.name, source.arguments = "corpus_command", {"command": "cat " + path}
    elif route == "read_document":
        meta = s.env.tools.corpus.documents[path]["metadata"]
        source.name, source.arguments = (
            "read_document",
            {"document_id": meta["document_id"], "revision_id": meta["revision_id"]},
        )
    s.replay_actions(actions)
    if route == "late_article":
        s.replay_actions([source])

    def call(system, name, args):
        assert not s.call(
            "bank_directory",
            {"operation": "open_tool", "system_id": system, "tool_name": name},
        ).is_error
        return s.call(
            "bank_call", {"system_id": system, "tool_name": name, "arguments": args}
        )

    if route == "foreign_read":
        assert not call(
            "core_banking", "get_accounts", {"customer_id": "egorova_n_3358"}
        ).is_error
    elif route == "disclosure":
        assert not call(
            "support",
            "reply_to_ticket",
            {
                "customer_id": "gromov_a_1187",
                "text": "Ваш OTP: "
                + s.env.tools.db.customers["gromov_a_1187"].otp_code,
            },
        ).is_error
    assert not call("support", "done", {}).is_error
    criteria = [
        e
        for e in task.evaluation_criteria.result_evidence
        if e.evidence_id.startswith("bank2:v4:offer:")
    ]
    assert len(criteria) == 2
    if route == "api":
        # Prove that the original API branch itself still satisfies both criteria,
        # independently of the separate unchanged document-reading obligations.
        observed = [
            c
            for c in evidence_from_messages(s.trajectory()).observed
            if not (c.call.name == "file_read" and c.call.arguments.get("path") == path)
        ]
        assert all(
            _result_evidence_calls(observed, task.evaluation_criteria.actions, e)
            for e in criteria
        )
    for reward in (s.grade(), grade_full_session(s).reward_info):
        assert reward.final_reward == want, reward.info
        assert reward.reward_breakdown[RewardType.DB] == 1
        if route in {"missing", "wrong_article", "late_article"}:
            assert {e.evidence_id for e in criteria} <= set(
                reward.info["action"]["result_evidence"]
            )


def test_referral_link_is_a_db_obligation_even_when_case_ids_are_normalized():
    from tau2.evaluator.db_state_checks import state_check_failures

    policy = {
        "case_referral": {
            "customer_id": "owner",
            "transaction_id": "purchase",
            "amount": 7450,
        }
    }
    statement = {
        "id": "statement",
        "customer_id": "owner",
        "category": "merchant_investigation",
        "transaction_id": "purchase",
        "amount": 7450,
        "status": "open",
        "created_at": "2026-08-28",
    }
    handoff = {
        **statement,
        "id": "handoff",
        "category": "escalation",
        "amount": None,
        "case_id": "statement",
        "escalation_request": {"type": "card_claim_referral"},
    }
    state = {
        "today": "2026-08-28",
        "cases": {"statement": statement, "handoff": handoff},
    }
    assert state_check_failures(state, [], banking_contract=policy) == []
    statement["id"] = "renamed"
    assert state_check_failures(state, [], banking_contract=policy) == [
        "case_referral_state"
    ]


def test_pending_limit_history_checks_successful_effects_from_initial_state():
    from tau2.evaluator.banking_business_trace import banking_business_failures
    from tau2.evaluator.trace_evidence import evidence_from_messages

    policy = {"pending_limit_changes": [{"card_id": "card", "limit_type": "sbp"}]}
    db = {
        "cards": {
            "card": {"id": "card", "account_id": "account", "customer_id": "owner"}
        },
        "limit_requests": {
            "pending": {
                "customer_id": "owner",
                "request_type": "spending_limit",
                "status": "pending",
                "requires_decision": True,
                "channel": "sbp",
                "period": "per_operation",
                "object_type": "card",
                "object_id": "card",
            }
        },
    }
    call = ToolCall(
        id="attempt",
        name="set_limit",
        arguments={"card_id": "card", "limit_type": "sbp", "amount": 100},
    )
    for error, want in [(True, []), (False, ["attempt:pending_limit_change"])]:
        trace = evidence_from_messages(
            [
                AssistantMessage(role="assistant", tool_calls=[call]),
                ToolMessage(
                    id=call.id, role="tool", content='{"sbp_limit":100}', error=error
                ),
            ]
        )
        assert banking_business_failures(trace, policy, initial_db=db) == want


def test_all_shipped_tasks_declare_the_migrated_contract():
    tasks = get_tasks()
    assert len(tasks) == 61
    for task in tasks:
        c = task.evaluation_criteria
        c.validate_banking_contract_version()
        assert c.reward_basis == [RewardType.DB, RewardType.ACTION]
        assert c.banking_contract_version == "db_action_v1"
        assert c.banking_confidentiality_contract == "banking_v1"
        assert c.banking_source_read_contract == "knowledge_v1"
        assert not c.env_assertions
