"""Real banking calls: late posting, optimistic state and unknown executions."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.data_model.message import ToolCall
from tests.utils.banking_settlement_fixture import ready

C, A, B = "gromov_a_1187", "txn_552290", "txn_552291"
PERIOD, CASE = "2026-07", "case-standard-july"
KEY = C + ":" + PERIOD


def call(env, name, **args):
    r = env.get_response(ToolCall(id=name, name=name, arguments=args))
    assert not r.error, r
    return json.loads(r.content)


def pay(env, amount=12, key="request-1", revision=2):
    return call(
        env,
        "execute_cashback_adjustment",
        permission_id="adjustment-A",
        transaction_id=A,
        amount=amount,
        idempotency_key=key,
        expected_revision=revision,
    )


def refresh(env):
    return call(env, "refresh_cashback_settlement", customer_id=C, period=PERIOD)


def test_pending_is_not_paid_and_refresh_posts_exactly_once():
    e = ready()
    before = e.tools.db.accounts["acc_1187"].balance
    rules = call(e, "get_cashback_rules", customer_id=C)
    assert rules["cycles"][0]["settlements"][0]["status"] == "pending"
    assert "settlement-B7" not in e.tools.db.transactions
    assert pay(e, revision=1)["status"] == "settlement_pending"
    assert refresh(e)["current_revision"] == 2
    assert refresh(e)["changed_transaction_ids"] == []
    assert e.tools.db.accounts["acc_1187"].balance == before + 7


def test_unknown_execution_status_and_identical_retry_do_not_pay_twice():
    e = ready()
    refresh(e)
    r = pay(e)
    assert r["status"] == "execution_unknown"
    status = call(e, "get_cashback_adjustment_status", operation_id=r["operation_id"])
    assert status["status"] == "succeeded" and status["granted"] == 12
    assert status["transaction_id"] == A and status["state_revision"] == 3
    assert (
        pay(e)["status"] == "succeeded"
    )  # lookup precedes consumed authority/revision
    assert (
        e.tools.db.cashback_adjustment_permissions["adjustment-A"]["used_amount"] == 12
    )


def test_stale_revision_is_recoverable_but_authority_violation_is_not_hidden():
    e = ready()
    refresh(e)
    assert pay(e, revision=1) == {
        "status": "state_changed",
        "customer_id": C,
        "period": PERIOD,
        "expected_revision": 1,
        "current_revision": 2,
    }
    r = e.get_response(
        ToolCall(
            id="bad",
            name="execute_cashback_adjustment",
            arguments={
                "permission_id": "adjustment-A",
                "transaction_id": A,
                "amount": 13,
                "idempotency_key": "bad",
                "expected_revision": 1,
            },
        )
    )
    assert r.error and "NOT_AUTHORIZED" in r.content
    assert pay(e)["status"] == "execution_unknown"


def test_reservation_first_then_split_execution_closes_reservations():
    e = ready()
    refresh(e)
    r = call(
        e,
        "amend_cashback_investigation",
        case_id=CASE,
        items=[
            {"transaction_id": A, "amount": 18},
            {"transaction_id": B, "amount": 13},
        ],
        reserved_adjustments=[
            {"permission_id": "adjustment-A", "transaction_id": A, "amount": 12}
        ],
        expected_revision=2,
    )
    assert r["state_revision"] == 3
    first = pay(e, amount=6, revision=3)
    assert first["status"] == "execution_unknown"
    call(e, "get_cashback_adjustment_status", operation_id=first["operation_id"])
    assert pay(e, amount=6, key="request-2", revision=4)["status"] == "succeeded"
    assert not e.tools.db.cases[CASE].adjustment_reservations
    assert e.tools.db.cases[CASE].amount == 31


def test_same_key_different_parameters_is_rejected():
    e = ready(late=False)
    pay(e, amount=6, revision=1)
    r = e.get_response(
        ToolCall(
            id="bad",
            name="execute_cashback_adjustment",
            arguments={
                "permission_id": "adjustment-A",
                "transaction_id": A,
                "amount": 5,
                "idempotency_key": "request-1",
                "expected_revision": 2,
            },
        )
    )
    assert r.error and "Ключ другого запроса" in r.content


def test_foreign_operation_and_new_key_after_consumption_are_rejected():
    e = ready(late=False)
    r = pay(e, revision=1)
    e.tools.db.cashback_adjustment_operations[r["operation_id"]]["customer_id"] = (
        "foreign"
    )
    res = e.get_response(
        ToolCall(
            id="foreign",
            name="get_cashback_adjustment_status",
            arguments={"operation_id": r["operation_id"]},
        )
    )
    assert res.error
    res = e.get_response(
        ToolCall(
            id="new",
            name="execute_cashback_adjustment",
            arguments={
                "permission_id": "adjustment-A",
                "transaction_id": A,
                "amount": 12,
                "idempotency_key": "new-key",
                "expected_revision": 2,
            },
        )
    )
    assert res.error


def funding_ready(*, accepted=True, late=True):
    """A local API example with a funding decision tied to one own statement."""
    from tau2.domains.banking_ru.reward_resolution_models import RewardResolutionOffer

    e = ready(late=late)
    db = e.tools.db
    db.transactions[B].reward_discrepancy_type = "partner_activation_review"
    db.reward_resolution_offers["funding-offer"] = RewardResolutionOffer(
        id="funding-offer",
        customer_id=C,
        case_id=CASE,
        period=PERIOD,
        transaction_ids=[A, B],
        version=1,
        amount=40,
        amount_basis="incremental",
        status="approved",
        rights="preserve",
        terms_id="rights",
        allocation_terms_id="amounts",
        allocation_first_transaction_id=A,
        allocation_first_amount=15,
        valid_from="2026-01-01",
        valid_to="2026-12-31",
        selection_budget={"id": "funding-budget", "bank": 20, "partner": 20},
    )
    db.reward_statement_registrations["funding-statement"] = dict(
        id="funding-statement",
        customer_id=C,
        case_id=CASE,
        transaction_ids=[B],
        status="accepted" if accepted else "registered",
    )
    db.reward_resolution_offer_updates["funding-event"] = dict(
        id="funding-event",
        customer_id=C,
        period=PERIOD,
        offer_id="funding-offer",
        status="pending",
        statement_id="funding-statement",
        previous_version=1,
        new_version=2,
        new_amount=45,
        new_selection_budget={"id": "funding-budget", "bank": 23, "partner": 20},
    )
    return e


def test_accepted_statement_publishes_versioned_funding_once_with_settlement():
    e = funding_ready()
    result = refresh(e)
    offer = e.tools.db.reward_resolution_offers["funding-offer"]
    assert (offer.version, offer.amount, offer.selection_budget.bank) == (2, 45, 23)
    assert result["changed_offer_ids"] == [offer.id]
    assert result["funding_decisions"] == [
        {
            "event_id": "funding-event",
            "offer_id": "funding-offer",
            "statement_id": "funding-statement",
            "previous_version": 1,
            "new_version": 2,
        }
    ]
    assert result["current_revision"] == 2
    assert (
        e.tools.db.reward_resolution_offer_updates["funding-event"]["status"]
        == "applied"
    )
    assert refresh(e)["changed_offer_ids"] == []
    assert e.tools.db.cashback_state_revisions[KEY] == 2


def test_unaccepted_funding_statement_cannot_publish_conditions_or_credits():
    e = funding_ready(accepted=False)
    before = deepcopy(e.tools.db.model_dump())
    response = e.get_response(
        ToolCall(
            id="unaccepted",
            name="refresh_cashback_settlement",
            arguments={"customer_id": C, "period": PERIOD},
        )
    )
    assert response.error and "STATEMENT_NOT_ACCEPTED" in response.content
    assert e.tools.db.model_dump() == before


@pytest.mark.parametrize(
    "bad",
    [
        {"statement_id": "missing"},
        {"previous_version": 2},
        {"new_version": 1},
        {"new_amount": -1},
        {"new_selection_budget": None},
        {"new_selection_budget": {"id": "budget", "bank": -1, "partner": 20}},
    ],
)
def test_invalid_funding_event_does_not_partially_post(bad):
    e = funding_ready()
    e.tools.db.reward_resolution_offer_updates["funding-event"].update(bad)
    before = deepcopy(e.tools.db.model_dump())
    response = e.get_response(
        ToolCall(
            id="invalid",
            name="refresh_cashback_settlement",
            arguments={"customer_id": C, "period": PERIOD},
        )
    )
    assert response.error
    assert e.tools.db.model_dump() == before


@pytest.mark.parametrize(
    "field,value",
    [("customer_id", "foreign"), ("case_id", "foreign-case"), ("transaction_ids", [A])],
)
def test_funding_event_is_bound_to_the_accepted_statement_scope(field, value):
    e = funding_ready()
    # A separate correct registration satisfies the ordinary prerequisite; the
    # event itself must still reject a foreign or mismatched selected statement.
    e.tools.db.reward_statement_registrations["other"] = dict(
        id="other", customer_id=C, case_id=CASE, transaction_ids=[B], status="accepted"
    )
    e.tools.db.reward_statement_registrations["funding-statement"][field] = value
    before = deepcopy(e.tools.db.model_dump())
    response = e.get_response(
        ToolCall(
            id="scope",
            name="refresh_cashback_settlement",
            arguments={"customer_id": C, "period": PERIOD},
        )
    )
    assert response.error
    assert e.tools.db.model_dump() == before


def test_preliminary_and_changed_offer_are_recoverable_without_payment():
    from tau2.domains.banking_ru.reward_resolution import execute, list_offers

    e = funding_ready(late=False)
    args = dict(
        offer_id="funding-offer",
        offer_version=1,
        payouts=[
            {"transaction_id": A, "amount": 15},
            {"transaction_id": B, "amount": 20},
        ],
        total_amount=35,
        case_id=CASE,
        confirmation_id="old",
        expected_revision=1,
        idempotency_key="funding-request",
    )
    assert list_offers(e.tools, C, CASE)[0]["funding_status"] == "pending"
    before = e.tools.db.accounts["acc_1187"].balance
    assert execute(e.tools, **args)["status"] == "funding_pending"
    refresh(e)
    assert list_offers(e.tools, C, CASE)[0]["funding_status"] == "final"
    assert execute(e.tools, **args)["status"] == "offer_changed"
    assert not e.tools._resolution_violations
    assert not e.tools.db.reward_resolution_executions
    assert e.tools.db.accounts["acc_1187"].balance == before


def test_duplicate_offer_decisions_are_atomic_and_legacy_approval_is_preserved():
    e = funding_ready()
    e.tools.db.reward_resolution_offer_updates["duplicate"] = deepcopy(
        e.tools.db.reward_resolution_offer_updates["funding-event"]
    )
    before = deepcopy(e.tools.db.model_dump())
    result = e.get_response(
        ToolCall(
            id="duplicate",
            name="refresh_cashback_settlement",
            arguments={"customer_id": C, "period": PERIOD},
        )
    )
    assert result.error
    assert e.tools.db.model_dump() == before
    del e.tools.db.reward_resolution_offer_updates["duplicate"]
    del e.tools.db.reward_resolution_offer_updates["funding-event"]
    e.tools.db.reward_resolution_offers["funding-offer"].status = "under_review"
    e.tools.db.reward_resolution_offer_updates["legacy"] = dict(
        id="legacy",
        offer_id="funding-offer",
        customer_id=C,
        period=PERIOD,
        status="pending",
    )
    refresh(e)
    assert e.tools.db.reward_resolution_offers["funding-offer"].status == "approved"
    assert e.tools.db.reward_resolution_offers["funding-offer"].version == 1


RA, RB, RCASE = "txn_804219", "txn_804227", "case_610903"


def resolution_claim_ready():
    from tau2.domains.banking_ru.data_model import BankingDB
    from tau2.domains.banking_ru.file_corpus import FileCorpus
    from tau2.domains.banking_ru.file_pilot import get_file_environment
    from tau2.domains.banking_ru.reward_resolution_models import RewardResolutionOffer
    from tau2.domains.banking_ru.reward_workflows import standard_gaps

    path = Path(__file__).resolve().parents[2] / "fixtures/banking_ru/reward_api"
    e = get_file_environment(
        path,
        "banking_ru",
        structured_identity=True,
        snapshot=(
            FileCorpus(path),
            BankingDB.model_validate_json((path / "db.json").read_text()),
        ),
    )
    db = e.tools.db
    db.session.verified_customers.append(C)
    db.reward_statement_registrations["accepted"] = dict(
        id="accepted",
        customer_id=C,
        case_id=RCASE,
        transaction_ids=[RA, RB],
        status="accepted",
    )
    refresh(e)
    db.reward_resolution_offers["claim-offer"] = RewardResolutionOffer(
        id="claim-offer",
        customer_id=C,
        case_id=RCASE,
        period=PERIOD,
        transaction_ids=[RA, RB],
        version=1,
        amount=19,
        amount_basis="incremental",
        status="approved",
        rights="preserve",
        terms_id="rights",
        allocation_terms_id="amounts",
        allocation_first_transaction_id=RA,
        allocation_first_amount=18,
        valid_from="2026-01-01",
        valid_to="2026-12-31",
    )
    assert standard_gaps(db, C, PERIOD)[RA] == 2500
    packet = dict(
        type="reward_resolution_confirmation",
        offer_id="claim-offer",
        offer_version=1,
        payouts=[dict(transaction_id=RA, amount=18), dict(transaction_id=RB, amount=1)],
        total_amount=19,
        case_id=RCASE,
        resulting_claims=[
            dict(transaction_id=RA, amount=7),
            dict(transaction_id=RB, amount=52),
            dict(transaction_id="txn_804205", amount=7),
        ],
        state_revision=2,
    )
    consent = call(e, "ask_client", customer_id=C, request=packet)
    arguments = {
        k: v
        for k, v in packet.items()
        if k not in {"type", "resulting_claims", "state_revision"}
    }
    arguments.update(
        confirmation_id=consent["confirmation_id"],
        expected_revision=2,
        idempotency_key="settle-existing-claim",
    )
    return e, arguments


def test_resolution_payment_settles_existing_own_claim_before_residual_amendment():
    e, args = resolution_claim_ready()
    assert e.tools.db.cases[RCASE].reward_items[0].amount == 10
    paid = call(e, "execute_reward_resolution", **args)
    assert paid["status"] == "succeeded"
    assert e.tools.db.cases[RCASE].reward_items[0].amount == 10
    result = call(
        e,
        "amend_cashback_investigation",
        case_id=RCASE,
        items=[dict(transaction_id=RA, amount=7), dict(transaction_id=RB, amount=52)],
        expected_revision=3,
    )
    assert result["amount"] == 66
    assert {x["transaction_id"]: x["amount"] for x in result["reward_items"]} == {
        RA: 7,
        RB: 52,
        "txn_804205": 7,
    }


def test_claim_cannot_shrink_before_actual_resolution_payment():
    e, _ = resolution_claim_ready()
    result = e.get_response(
        ToolCall(
            id="early-shrink",
            name="amend_cashback_investigation",
            arguments=dict(
                case_id=RCASE,
                items=[dict(transaction_id=RA, amount=7)],
                expected_revision=2,
            ),
        )
    )
    assert result.error
    assert e.tools.db.cases[RCASE].reward_items[0].amount == 10


def test_resolution_cannot_consume_a_claim_reserved_in_another_case():
    e, args = resolution_claim_ready()
    other = e.tools.db.cases[RCASE].model_copy(deep=True)
    other.id = "other-case"
    e.tools.db.cases[other.id] = other
    result = e.get_response(
        ToolCall(id="other-reserve", name="execute_reward_resolution", arguments=args)
    )
    assert result.error
    assert not e.tools.db.reward_resolution_executions
    assert "reward-resolution:" + RCASE + ":" + RA not in e.tools.db.transactions


@pytest.mark.parametrize(
    "proof", ["missing_operation", "other_case", "wrong_remaining"]
)
def test_claim_shrink_requires_matching_successful_resolution_and_exact_remaining(
    proof,
):
    e, args = resolution_claim_ready()
    call(e, "execute_reward_resolution", **args)
    if proof == "missing_operation":
        e.tools.db.reward_resolution_executions.clear()
    elif proof == "other_case":
        next(
            iter(e.tools.db.reward_resolution_executions.values())
        ).case_id = "other-case"
    result = e.get_response(
        ToolCall(
            id="invalid-shrink",
            name="amend_cashback_investigation",
            arguments=dict(
                case_id=RCASE,
                items=[
                    dict(
                        transaction_id=RA, amount=6 if proof == "wrong_remaining" else 7
                    )
                ],
                expected_revision=3,
            ),
        )
    )
    assert result.error
    assert e.tools.db.cases[RCASE].reward_items[0].amount == 10
