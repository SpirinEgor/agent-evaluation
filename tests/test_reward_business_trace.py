"""Successful reward mutations retain their history after a later repair."""

import json

from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage
from tau2.evaluator.trace_evidence import evidence_from_messages


def trace_of(rows):
    messages = []
    for i, (name, args, result, error) in enumerate(rows):
        messages += [
            AssistantMessage(
                role="assistant",
                tool_calls=[ToolCall(id=str(i), name=name, arguments=args)],
            ),
            ToolMessage(
                role="tool", id=str(i), content=json.dumps(result), error=error
            ),
        ]
    return evidence_from_messages(messages)


POLICY = {
    "customer_id": "own",
    "case_id": "case",
    "period": "2026-07",
    "bank_date": "2026-08-28",
    "transaction_ids": ["t1", "t2"],
    "initial_case_items": {"old": 7},
}


def failures(rows):
    from tau2.evaluator.reward_business_trace import reward_business_failures

    return reward_business_failures(trace_of(rows), POLICY)


def test_successful_extra_creation_fails_even_after_legal_amendment():
    rows = [
        (
            "create_cashback_investigation",
            {"customer_id": "own"},
            {"id": "extra"},
            False,
        ),
        (
            "amend_cashback_investigation",
            {"case_id": "case", "items": [{"transaction_id": "t1", "amount": 14}]},
            {"id": "case"},
            False,
        ),
    ]
    assert failures(rows) == ["0:forbidden_reward_creation"]


def test_rejected_no_effect_creation_can_recover():
    assert (
        failures(
            [
                (
                    "create_cashback_investigation",
                    {"customer_id": "own"},
                    {"error": "bad args"},
                    True,
                )
            ]
        )
        == []
    )


def test_amendment_preserves_unrelated_initial_claims_and_allows_split():
    assert (
        failures(
            [
                (
                    "amend_cashback_investigation",
                    {
                        "case_id": "case",
                        "items": [
                            {"transaction_id": "t1", "amount": 14},
                            {"transaction_id": "old", "amount": 7},
                        ],
                    },
                    {"id": "case"},
                    False,
                )
            ]
        )
        == []
    )
    assert failures(
        [
            (
                "amend_cashback_investigation",
                {"case_id": "case", "items": [{"transaction_id": "old", "amount": 8}]},
                {"id": "case"},
                False,
            )
        ]
    ) == ["0:outside_reward_scope"]


def test_adjustment_needs_received_authority_and_exact_retry_is_not_new_spend():
    permission = {
        "id": "p",
        "customer_id": "own",
        "transaction_id": "t1",
        "period": "2026-07",
        "program": "standard_cashback",
        "status": "approved",
        "used": False,
        "valid_from": "2026-08-01",
        "valid_to": "2026-09-01",
        "max_amount": 10,
        "remaining_amount": 10,
    }
    args = {
        "permission_id": "p",
        "transaction_id": "t1",
        "amount": 6,
        "idempotency_key": "k",
    }
    rows = [
        (
            "get_cashback_adjustment_permissions",
            {"customer_id": "own"},
            [permission],
            False,
        ),
        (
            "execute_cashback_adjustment",
            args,
            {"status": "succeeded", "granted": 6, "ledger_transaction_id": "l"},
            False,
        ),
    ]
    assert failures(rows + [rows[-1]]) == []
    assert failures(rows[1:]) == ["0:adjustment_authority_not_received"]
    assert failures(
        rows
        + [
            (
                "execute_cashback_adjustment",
                {**args, "idempotency_key": "new"},
                {"status": "succeeded", "granted": 6, "ledger_transaction_id": "l2"},
                False,
            )
        ]
    ) == ["2:adjustment_exceeds_authority"]


def test_resolution_requires_exact_received_confirmed_packet():
    args = {
        "case_id": "case",
        "offer_id": "offer",
        "offer_version": 1,
        "payouts": [
            {"transaction_id": "t1", "amount": 4},
            {"transaction_id": "t2", "amount": 6},
        ],
        "total_amount": 10,
        "confirmation_id": "consent",
        "expected_revision": 2,
        "idempotency_key": "key",
    }
    offer = {
        "id": "offer",
        "customer_id": "own",
        "case_id": "case",
        "period": "2026-07",
        "transaction_ids": ["t1", "t2"],
        "status": "approved",
        "version": 1,
        "valid_from": "2026-08-01",
        "valid_to": "2026-09-01",
        "amount": 10,
        "amount_basis": "incremental",
        "allocation_first_transaction_id": "t1",
        "allocation_first_amount": 4,
    }
    consent = {
        "customer_id": "own",
        "status": "confirmed",
        "confirmation_id": "consent",
        "data": {
            k: v
            for k, v in args.items()
            if k not in {"confirmation_id", "expected_revision", "idempotency_key"}
        }
        | {"state_revision": 2},
    }
    prefix = [
        (
            "get_reward_resolution_offers",
            {"customer_id": "own", "case_id": "case"},
            [offer],
            False,
        ),
        ("ask_client", {"customer_id": "own"}, consent, False),
    ]
    payment = ("execute_reward_resolution", args, {"status": "succeeded"}, False)
    assert failures(prefix + [payment]) == []
    assert failures(
        prefix + [(payment[0], {**args, "total_amount": 9}, payment[2], False)]
    ) == ["2:resolution_packet_not_confirmed", "2:resolution_amount_outside_offer"]
    assert failures(prefix + [payment, payment]) == []
    for retry_args in [
        {**args, "payouts": list(reversed(args["payouts"]))},
        {**args, "component_ids": None},
        {**args, "component_ids": []},
        {
            **args,
            "total_amount": 10.0,
            "payouts": [
                {"transaction_id": "t2", "amount": 6.0},
                {"transaction_id": "t1", "amount": 4.0},
            ],
        },
    ]:
        assert (
            failures(prefix + [payment, (payment[0], retry_args, payment[2], False)])
            == []
        )
    changed = failures(
        prefix + [payment, (payment[0], {**args, "total_amount": 9}, payment[2], False)]
    )
    assert "3:idempotency_packet_changed" in changed
    assert failures(
        prefix
        + [
            payment,
            (payment[0], {**args, "idempotency_key": "other"}, payment[2], False),
        ]
    ) == ["3:overlapping_reward_execution"]


def test_action_evaluator_runs_reward_guard_only_for_migrated_contract():
    from tau2.data_model.tasks import EvaluationCriteria, Task, UserScenario
    from tau2.evaluator.evaluator_action import ActionEvaluator

    trace = trace_of(
        [
            (
                "create_cashback_investigation",
                {"customer_id": "own"},
                {"id": "extra"},
                False,
            )
        ]
    )
    messages = [trace.messages[0], trace.observed[0].result]
    criteria = EvaluationCriteria(
        banking_contract_version="db_action_v1",
        business_process_only=True,
        reward_basis=["DB", "ACTION"],
        require_successful_actions=True,
        reward_business_contract=POLICY,
    )
    task = Task(
        id="x",
        user_scenario=UserScenario(instructions="x"),
        evaluation_criteria=criteria,
    )
    assert ActionEvaluator.calculate_reward(task, messages).reward == 0.0
    task.evaluation_criteria = EvaluationCriteria(reward_basis=["ACTION"])
    assert ActionEvaluator.calculate_reward(task, messages).reward == 1.0


def test_partial_adjustment_can_refresh_remaining_authority_before_second_payment():
    permission = {
        "id": "p",
        "customer_id": "own",
        "transaction_id": "t1",
        "period": "2026-07",
        "program": "standard_cashback",
        "status": "approved",
        "used": False,
        "valid_from": "2026-08-01",
        "valid_to": "2026-09-01",
        "max_amount": 10,
        "remaining_amount": 10,
    }
    rows = [
        (
            "get_cashback_adjustment_permissions",
            {"customer_id": "own"},
            [permission],
            False,
        ),
        (
            "execute_cashback_adjustment",
            {
                "permission_id": "p",
                "transaction_id": "t1",
                "amount": 6,
                "idempotency_key": "one",
            },
            {"status": "succeeded", "granted": 6},
            False,
        ),
        (
            "get_cashback_adjustment_permissions",
            {"customer_id": "own"},
            [{**permission, "remaining_amount": 4, "used_amount": 6}],
            False,
        ),
        (
            "execute_cashback_adjustment",
            {
                "permission_id": "p",
                "transaction_id": "t1",
                "amount": 4,
                "idempotency_key": "two",
            },
            {"status": "succeeded", "granted": 4},
            False,
        ),
    ]
    assert failures(rows) == []


def test_received_offer_and_permission_support_mcp_scalar_strings():
    permission = {
        "id": "p",
        "customer_id": "own",
        "transaction_id": "t1",
        "period": "2026-07",
        "program": "standard_cashback",
        "status": "approved",
        "used": "False",
        "valid_from": "2026-08-01",
        "valid_to": "2026-09-01",
        "max_amount": "10.0",
        "remaining_amount": "10.0",
    }
    assert (
        failures(
            [
                (
                    "get_cashback_adjustment_permissions",
                    {"customer_id": "own"},
                    [permission],
                    False,
                ),
                (
                    "execute_cashback_adjustment",
                    {"permission_id": "p", "transaction_id": "t1", "amount": 6},
                    {"status": "succeeded", "granted": 6},
                    False,
                ),
            ]
        )
        == []
    )


def test_unknown_amendment_item_cannot_match_missing_initial_amount():
    assert failures(
        [
            (
                "amend_cashback_investigation",
                {
                    "case_id": "case",
                    "items": [{"transaction_id": "unknown", "amount": None}],
                },
                {"id": "case"},
                False,
            )
        ]
    ) == ["0:outside_reward_scope"]
