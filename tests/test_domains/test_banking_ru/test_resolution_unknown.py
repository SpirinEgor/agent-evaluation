"""Validated unknown executions retain holds until an authoritative outcome."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.reward_funding import reserve_snapshot
from tau2.domains.banking_ru.reward_resolution import execute
from tau2.environment.tool import ToolExecutionRejected
from tests.test_domains.test_banking_ru.test_resolution_funding_pools import (
    shared_pay,
    shared_ready,
)


def ready(outcome="failed_final"):
    e = shared_ready()
    e.tools.db.reward_resolution_execution_profiles = {"X": outcome}
    return e


def status(e, operation_id):
    return e.tools.get_reward_resolution_status(operation_id).root


def replay(e, key="X"):
    op = next(
        o
        for o in e.tools.db.reward_resolution_executions.values()
        if o.idempotency_key == key
    )
    return execute(e.tools, **op.packet, expected_revision=0, idempotency_key=key)


@pytest.mark.parametrize("outcome", ["failed_final", "succeeded"])
@pytest.mark.parametrize("resolve", ["status", "replay"])
def test_unknown_hold_resolves_once_and_replays_final(outcome, resolve):
    e = ready(outcome)
    initial = deepcopy(e.tools.db.transactions)
    start_hash = e.tools.get_db_hash()
    result = shared_pay(e, "X")
    assert result["status"] == "execution_unknown"
    assert result["operation_id"] == result["id"]
    assert result["funding_hold"] == {
        "reserve_id": "shared-reserve",
        "bank": 9,
        "partner": 9,
    }
    assert e.tools.db.transactions == initial
    assert e.tools.get_db_hash() != start_hash
    reserve = reserve_snapshot(e.tools.db, e.tools.db.reward_resolution_offers["Y"])
    assert reserve["remaining_bank"] == 14
    assert reserve["held_bank"] == reserve["held_partner"] == 9
    final = status(e, result["id"]) if resolve == "status" else replay(e)
    assert final["status"] == outcome
    assert final["state_revision"] == result["state_revision"] + 1
    assert len(final["ledger"]) == (1 if outcome == "succeeded" else 0)
    assert (
        final["funding_charge" if outcome == "succeeded" else "released_funding"]
        == result["funding_hold"]
    )
    before = e.tools.db.model_dump()
    assert replay(e) == final
    assert status(e, result["id"]) == final
    assert e.tools.db.model_dump() == before
    assert not e.tools._resolution_violations


@pytest.mark.parametrize("outcome", ["failed_final", "succeeded"])
@pytest.mark.parametrize("order", [("X", "Y"), ("Y", "X")])
def test_independent_payment_can_precede_or_follow_pending_hold(outcome, order):
    e = ready(outcome)
    results = {name: shared_pay(e, name) for name in order}
    assert results["Y"]["status"] == "succeeded"
    assert results["X"]["status"] == "execution_unknown"
    assert replay(e)["status"] == outcome
    reserve = reserve_snapshot(e.tools.db, e.tools.db.reward_resolution_offers["Y"])
    assert reserve["held_bank"] == 0
    assert reserve["remaining_bank"] == (1 if outcome == "succeeded" else 10)


@pytest.mark.parametrize("outcome", [None, "failed_final", "succeeded"])
def test_new_key_retry_is_neutral_and_atomic(outcome):
    e = ready(outcome or "failed_final")
    result = shared_pay(e, "X")
    if outcome:
        status(e, result["id"])
    before = e.tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected):
        shared_pay(e, "X", key="new")
    assert e.tools.db.model_dump() == before
    assert not e.tools._resolution_violations


def fallback_ready(outcome="failed_final"):
    e = ready(outcome)
    original = e.tools.db.reward_resolution_offers["X"]
    fallback = original.model_copy(
        deep=True,
        update={
            "id": "R",
            "fallback_for_offer_id": "X",
            "amount": 17,
            "allocation_first_amount": 17,
        },
    )
    fallback.components[0].id = "R"
    fallback.components[0].amount = 17
    fallback.components[0].allocation_first_amount = 17
    fallback.components[0].bank_cost = 8
    e.tools.db.reward_resolution_offers["R"] = fallback
    return e


def fallback_packet(e, operation_id=None):
    from tau2.domains.banking_ru.cashback_state import revision
    from tau2.domains.banking_ru.reward_workflows import money, standard_gaps
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import (
        PERIOD,
        RCASE,
        C,
    )

    offer = e.tools.db.reward_resolution_offers["R"]
    gaps = standard_gaps(e.tools.db, C, PERIOD)
    claims = {x.transaction_id: x.amount for x in e.tools.db.cases[RCASE].reward_items}
    for member in e.tools.db.reward_resolution_offers.values():
        for tid in member.transaction_ids:
            claims[tid] = money(gaps[tid])
    tid = offer.transaction_ids[0]
    claims[tid] = max(1, claims[tid] - 17)
    return dict(
        type="reward_resolution_confirmation",
        offer_id="R",
        offer_version=offer.version,
        fallback_operation_id=operation_id,
        case_id=RCASE,
        total_amount=17,
        payouts=[dict(transaction_id=tid, amount=17)],
        component_ids=["R"],
        resulting_claims=[dict(transaction_id=t, amount=a) for t, a in claims.items()],
        state_revision=revision(e.tools.db, C, PERIOD),
    )


def confirm_and_execute_fallback(e, operation_id):
    from tau2.domains.banking_ru.client_requests import (
        RewardResolutionConfirmationRequest,
    )
    from tau2.domains.banking_ru.reward_resolution import confirm

    request = RewardResolutionConfirmationRequest(**fallback_packet(e, operation_id))
    customer = e.tools.db.reward_resolution_offers["R"].customer_id
    consent = confirm(e.tools, customer, request)
    packet = {
        k: v
        for k, v in consent["data"].items()
        if k not in {"resulting_claims", "state_revision"}
    }
    return execute(
        e.tools,
        **packet,
        expected_revision=request.state_revision,
        confirmation_id=consent["confirmation_id"],
        idempotency_key="R",
    )


def test_fallback_denial_recovers_after_final_release_and_new_consent():
    e = fallback_ready()
    pending = shared_pay(e, "X")
    before = e.tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected, match="ERR_FALLBACK_NOT_AVAILABLE"):
        confirm_and_execute_fallback(e, pending["id"])
    assert e.tools.db.model_dump() == before
    assert not e.tools._resolution_violations
    status(e, pending["id"])
    assert confirm_and_execute_fallback(e, pending["id"])["status"] == "succeeded"
    assert len(e.tools.db.reward_resolution_executions) == 2
    reserve = reserve_snapshot(e.tools.db, e.tools.db.reward_resolution_offers["R"])
    assert reserve["remaining_bank"] == 15
    assert {row["status"] for row in reserve["operations"]} == {
        "failed_final",
        "succeeded",
    }


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "unknown",
        "customer",
        "case",
        "period",
        "scope",
        "offer",
        "success",
        "hold",
    ],
)
def test_fallback_rejects_wrong_predecessor_without_effects(defect):
    e = fallback_ready("succeeded" if defect == "success" else "failed_final")
    pending = shared_pay(e, "X")
    status(e, pending["id"])
    op = e.tools.db.reward_resolution_executions[pending["id"]]
    reference = pending["id"]
    if defect == "missing":
        reference = None
    if defect == "unknown":
        reference = "unknown"
    if defect in {"customer", "case", "period"}:
        setattr(
            op,
            {"customer": "customer_id", "case": "case_id", "period": "period"}[defect],
            "other",
        )
    if defect == "scope":
        op.transaction_ids = ["other"]
    if defect == "offer":
        op.packet["offer_id"] = "other"
    if defect == "hold":
        op.result["funding_hold"] = op.result["released_funding"]
    before = e.tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected, match="ERR_FALLBACK_NOT_AVAILABLE"):
        confirm_and_execute_fallback(e, reference)
    assert e.tools.db.model_dump() == before
    assert not e.tools._resolution_violations


def test_fallback_execution_cannot_reuse_consent_without_its_predecessor_proof():
    e = fallback_ready()
    pending = shared_pay(e, "X")
    status(e, pending["id"])
    request = fallback_packet(e, pending["id"])
    e.tools._resolution_consents["old"] = {
        k: v for k, v in request.items() if k not in {"type", "fallback_operation_id"}
    }
    args = {
        k: v
        for k, v in request.items()
        if k not in {"type", "resulting_claims", "state_revision"}
    }
    before = e.tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected, match="ERR_FALLBACK_NOT_AVAILABLE"):
        execute(
            e.tools,
            **args,
            confirmation_id="old",
            expected_revision=request["state_revision"],
            idempotency_key="R",
        )
    assert e.tools.db.model_dump() == before
    assert not e.tools._resolution_violations


def test_status_does_not_resolve_unverified_owner():
    e = ready()
    pending = shared_pay(e, "X")
    e.tools.db.session.verified_customers = []
    before = e.tools.db.model_dump()
    with pytest.raises(ValueError):
        status(e, pending["id"])
    assert e.tools.db.model_dump() == before


def test_conflicting_replay_is_neutral_before_or_after_settlement():
    e = ready()
    shared_pay(e, "X")
    op = next(iter(e.tools.db.reward_resolution_executions.values()))
    for settle in [False, True]:
        if settle:
            replay(e)
        before = e.tools.db.model_dump()
        args = {**op.packet, "total_amount": 17}
        with pytest.raises(ToolExecutionRejected, match="ERR_IDEMPOTENCY_CONFLICT"):
            execute(e.tools, **args, expected_revision=0, idempotency_key="X")
        assert e.tools.db.model_dump() == before
        assert not e.tools._resolution_violations


def test_review_handoff_cannot_overtake_a_pending_payment_and_recovers():
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import RCASE

    e = ready()
    pending = shared_pay(e, "X")
    case = e.tools.db.cases[RCASE]
    before = e.tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected, match="ERR_RESOLUTION_PENDING"):
        e.tools.submit_cashback_review(RCASE, case.version)
    assert e.tools.db.model_dump() == before
    assert not e.tools._resolution_violations
    status(e, pending["id"])
    assert (
        e.tools.submit_cashback_review(RCASE, case.version).review_status
        == "registered"
    )


@pytest.mark.parametrize("cost", [0, 9])
def test_pending_shared_operation_blocks_funding_replacement_even_zero_cost(cost):
    from tau2.domains.banking_ru.cashback_state import refresh
    from tau2.domains.banking_ru.reward_resolution_models import (
        RewardResolutionExecution,
    )
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import (
        A,
        funding_ready,
    )

    e = funding_ready()
    db = e.tools.db
    offer = db.reward_resolution_offers["funding-offer"]
    offer.selection_budget.shared = True
    db.reward_resolution_offer_updates["funding-event"]["new_selection_budget"][
        "shared"
    ] = True
    sibling = offer.model_copy(
        deep=True, update={"id": "sibling", "transaction_ids": [A]}
    )
    db.reward_resolution_offers[sibling.id] = sibling
    db.reward_resolution_executions["pending"] = RewardResolutionExecution(
        id="pending",
        customer_id=offer.customer_id,
        case_id=offer.case_id,
        period=offer.period,
        transaction_ids=[A],
        idempotency_key="pending",
        packet={"offer_id": "sibling"},
        result={
            "status": "execution_unknown",
            "funding_hold": {
                "reserve_id": offer.selection_budget.id,
                "bank": cost,
                "partner": cost,
            },
        },
    )
    before = deepcopy(db.model_dump())
    with pytest.raises(ValueError, match="ERR_INVALID_OFFER_DECISION"):
        refresh(e.tools, offer.customer_id, offer.period)
    assert db.model_dump() == before


def test_status_public_tool_preserves_structured_final_funding_and_numeric_revision():
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import call

    e = ready()
    pending = shared_pay(e, "X")
    result = call(
        e, "get_reward_resolution_status", resolution_operation_id=pending["id"]
    )
    assert result["status"] == "failed_final"
    assert result["released_funding"]["bank"] == 9
    assert isinstance(result["state_revision"], int)


def test_profiled_execution_validates_consent_before_any_hold_or_revision():
    e = ready()
    shared_pay(e, "X")
    packet = deepcopy(
        next(iter(e.tools.db.reward_resolution_executions.values())).packet
    )
    fresh = ready()
    before = fresh.tools.db.model_dump()
    with pytest.raises(ValueError, match="Нет согласия"):
        execute(fresh.tools, **packet, expected_revision=2, idempotency_key="X")
    assert fresh.tools.db.model_dump() == before


def test_pending_projection_is_request_identity_independent_and_release_removes_it():
    e = ready()
    initial_hash = e.tools.get_db_hash()
    pending = shared_pay(e, "X")
    pending_hash = e.tools.get_db_hash()
    assert initial_hash != pending_hash
    operation = e.tools.db.reward_resolution_executions.pop(pending["id"])
    operation.id = "other-generated-id"
    operation.idempotency_key = "other-request-key"
    operation.result["id"] = operation.result["operation_id"] = operation.id
    e.tools.db.reward_resolution_executions[operation.id] = operation
    assert e.tools.get_db_hash() == pending_hash
    status(e, operation.id)
    assert e.tools.get_db_hash() == initial_hash


def test_status_read_replay_materializes_final_release_before_fallback_consent():
    from tau2.data_model.message import AssistantMessage, ToolCall

    live = fallback_ready()
    pending = shared_pay(live, "X")
    pending_db = deepcopy(live.tools.db)
    call = ToolCall(
        id="resolve",
        name="get_reward_resolution_status",
        arguments={"resolution_operation_id": pending["id"]},
    )
    response = live.get_response(call)
    assert not response.error
    restored = fallback_ready()
    restored.tools.db = pending_db
    restored.set_state(
        initialization_data=None,
        initialization_actions=None,
        message_history=[
            AssistantMessage(role="assistant", tool_calls=[call]),
            response,
        ],
    )
    operation = restored.tools.db.reward_resolution_executions[pending["id"]]
    assert operation.result["status"] == "failed_final"
    assert restored._is_mutating_tool("get_reward_resolution_status")
    assert (
        confirm_and_execute_fallback(restored, pending["id"])["status"] == "succeeded"
    )
