"""Successful reward mutations retain their history after a later repair."""

import json

import pytest

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
    offer.update(component_count="0", components_complete="True", components=[])
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


def composite_rows():
    """A hand-calculated 12 nominal / 10 cash menu, without a hidden optimum."""
    from copy import deepcopy

    offer = {
        "id": "offer",
        "customer_id": "own",
        "case_id": "case",
        "period": "2026-07",
        "transaction_ids": ["t1", "t2"],
        "status": "approved",
        "version": 1,
        "rights": "preserve",
        "valid_from": "2026-08-01",
        "valid_to": "2026-09-01",
        "amount": 10,
        "amount_basis": "incremental",
        "terms_id": "rights",
        "allocation_terms_id": "amounts",
        "allocation_first_transaction_id": "t1",
        "allocation_first_amount": 4,
        "selection_budget": {"id": "budget", "bank": 6, "partner": 6},
        "component_count": 2,
        "components_complete": True,
        "components": [
            {
                "id": "x",
                "exclusive_group": "bank",
                "amount": 5,
                "settlement_deduction": 1,
                "bank_cost": 3,
                "partner_cost": 2,
                "allocation_first_amount": 1,
            },
            {
                "id": "y",
                "exclusive_group": "partner",
                "amount": 7,
                "settlement_deduction": 1,
                "bank_cost": 3,
                "partner_cost": 4,
                "allocation_first_amount": 3,
                "required_companions": [["x"]],
            },
        ],
    }
    args = {
        "case_id": "case",
        "offer_id": "offer",
        "offer_version": 1,
        "component_ids": ["x", "y"],
        "payouts": [
            {"transaction_id": "t1", "amount": 4},
            {"transaction_id": "t2", "amount": 6},
        ],
        "total_amount": 10,
        "confirmation_id": "consent",
        "expected_revision": 2,
        "idempotency_key": "key",
    }
    consent = {
        "customer_id": "own",
        "status": "confirmed",
        "confirmation_id": "consent",
        "data": {
            k: deepcopy(v)
            for k, v in args.items()
            if k not in {"confirmation_id", "expected_revision", "idempotency_key"}
        }
        | {"state_revision": 2},
    }
    return [
        (
            "get_reward_resolution_offers",
            {"customer_id": "own", "case_id": "case"},
            [offer],
            False,
        ),
        ("ask_client", {"customer_id": "own"}, consent, False),
        ("execute_reward_resolution", args, {"status": "succeeded"}, False),
    ]


def test_composite_action_accepts_received_valid_selection_and_safe_retry():
    rows = composite_rows()
    assert failures(rows) == []
    assert failures([rows[1], rows[0], rows[2]])
    assert failures(rows + [rows[-1]]) == []
    # Valid but smaller is a DB objective failure, not an ACTION failure.
    rows[2][1].update(
        component_ids=["x"],
        total_amount=4,
        payouts=[
            {"transaction_id": "t1", "amount": 1},
            {"transaction_id": "t2", "amount": 3},
        ],
    )
    rows[1][2]["data"].update(
        {k: rows[2][1][k] for k in ["component_ids", "total_amount", "payouts"]}
    )
    assert failures(rows) == []


def test_composite_action_accepts_complete_received_pages_and_rejects_missing_or_late_page():
    from copy import deepcopy

    rows = composite_rows()
    offer = rows[0][2][0]
    parts = offer.pop("components")
    offer.update(components=[], components_complete=False)
    pages = [
        (
            "get_reward_resolution_components",
            {"offer_id": "offer", "offset": i, "limit": 1},
            {
                "offer_id": "offer",
                "offer_version": 1,
                "offset": i,
                "total": 2,
                "next_offset": i + 1 if i == 0 else None,
                "components": [p],
            },
            False,
        )
        for i, p in enumerate(parts)
    ]
    assert failures([rows[0], *pages, *rows[1:]]) == []
    assert failures([rows[0], rows[1], *pages, rows[2]])
    assert failures([rows[0], pages[0], *rows[1:]])
    assert failures([rows[0], pages[0], *rows[1:], pages[1]])
    foreign = deepcopy(pages[1])
    foreign[2]["offer_id"] = "foreign"
    assert failures([rows[0], pages[0], foreign, *rows[1:]])
    duplicate = deepcopy(pages[1])
    duplicate[2]["components"] = [parts[0]]
    assert failures([rows[0], pages[0], duplicate, *rows[1:]])


def test_composite_action_rejects_fabricated_success_with_invalid_terms_or_packet():
    for defect in [
        "budget",
        "dependency",
        "incompatibility",
        "group",
        "net",
        "allocation",
        "unknown",
        "duplicate",
    ]:
        rows = composite_rows()
        offer = rows[0][2][0]
        args = rows[2][1]
        if defect == "budget":
            offer["selection_budget"]["bank"] = 5
        elif defect == "dependency":
            offer["components"][1]["required_companions"] = [["absent"]]
        elif defect == "incompatibility":
            offer["components"][1]["incompatible_with"] = ["x"]
        elif defect == "group":
            offer["components"][1]["exclusive_group"] = "bank"
        elif defect == "net":
            offer["components"][0]["settlement_deduction"] = 2
        elif defect == "allocation":
            offer["components"][0]["allocation_first_amount"] = 2
        elif defect == "unknown":
            args["component_ids"] = ["x", "absent"]
        else:
            args["component_ids"] = ["x", "y", "y"]
        assert failures(rows), defect


def test_component_pages_must_match_received_offer_version_and_allow_recovery():
    from copy import deepcopy

    rows = composite_rows()
    offer = rows[0][2][0]
    offer["version"] = 2
    rows[1][2]["data"]["offer_version"] = 2
    rows[2][1]["offer_version"] = 2
    parts = offer.pop("components")
    offer.update(components=[], components_complete=False)
    pages = [
        (
            "get_reward_resolution_components",
            {"offer_id": "offer", "offset": i},
            {
                "offer_id": "offer",
                "offer_version": 2,
                "offset": i,
                "total": 2,
                "components": [part],
            },
            False,
        )
        for i, part in enumerate(parts)
    ]
    assert failures([rows[0], *pages, *rows[1:]]) == []
    stale = deepcopy(pages[0])
    stale[2]["offer_version"] = 1
    assert failures([rows[0], stale, pages[1], *rows[1:]])
    assert failures([rows[0], stale, *pages, *rows[1:]]) == []
    unversioned = deepcopy(pages[0])
    unversioned[2].pop("offer_version")
    assert failures([rows[0], unversioned, pages[1], *rows[1:]])


def test_new_received_offer_invalidates_prior_consent_for_fabricated_success():
    from copy import deepcopy

    rows = composite_rows()
    new_offer = deepcopy(rows[0])
    new_offer[2][0]["version"] = 2
    assert failures([*rows[:2], new_offer, rows[2]])


def test_preliminary_funding_cannot_authorize_success_but_safe_rejection_recovers():
    from copy import deepcopy

    rows = composite_rows()
    rows[0][2][0]["funding_status"] = "pending"
    assert failures(rows)
    final = deepcopy(rows[0])
    final[2][0]["funding_status"] = "final"
    refused = (rows[2][0], rows[2][1], {"status": "funding_pending"}, False)
    assert failures([rows[0], rows[1], refused, final, rows[1], rows[2]]) == []


def test_funding_refresh_requires_received_acceptance_and_safe_pending_needs_no_revision():
    from tau2.evaluator.cashback_state import cashback_state_failures

    args = {"customer_id": "own", "period": "2026-07"}
    receipt = (
        "get_reward_statement_status",
        {"customer_id": "own", "statement_id": "st"},
        {
            "id": "st",
            "customer_id": "own",
            "case_id": "case",
            "status": "accepted",
            "transaction_ids": ["t1"],
        },
        False,
    )
    refresh = (
        "refresh_cashback_settlement",
        args,
        {
            "status": "settled",
            "customer_id": "own",
            "period": "2026-07",
            "funding_decisions": [
                {
                    "event_id": "event",
                    "offer_id": "offer",
                    "statement_id": "st",
                    "previous_version": 1,
                    "new_version": 2,
                }
            ],
        },
        False,
    )
    assert failures([refresh])
    assert failures([receipt, refresh]) == []
    delayed = trace_of([receipt, refresh])
    delayed.observed[0] = delayed.observed[0].model_copy(
        update={"result_position": delayed.observed[1].call_position + 1}
    )
    from tau2.evaluator.reward_business_trace import reward_business_failures

    assert reward_business_failures(delayed, POLICY)
    for status in ["funding_pending", "offer_changed"]:
        rows = [
            (
                "execute_reward_resolution",
                {"offer_id": "offer", "expected_revision": 999},
                {"status": status},
                False,
            )
        ]
        assert (
            cashback_state_failures(
                trace_of(rows).observed, {"customer_id": "own", "period": "2026-07"}
            )
            == []
        )


def conditional_rows():
    rows = composite_rows()
    rows[0][2][0]["components"][1]["eligibility"] = {
        "kind": "partner_confirmation",
        "transaction_id": "t2",
    }
    evidence = {"y": {"contract_id": "contract", "record_id": "partner"}}
    rows[1][2]["data"]["eligibility_evidence"] = evidence
    rows[2][1]["eligibility_evidence"] = evidence
    sources = [
        (
            "get_transaction_details",
            {"transaction_id": "t2"},
            {
                "id": "t2",
                "customer_id": "own",
                "account_id": "account",
                "card_id": "new",
                "receipt_id": "wanted",
                "kind": "purchase",
                "status": "posted",
                "purchased_at": "2026-07-26T12:00:00+03:00",
            },
            False,
        ),
        (
            "get_reward_contracts",
            {"customer_id": "own"},
            [
                {
                    "id": "contract",
                    "customer_id": "own",
                    "account_id": "account",
                    "card_ids": ["old", "new"],
                    "effective_from": "2026-06-01",
                    "effective_to": None,
                    "replaced_card_id": "old",
                    "replacement_card_id": "new",
                    "replacement_effective_from": "2026-07-20",
                }
            ],
            False,
        ),
        (
            "get_partner_confirmations",
            {"customer_id": "own"},
            {
                "complete": True,
                "records": [
                    {
                        "id": "partner",
                        "customer_id": "own",
                        "contract_id": "contract",
                        "receipt_id": "wanted",
                        "enrollment_card_id": "old",
                        "status": "accepted",
                    }
                ],
            },
            False,
        ),
    ]
    return rows, sources


def test_conditional_success_needs_received_matching_grounds_before_consent():
    from copy import deepcopy

    rows, sources = conditional_rows()
    assert failures([rows[0], *sources, *rows[1:]]) == []
    assert failures([rows[0], *rows[1:]])
    assert failures([rows[0], rows[1], *sources, rows[2]])
    wrong = deepcopy(sources)
    wrong[-1][2]["records"][0]["receipt_id"] = "other"
    assert failures([rows[0], *wrong, *rows[1:]])
    changed = deepcopy(rows)
    changed[2][1]["eligibility_evidence"] = {
        "y": {"contract_id": "contract", "record_id": "other"}
    }
    assert failures([changed[0], *sources, *changed[1:]])
    # A safe rejection followed by a properly evidenced consent may recover.
    denied = (rows[2][0], rows[2][1], {"error": "no grounds"}, True)
    assert failures([rows[0], rows[1], denied, *sources, *rows[1:]]) == []


def test_conditional_ground_in_same_batch_is_not_received_before_consent():
    from tau2.evaluator.reward_business_trace import reward_business_failures

    rows, sources = conditional_rows()
    trace = trace_of([rows[0], *sources, *rows[1:]])
    partner = trace.observed[3]
    consent = trace.observed[4]
    trace.observed[3] = partner.model_copy(
        update={"result_position": consent.call_position}
    )
    assert reward_business_failures(trace, POLICY)


def test_service_incident_evidence_uses_same_contract_and_purchase_time():
    rows, sources = conditional_rows()
    rows[0][2][0]["components"][1]["eligibility"]["kind"] = "service_incident"
    rows[2][1]["eligibility_evidence"]["y"]["record_id"] = "incident"
    sources[-1] = (
        "get_service_incidents",
        {"customer_id": "own"},
        {
            "complete": True,
            "records": [
                {
                    "id": "incident",
                    "customer_id": "own",
                    "contract_id": "contract",
                    "kind": "activation_sync_failure",
                    "starts_at": "2026-07-26T11:00:00+03:00",
                    "ends_at": "2026-07-26T13:00:00+03:00",
                }
            ],
        },
        False,
    )
    assert failures([rows[0], *sources, *rows[1:]]) == []
    sources[-1][2]["records"][0]["contract_id"] = "archived"
    assert failures([rows[0], *sources, *rows[1:]])


def test_harmless_operation_summary_cannot_erase_received_purchase_facts():
    rows, sources = conditional_rows()
    summary = (
        "get_operations",
        {"customer_id": "own"},
        [{"id": "t2", "date": "2026-07-26", "amount": 100, "merchant": "Cafe"}],
        False,
    )
    assert failures([rows[0], *sources, summary, *rows[1:]]) == []
    # Summary alone remains insufficient to justify conditional authority.
    assert failures([rows[0], summary, *sources[1:], *rows[1:]])


def shared_reserve_rows(*, refreshed=False, capacity=12):
    """Two independent scopes spend the same bank/partner reserve."""
    from copy import deepcopy

    first = composite_rows()
    first_offer = first[0][2][0]
    first_offer["selection_budget"].update(shared=True, bank=capacity, partner=capacity)
    first_offer["funding_reserve"] = {
        "id": "budget",
        "bank": capacity,
        "partner": capacity,
        "remaining_bank": capacity,
        "remaining_partner": capacity,
    }
    first[2][2]["funding_charge"] = {"reserve_id": "budget", "bank": 6, "partner": 6}
    second = deepcopy(first)
    offer = second[0][2][0]
    offer["id"] = "second"
    offer["transaction_ids"] = ["t3", "t4"]
    offer["allocation_first_transaction_id"] = "t3"
    args = second[2][1]
    args.update(
        offer_id="second",
        confirmation_id="second-consent",
        idempotency_key="second-key",
    )
    args["payouts"][0]["transaction_id"] = "t3"
    args["payouts"][1]["transaction_id"] = "t4"
    second[1][2]["confirmation_id"] = "second-consent"
    second[1][2]["data"].update(offer_id="second", payouts=deepcopy(args["payouts"]))
    if refreshed:
        offer["funding_reserve"].update(
            remaining_bank=capacity - 6, remaining_partner=capacity - 6
        )
        return first + second
    first[0][2].append(offer)
    return first + second[1:]


def test_shared_reserve_cannot_be_spent_twice_from_one_received_snapshot():
    problems = failures(shared_reserve_rows(capacity=10))
    assert "4:shared_funding_exceeded" in problems


def test_shared_reserve_refresh_does_not_double_count_previous_payment():
    for refreshed in [False, True]:
        rows = shared_reserve_rows(refreshed=refreshed)
        assert failures(rows) == []
        assert failures(rows + [rows[-1]]) == []


def test_shared_reserve_requires_received_snapshot_and_exact_charge():
    from copy import deepcopy

    rows = shared_reserve_rows()
    for offer in rows[0][2]:
        offer.pop("funding_reserve")
    assert any("shared_funding_not_received" in item for item in failures(rows))
    rows = shared_reserve_rows()
    bad = deepcopy(rows)
    bad[-1][2]["funding_charge"]["partner"] = 0
    assert any("shared_funding_charge_mismatch" in item for item in failures(bad))


def test_shared_payment_result_must_be_received_before_dependent_case_change():
    from tau2.evaluator.reward_business_trace import reward_business_failures

    rows = shared_reserve_rows()
    rows.append(
        (
            "amend_cashback_investigation",
            {"case_id": "case", "items": [{"transaction_id": "t1", "amount": 3}]},
            {"id": "case"},
            False,
        )
    )
    messages = []
    for i, (name, args, result, error) in enumerate(rows):
        messages.extend(
            [
                AssistantMessage(
                    role="assistant",
                    tool_calls=[ToolCall(id=str(i), name=name, arguments=args)],
                ),
                ToolMessage(
                    role="tool", id=str(i), content=json.dumps(result), error=error
                ),
            ]
        )
    delayed = messages.pop(5)
    messages.append(delayed)
    trace = evidence_from_messages(messages)
    assert any(
        "payment_result_not_received" in x
        for x in reward_business_failures(trace, POLICY)
    )


@pytest.mark.parametrize("delayed", ["original", "repeat"])
@pytest.mark.parametrize(
    "equivalence",
    ["normalized", "changed_key", "changed_packet", "missing_key", "failed", "late"],
)
def test_payment_receipt_can_come_from_equivalent_successful_replay(
    delayed, equivalence
):
    from copy import deepcopy

    from tau2.evaluator.reward_business_trace import reward_business_failures

    rows = composite_rows()
    replay = deepcopy(rows[-1])
    replay[1]["component_ids"].reverse()
    replay[1]["payouts"].reverse()
    replay[1]["total_amount"] = "10.00"
    replay[1]["payouts"][0]["amount"] = "6.00"
    replay[1]["expected_revision"] = 99
    if equivalence == "changed_key":
        replay[1]["idempotency_key"] = "different"
    elif equivalence == "changed_packet":
        replay[1]["confirmation_id"] = "different"
    elif equivalence == "missing_key":
        rows[-1][1].pop("idempotency_key")
        replay[1].pop("idempotency_key")
    elif equivalence == "failed":
        replay = (replay[0], replay[1], {"error": "rejected"}, True)
    rows += [
        replay,
        (
            "amend_cashback_investigation",
            {"case_id": "case", "items": [{"transaction_id": "t1", "amount": 3}]},
            {"id": "case"},
            False,
        ),
    ]
    messages, held = [], []
    for i, (name, args, result, error) in enumerate(rows):
        messages.append(
            AssistantMessage(
                role="assistant",
                tool_calls=[ToolCall(id=str(i), name=name, arguments=args)],
            )
        )
        response = ToolMessage(
            role="tool", id=str(i), content=json.dumps(result), error=error
        )
        if i == (2 if delayed == "original" else 3) or (
            equivalence == "late" and i in {2, 3}
        ):
            held.append(response)
        else:
            messages.append(response)
    problems = reward_business_failures(evidence_from_messages(messages + held), POLICY)
    receipt_problems = [p for p in problems if "payment_result_not_received" in p]
    expected = delayed == "original" and equivalence != "normalized"
    if delayed == "repeat" and equivalence in {
        "changed_key",
        "changed_packet",
        "missing_key",
        "late",
    }:
        expected = True
    assert bool(receipt_problems) is expected, problems
