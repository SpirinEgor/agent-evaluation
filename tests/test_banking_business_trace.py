"""Business history boundaries independent of final-state repair."""

import json

from tau2.data_model.message import ToolCall, ToolMessage
from tau2.evaluator.banking_business_trace import banking_business_failures
from tau2.evaluator.trace_evidence import ObservedCall, TraceEvidence


def trace(rows):
    return TraceEvidence(
        observed=[
            ObservedCall(
                call=ToolCall(id=str(i), name=name, arguments=args),
                result=ToolMessage(
                    id=str(i), role="tool", content=json.dumps(result), error=error
                ),
                call_position=i * 2,
                result_position=i * 2 + 1,
                execution_index=i,
            )
            for i, (name, args, result, error) in enumerate(rows)
        ]
    )


def fee_rows(amount=10, key="a", ledger="ledger-a"):
    return [
        (
            "get_fee_refund_state",
            {"transaction_id": "fee"},
            {"id": "fee", "remaining": 10, "state_version": "v1"},
            False,
        ),
        (
            "ask_client",
            {"request": {"type": "fee_refund_confirmation"}},
            {
                "confirmation_id": "yes",
                "status": "confirmed",
                "data": {
                    "refunds": [{"transaction_id": "fee", "amount": amount}],
                    "state_versions": {"fee": "v1"},
                },
            },
            False,
        ),
        (
            "refund_fee",
            {
                "transaction_id": "fee",
                "amount": amount,
                "confirmation_id": "yes",
                "idempotency_key": key,
            },
            {"refunded": amount, "refund_transaction_id": ledger},
            False,
        ),
    ]


def test_fee_atomic_and_cumulative_budgets_and_idempotent_retry():
    policy = {"fee_refunds": {"fee": 10}}
    rows = fee_rows()
    assert not banking_business_failures(trace(rows + [rows[-1]]), policy)
    # The received balance permits 11, but the declared total budget is only 10.
    # Consent matches 11, so neither remaining balance nor consent masks the cap.
    over_budget = fee_rows(11)
    over_budget[0][2]["remaining"] = 20
    assert banking_business_failures(trace(over_budget), policy) == [
        "2:fee_cumulative_exceeded"
    ]
    # Returning a second independent posting cannot be repaired by a later DB edit.
    assert banking_business_failures(
        trace(rows + [fee_rows(key="b", ledger="ledger-b")[-1]]), policy
    )


def test_fee_uses_remaining_after_every_successful_posting():
    rows = fee_rows(6)
    second = fee_rows(6, key="b", ledger="ledger-b")[-1]
    assert banking_business_failures(
        trace(rows + [second]), {"fee_refunds": {"fee": 20}}
    )


def test_rejected_fee_error_can_recover_and_same_batch_consent_is_unreceived():
    rows = fee_rows()
    rejected = (
        "refund_fee",
        {"transaction_id": "fee", "amount": 100},
        "ERR_INVALID_ARGUMENT",
        True,
    )
    assert not banking_business_failures(
        trace([rejected] + rows), {"fee_refunds": {"fee": 10}}
    )
    t = trace(rows)
    t.observed[1] = t.observed[1].model_copy(update={"result_position": 5})
    assert banking_business_failures(t, {"fee_refunds": {"fee": 10}})


def document_rows():
    return [
        (
            "get_case_documents",
            {"case_id": "case"},
            [
                {
                    "id": "doc",
                    "customer_id": "owner",
                    "case_id": "case",
                    "version": "2",
                    "purpose": "registration_confirmation",
                    "audience": "customer",
                    "transmissible": True,
                    "readiness": "ready",
                    "allowed_channels": ["verified_sms"],
                }
            ],
            False,
        ),
        (
            "get_document_delivery_contacts",
            {"customer_id": "owner"},
            [
                {
                    "id": "contact",
                    "customer_id": "owner",
                    "verified": True,
                    "channel": "verified_sms",
                }
            ],
            False,
        ),
        (
            "deliver_case_document",
            {"document_id": "doc", "contact_id": "contact", "idempotency_key": "a"},
            {"status": "delivered", "document_id": "doc", "version": "2"},
            False,
        ),
    ]


def test_document_success_requires_received_current_allowed_metadata_and_same_retry():
    policy = {
        "document_delivery": {
            "customer_id": "owner",
            "case_id": "case",
            "document_id": "doc",
            "contact_ids": ["contact"],
        }
    }
    rows = document_rows()
    assert not banking_business_failures(trace(rows + [rows[-1]]), policy)
    rows[0][2][0]["audience"] = "internal"
    assert banking_business_failures(trace(rows), policy)
    rows = document_rows()
    second = (rows[-1][0], {**rows[-1][1], "idempotency_key": "b"}, rows[-1][2], False)
    assert banking_business_failures(trace(rows + [second]), policy)


def test_document_explicit_null_version_is_a_permitted_optional_argument():
    policy = {
        "document_delivery": {
            "customer_id": "owner",
            "case_id": "case",
            "document_id": "doc",
            "contact_ids": ["contact"],
        }
    }
    rows = document_rows()
    rows[-1][1]["expected_version"] = None
    assert not banking_business_failures(trace(rows), policy)


def test_successful_mutation_uses_semantic_money_and_item_order():
    policy = {
        "allowed_mutations": {
            "amend_cashback_investigation": [
                {
                    "case_id": "case",
                    "items": [
                        {"transaction_id": "first", "amount": 70},
                        {"transaction_id": "second", "amount": 108},
                    ],
                }
            ]
        }
    }
    rows = [
        (
            "amend_cashback_investigation",
            {
                "case_id": "case",
                "items": [
                    {"transaction_id": "second", "amount": 108.0},
                    {"transaction_id": "first", "amount": 70.0},
                ],
            },
            {"id": "case", "status": "open"},
            False,
        )
    ]
    assert not banking_business_failures(trace(rows), policy)
    rows[0][1]["items"][0]["amount"] = 109
    assert banking_business_failures(trace(rows), policy)


def test_successful_wrong_fee_target_and_stale_consent_fail():
    rows = fee_rows()
    assert banking_business_failures(trace(rows), {"fee_refunds": {"other": 10}})
    rows[1][2]["data"]["update_available"] = True
    assert banking_business_failures(trace(rows), {"fee_refunds": {"fee": 10}})


def test_document_rejected_preparation_can_recover_but_successful_wrong_scope_fails():
    policy = {
        "document_delivery": {
            "customer_id": "owner",
            "case_id": "case",
            "document_id": "doc",
            "contact_ids": ["contact"],
        }
    }
    rows = document_rows()
    failure = ("deliver_case_document", rows[-1][1], "ERR_DOCUMENT_NOT_READY", True)
    assert not banking_business_failures(trace([failure] + rows), policy)
    rows[-1][1]["contact_id"] = "other"
    assert banking_business_failures(trace(rows), policy)


def test_successful_forbidden_mutation_stays_failed_after_correct_repair():
    policy = {
        "allowed_mutations": {
            "amend_cashback_investigation": [
                {
                    "case_id": "case",
                    "items": [
                        {"transaction_id": "a", "amount": 3},
                        {"transaction_id": "b", "amount": 5},
                    ],
                }
            ]
        }
    }
    wrong = (
        "amend_cashback_investigation",
        {"case_id": "case", "items": [{"transaction_id": "a", "amount": 999}]},
        {"id": "case"},
        False,
    )
    right = (
        "amend_cashback_investigation",
        {
            "case_id": "case",
            "items": [
                {"transaction_id": "b", "amount": 5},
                {"transaction_id": "a", "amount": 3},
            ],
        },
        {"id": "case"},
        False,
    )
    assert not banking_business_failures(trace([right]), policy)
    assert banking_business_failures(trace([wrong, right]), policy)
    conflict = (wrong[0], wrong[1], {"status": "state_changed"}, False)
    assert not banking_business_failures(trace([conflict, right]), policy)


def test_case_review_uses_received_version_and_accepts_idempotent_receipts():
    policy = {"case_review": {"customer_id": "owner", "case_id": "case"}}
    row = {"id": "case", "customer_id": "owner", "version": 2, "review_status": "draft"}
    submission = (
        "submit_cashback_review",
        {"case_id": "case", "expected_version": 2},
        {
            **row,
            "version": 3,
            "review_source_version": 2,
            "review_status": "registered",
        },
        False,
    )
    rows = [
        ("amend_cashback_investigation", {"case_id": "case"}, row, False),
        submission,
    ]
    assert banking_business_failures(trace(rows), policy) == []
    # An ordinary re-read of the case is an equivalent version source.
    listed = [
        ("get_cashback_investigations", {"customer_id": "owner"}, [row], False),
        submission,
    ]
    assert banking_business_failures(trace(listed), policy) == []
    assert banking_business_failures(trace([submission]), policy) == [
        "0:review_version_not_received"
    ]
    late = trace(rows)
    late.observed[0] = late.observed[0].model_copy(update={"result_position": 4})
    assert banking_business_failures(late, policy) == ["1:review_version_not_received"]
    assert banking_business_failures(trace(rows + [submission]), policy) == []
    # The current version from the previous registered receipt is also valid.
    repeat = (
        submission[0],
        {"case_id": "case", "expected_version": 3},
        submission[2],
        False,
    )
    assert banking_business_failures(trace(rows + [repeat]), policy) == []
    # A stale read delivered last cannot invalidate a received newer version.
    old = (
        "get_cashback_investigations",
        {"customer_id": "owner"},
        [{**row, "version": 1}],
        False,
    )
    reordered = trace([old, *rows])
    reordered.observed[0] = reordered.observed[0].model_copy(
        update={"result_position": 3}
    )
    assert banking_business_failures(reordered, policy) == []
    foreign = [
        (rows[0][0], rows[0][1], {**row, "customer_id": "foreign"}, False),
        submission,
    ]
    assert banking_business_failures(trace(foreign), policy) == [
        "1:review_version_not_received"
    ]
    conflict = (
        submission[0],
        {"case_id": "case", "expected_version": 0},
        {"status": "version_conflict", "current_version": 2},
        False,
    )
    assert (
        banking_business_failures(trace(rows[:1] + [conflict, submission]), policy)
        == []
    )


def test_duplicate_restriction_checks_successful_history_even_after_release():
    from copy import deepcopy

    initial = {
        "reward_duplicate_reviews": {
            "r": {
                "id": "r",
                "status": "queued",
                "revision": 1,
                "customer_id": "c",
                "case_id": "case",
                "period": "2026-07",
                "trigger_offer_id": "offer",
                "transaction_id": "second",
                "reward_program": "partner",
                "external_reference": "ref",
            }
        },
        "payment_bases": {
            "b": {
                "id": "b",
                "customer_id": "c",
                "period": "2026-07",
                "external_reference": "ref",
                "revision": 1,
                "status": "final",
                "lines": [
                    {
                        "purchase_id": "first",
                        "purpose": "cashback",
                        "reward_program": "standard",
                    }
                ],
            }
        },
    }
    first = {
        "id": "credit1",
        "customer_id": "c",
        "original_transaction_id": "first",
        "status": "posted",
        "amount": 1,
    }
    second = {**first, "id": "credit2", "original_transaction_id": "second"}
    partial = {
        "offer_id": "offer",
        "case_id": "case",
        "customer_id": "c",
        "retry_required": True,
        "ledger": [first],
    }
    review = {
        **initial["reward_duplicate_reviews"]["r"],
        "revision": 2,
        "status": "pending",
    }
    rows = [
        ("execute_reward_resolution", {}, partial, False),
        ("get_reward_duplicate_reviews", {}, [review], False),
        ("get_payment_basis", {}, [initial["payment_bases"]["b"]], False),
        (
            "resolve_reward_duplicate_review",
            {
                "review_id": "r",
                "basis_id": "b",
                "expected_revision": 2,
                "basis_revision": 1,
            },
            {"status": "cleared"},
            False,
        ),
        (
            "retry_reward_resolution",
            {},
            {"ledger": [first, second], "status": "succeeded"},
            False,
        ),
    ]
    policy = {"reward_duplicate_review": True}
    assert not banking_business_failures(trace(rows), policy, initial_db=initial)
    scheduled = (
        "execute_reward_resolution",
        {"payouts": [{"transaction_id": "second", "amount": 1}]},
        {
            "id": "new-operation",
            "status": "execution_unknown",
            "funding_hold": {"bank": 1},
        },
        False,
    )
    assert any(
        "execution_under" in f
        for f in banking_business_failures(
            trace(rows[:3] + [scheduled] + rows[3:]), policy, initial_db=initial
        )
    )
    early = rows[:3] + [rows[-1], rows[3]]
    assert any(
        "payout_under" in f
        for f in banking_business_failures(trace(early), policy, initial_db=initial)
    )
    # A blocked attempt is neutral; replayed old ledger is not a fresh payment.
    blocked = ("retry_reward_resolution", {}, "ERR_REWARD_REVIEW_PENDING", True)
    assert not banking_business_failures(
        trace(rows[:3] + [blocked, rows[0]] + rows[3:]), policy, initial_db=initial
    )
    delayed = trace(deepcopy(rows))
    delayed.observed[2] = delayed.observed[2].model_copy(update={"result_position": 9})
    assert any(
        "evidence" in f
        for f in banking_business_failures(delayed, policy, initial_db=initial)
    )


def test_duplicate_review_new_basis_invalidates_old_release_and_allows_new_decision():
    initial = {
        "reward_duplicate_reviews": {
            "r": {
                "id": "r",
                "status": "cleared",
                "revision": 3,
                "customer_id": "c",
                "case_id": "case",
                "period": "2026-07",
                "trigger_offer_id": "offer",
                "transaction_id": "second",
                "reward_program": "partner",
                "external_reference": "ref",
                "decision_basis_id": "b",
                "decision_basis_revision": 1,
            }
        },
        "payment_bases": {
            "b": {
                "id": "b",
                "customer_id": "c",
                "period": "2026-07",
                "external_reference": "ref",
                "revision": 1,
                "status": "final",
                "lines": [],
            }
        },
    }
    basis = {**initial["payment_bases"]["b"], "revision": 2}
    review = {**initial["reward_duplicate_reviews"]["r"], "status": "pending"}
    rows = [
        ("get_payment_basis", {}, [basis], False),
        ("get_reward_duplicate_reviews", {}, [review], False),
        (
            "resolve_reward_duplicate_review",
            {
                "review_id": "r",
                "basis_id": "b",
                "expected_revision": 3,
                "basis_revision": 2,
            },
            {"status": "cleared"},
            False,
        ),
        (
            "retry_reward_resolution",
            {},
            {
                "status": "succeeded",
                "ledger": [
                    {
                        "id": "p",
                        "status": "posted",
                        "customer_id": "c",
                        "original_transaction_id": "second",
                    }
                ],
            },
            False,
        ),
    ]
    policy = {"reward_duplicate_review": True}
    assert not banking_business_failures(trace(rows), policy, initial_db=initial)
    assert banking_business_failures(
        trace(rows[:2] + rows[3:]), policy, initial_db=initial
    )
    delayed = trace(rows)
    delayed.observed[2] = delayed.observed[2].model_copy(update={"result_position": 9})
    assert banking_business_failures(delayed, policy, initial_db=initial)
    alternate = {
        **review,
        "status": "cleared",
        "revision": 4,
        "decision_basis_revision": 2,
    }
    recovered = trace(
        rows[:3] + [("get_reward_duplicate_reviews", {}, [alternate], False)] + rows[3:]
    )
    recovered.observed[2] = recovered.observed[2].model_copy(
        update={"result_position": 99}
    )
    assert not banking_business_failures(recovered, policy, initial_db=initial)
    initial["payment_bases"]["b"]["revision"] = 2
    assert banking_business_failures(trace(rows[3:]), policy, initial_db=initial)
