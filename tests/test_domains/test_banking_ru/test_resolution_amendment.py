"""Partial agreements reconcile postings and bind new allocations to fresh consent."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.cashback_state import revision
from tau2.domains.banking_ru.reward_resolution import execute, retry
from tau2.domains.banking_ru.reward_resolution_amendment import (
    amend,
    funding_after_amendment,
    validated_amendment,
)
from tau2.domains.banking_ru.reward_resolution_models import (
    RewardResolutionBudget,
    RewardResolutionComponent,
    RewardResolutionCorrection,
)
from tau2.environment.tool import ToolExecutionRejected
from tests.test_domains.test_banking_ru.test_cashback_settlement_state import (
    PERIOD,
    RA,
    RB,
    RCASE,
    C,
    call,
    resolution_claim_ready,
)


def prepared(first=18):
    env, args = resolution_claim_ready()
    db = env.tools.db
    offer = db.reward_resolution_offers["claim-offer"]
    offer.amount = 65
    offer.selection_budget = RewardResolutionBudget(
        id="amendment-reserve", bank=35, partner=30, shared=True
    )
    offer.components = [
        RewardResolutionComponent(
            id="part",
            exclusive_group="one",
            amount=65,
            bank_cost=35,
            partner_cost=30,
            allocation_first_amount=first,
        )
    ]
    db.reward_resolution_partial_profiles[offer.id] = RB
    db.reward_resolution_correction_profiles[offer.id] = RewardResolutionCorrection(
        transaction_id=RB, amount=10, ledger_id="bank-correction"
    )
    packet = dict(
        type="reward_resolution_confirmation",
        offer_id=offer.id,
        offer_version=1,
        case_id=RCASE,
        component_ids=["part"],
        total_amount=65,
        payouts=[
            dict(transaction_id=RA, amount=first),
            dict(transaction_id=RB, amount=65 - first),
        ],
        resulting_claims=[
            dict(transaction_id=RA, amount=25 - first),
            dict(transaction_id=RB, amount=53 - (65 - first)),
            dict(transaction_id="txn_804205", amount=7),
        ],
        state_revision=2,
    )
    consent = call(env, "ask_client", customer_id=C, request=packet)
    args.update(
        {
            k: v
            for k, v in packet.items()
            if k not in {"type", "resulting_claims", "state_revision"}
        },
        confirmation_id=consent["confirmation_id"],
    )
    return env, args


def pending(first=18):
    env, args = prepared(first)
    return env, execute(env.tools, **args), args


def amendment_packet(env, operation, first=18):
    return dict(
        type="reward_resolution_confirmation",
        resolution_operation_id=operation["id"],
        offer_id="claim-offer",
        offer_version=1,
        case_id=RCASE,
        component_ids=["part"],
        total_amount=63 - first,
        payouts=[
            dict(transaction_id=RA, amount=25 - first),
            dict(transaction_id=RB, amount=38),
        ],
        resulting_claims=[
            dict(transaction_id=RB, amount=5),
            dict(transaction_id="txn_804205", amount=7),
        ],
        state_revision=revision(env.tools.db, C, PERIOD),
    )


def amended_request(env, operation, first=18):
    packet = amendment_packet(env, operation, first)
    consent = call(env, "ask_client", customer_id=C, request=packet)
    return dict(
        resolution_operation_id=operation["id"],
        payouts=packet["payouts"],
        total_amount=packet["total_amount"],
        confirmation_id=consent["confirmation_id"],
        expected_revision=packet["state_revision"],
    )


def test_corrected_pending_agreement_rejects_old_retry_then_replans_once():
    env, result, original = pending()
    before = env.tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected, match="RECONCILIATION_REQUIRED"):
        retry(env.tools, result["id"])
    assert env.tools.db.model_dump() == before
    assert execute(env.tools, **original) == result
    args = amended_request(env, result)
    amended = amend(env.tools, **args)
    assert amended["agreement_total_amount"] == 63
    assert amended["funding_hold"] == {
        "reserve_id": "amendment-reserve",
        "bank": 33,
        "partner": 30,
    }
    assert amend(env.tools, **args) == amended
    final = retry(env.tools, result["id"])
    assert final["total_amount"] == 63
    assert len(final["ledger"]) == 3
    assert not final.get("funding_hold")
    assert validated_amendment(
        env.tools.db, env.tools.db.reward_resolution_executions[result["id"]]
    )
    before = env.tools.db.model_dump()
    assert retry(env.tools, result["id"]) == final
    assert amend(env.tools, **args) == final
    assert execute(env.tools, **original) == final
    assert env.tools.db.model_dump() == before


@pytest.mark.parametrize(
    "defect", ["consent", "revision", "scope", "amount", "claims", "original_cap"]
)
def test_invalid_amendment_is_atomic_and_recoverable(defect):
    env, result, _ = pending()
    args = amended_request(env, result)
    if defect == "consent":
        args["confirmation_id"] = "unknown"
    elif defect == "revision":
        args["expected_revision"] -= 1
    else:
        consent = env.tools._resolution_consents[args["confirmation_id"]]
        if defect == "scope":
            args["payouts"][0]["transaction_id"] = "txn_804205"
        if defect == "amount":
            args["payouts"][1]["amount"] = 48
            args["total_amount"] += 10
        if defect == "original_cap":
            args["payouts"][1]["amount"] = 43
            args["total_amount"] += 5
        consent.update(
            payouts=deepcopy(args["payouts"]), total_amount=args["total_amount"]
        )
        if defect == "claims":
            consent["resulting_claims"][0]["amount"] = 99
    before = env.tools.db.model_dump()
    if defect == "revision":
        assert amend(env.tools, **args)["status"] == "state_changed"
    else:
        with pytest.raises(ToolExecutionRejected):
            amend(env.tools, **args)
    assert env.tools.db.model_dump() == before


def test_alternative_initial_allocation_has_same_valid_final_db():
    hashes = []
    for first in (18, 20):
        env, result, _ = pending(first)
        amend(env.tools, **amended_request(env, result, first))
        retry(env.tools, result["id"])
        snapshot = deepcopy(env.tools.db.model_dump())
        op = env.tools.db.reward_resolution_executions[result["id"]]
        assert validated_amendment(env.tools.db, op)
        hashes.append((env, snapshot))
    # Compare normalized ledger; offer inputs themselves intentionally remain distinct.
    from tau2.domains.banking_ru.reward_resolution_amendment import (
        normalize_amended_state,
    )

    normalized = []
    for env, snapshot in hashes:
        funding = {
            f"{C}|{RCASE}|{PERIOD}|amendment-reserve": {"bank": 3300, "partner": 3000}
        }
        normalize_amended_state(env.tools.db, snapshot, funding)
        normalized.append((snapshot["transactions"], funding))
    assert normalized[0] == normalized[1]


@pytest.mark.parametrize(
    "defect", ["funding", "ledger", "pending", "hold", "correction", "extra_copy"]
)
def test_semantic_normalization_preserves_corruption(defect):
    env, result, _ = pending()
    amend(env.tools, **amended_request(env, result))
    retry(env.tools, result["id"])
    before = env.tools.get_db_hash()
    op = env.tools.db.reward_resolution_executions[result["id"]]
    if defect == "funding":
        op.result["funding_charge"]["bank"] += 1
    elif defect == "ledger":
        env.tools.db.transactions[op.result["ledger"][0]["id"]].amount += 1
    elif defect == "pending":
        op.result["status"] = "execution_unknown"
    elif defect == "hold":
        op.result["funding_hold"] = deepcopy(op.result["funding_charge"])
    elif defect == "correction":
        env.tools.db.transactions["bank-correction"].amount += 1
    elif defect == "extra_copy":
        original = env.tools.db.transactions[op.result["ledger"][0]["id"]]
        copy = original.model_copy(update={"id": "unrelated-copy"})
        env.tools.db.transactions[copy.id] = copy
        op.result["ledger"][0] = copy.model_dump()
    assert validated_amendment(env.tools.db, op) is None
    assert env.tools.get_db_hash() != before


def test_release_exhausts_bank_then_partner():
    assert funding_after_amendment(
        {"reserve_id": "r", "bank": 2, "partner": 9}, 10, 4
    ) == {"reserve_id": "r", "bank": 0, "partner": 5}


@pytest.mark.parametrize(
    "defect",
    [
        "overpay",
        "immediate_overpay",
        "duplicate",
        "scope",
        "immediate_collision",
        "deferred_collision",
    ],
)
def test_malformed_correction_cannot_partially_post_agreement(defect):
    env, args = prepared()
    profile = env.tools.db.reward_resolution_correction_profiles["claim-offer"]
    if defect == "overpay":
        profile.amount = 54
    elif defect == "immediate_overpay":
        profile.transaction_id = RA
        profile.amount = 8  # 18 immediate + 8 correction exceeds the 25 obligation.
    elif defect == "duplicate":
        profile.ledger_id = RA
    elif defect == "scope":
        profile.transaction_id = "txn_804205"
    elif defect in {"immediate_collision", "deferred_collision"}:
        tid = RA if defect == "immediate_collision" else RB
        profile.ledger_id = f"reward-resolution:{RCASE}:{tid}"
    before = env.tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected, match="ERR_CORRECTION"):
        execute(env.tools, **args)
    assert env.tools.db.model_dump() == before


def test_new_consent_cannot_lower_current_customer_minimum():
    env, result, _ = pending()
    env.tools.db.reward_resolution_preferences[C].minimum_payouts = {RA: 26}
    before = deepcopy(env.tools._resolution_consents)
    response = call(
        env, "ask_client", customer_id=C, request=amendment_packet(env, result)
    )
    assert response["status"] == "declined"
    assert "confirmation_id" not in response
    assert env.tools._resolution_consents == before
