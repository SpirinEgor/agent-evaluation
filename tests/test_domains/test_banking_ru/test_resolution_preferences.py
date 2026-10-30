"""Client minimum is a separate instruction, never inferred from payment consent."""

from copy import deepcopy

import pytest

from tau2.data_model.message import AssistantMessage, ToolCall
from tau2.domains.banking_ru.reward_resolution_models import RewardResolutionPreferences
from tests.test_domains.test_banking_ru.test_cashback_settlement_state import (
    RA,
    RB,
    RCASE,
    C,
    call,
)
from tests.test_domains.test_banking_ru.test_resolution_unknown import (
    fallback_packet,
    fallback_ready,
    shared_pay,
    status,
)


def ready(*, reducible=False):
    env = fallback_ready()
    original = env.tools.db.reward_resolution_preferences[C].model_dump()
    env.tools.db.reward_resolution_preferences[C] = (
        RewardResolutionPreferences.model_validate(
            {
                **original,
                "minimum_payouts": {RA: 18},
                "minimum_payout_reduction_limits": {RA: 17} if reducible else {},
            }
        )
    )
    return env


def change(env, amount=17, *, transaction_id=RA, case_id=RCASE):
    return call(
        env,
        "ask_client",
        customer_id=C,
        transaction_id=transaction_id,
        request={
            "type": "reward_resolution_minimum_change",
            "case_id": case_id,
            "minimum_amount": amount,
        },
    )


def released(env):
    packet = fallback_packet(env)
    packet.update(
        offer_id="X", fallback_operation_id=None, total_amount=18, component_ids=["X"]
    )
    packet["payouts"] = [{"transaction_id": RA, "amount": 18}]
    packet["resulting_claims"] = [
        {**row, "amount": 7} if row["transaction_id"] == RA else row
        for row in packet["resulting_claims"]
    ]
    accepted = consent(env, packet)
    pending = execute(env, packet, accepted["confirmation_id"])
    status(env, pending["id"])
    return fallback_packet(env, pending["id"])


def consent(env, packet):
    return call(env, "ask_client", customer_id=C, request=packet)


def execute(env, packet, confirmation_id):
    return call(
        env,
        "execute_reward_resolution",
        **{
            k: v
            for k, v in packet.items()
            if k not in {"type", "resulting_claims", "state_revision"}
        },
        confirmation_id=confirmation_id,
        expected_revision=packet["state_revision"],
        idempotency_key="minimum-payment-" + packet["offer_id"],
    )


def test_public_preferences_show_initial_minimum_but_hide_author_limit():
    env = ready(reducible=True)
    result = call(
        env,
        "ask_client",
        customer_id=C,
        request={"type": "case_clarification", "topic": "resolution_preferences"},
    )
    assert result["data"]["minimum_payouts"] == {RA: 18}
    assert "minimum_payout_reduction_limits" not in result["data"]


@pytest.mark.parametrize(
    "reducible,expected", [(False, "declined"), (True, "accepted")]
)
def test_explicit_change_receipt_is_exact_and_does_not_mutate_bank_db(
    reducible, expected
):
    env = ready(reducible=reducible)
    before = deepcopy(env.tools.db.model_dump())
    result = change(env)
    assert result["request_type"] == "reward_resolution_minimum_change"
    assert result["status"] == expected
    assert result["answer_id"]
    assert result["data"] == {
        "customer_id": C,
        "case_id": RCASE,
        "transaction_id": RA,
        "previous_minimum": 18,
        "requested_minimum": 17,
        "effective_minimum": 17 if reducible else 18,
    }
    assert env.tools.db.model_dump() == before
    assert change(env) == result


def test_confirmation_cannot_lower_strict_minimum_and_execution_has_no_effects():
    env = ready()
    packet = released(env)
    before = deepcopy(env.tools.db.model_dump())
    result = consent(env, packet)
    assert result["status"] == "declined"
    assert "confirmation_id" not in result
    assert change(env)["status"] == "declined"
    assert consent(env, packet)["status"] == "declined"
    with pytest.raises(AssertionError, match="MINIMUM"):
        execute(env, packet, "forged")
    assert env.tools.db.model_dump() == before
    assert not env.tools._resolution_violations


def test_explicit_lowering_requires_fresh_consent_then_pays_exactly_seventeen():
    env = ready(reducible=True)
    packet = released(env)
    assert consent(env, packet)["status"] == "declined"
    assert change(env)["status"] == "accepted"
    accepted = consent(env, packet)
    assert accepted["status"] == "confirmed"
    result = execute(env, packet, accepted["confirmation_id"])
    assert result["status"] == "succeeded"
    assert result["total_amount"] == 17
    assert result["ledger"][0]["original_transaction_id"] == RA
    assert env.tools.db.reward_resolution_preferences[C].minimum_payouts == {RA: 18}


def test_changed_minimum_invalidates_prior_scoped_consent_without_db_changes():
    env = ready(reducible=True)
    # Z24 is legal under both minima, but its consent predates the changed instruction.
    packet = released(env)
    packet.update(
        offer_id="Z", fallback_operation_id=None, total_amount=24, component_ids=["Z"]
    )
    packet["payouts"] = [{"transaction_id": RA, "amount": 24}]
    packet["resulting_claims"] = [
        {**row, "amount": 1} if row["transaction_id"] == RA else row
        for row in packet["resulting_claims"]
    ]
    old = consent(env, packet)
    assert old["status"] == "confirmed"
    change(env)
    before = deepcopy(env.tools.db.model_dump())
    assert execute(env, packet, old["confirmation_id"])["status"] == "consent_stale"
    assert env.tools.db.model_dump() == before
    new = consent(env, packet)
    assert new["confirmation_id"] != old["confirmation_id"]
    assert execute(env, packet, new["confirmation_id"])["status"] == "succeeded"


@pytest.mark.parametrize(
    "defect",
    [
        "unverified",
        "foreign_transaction",
        "outside_case",
        "foreign_case",
        "unknown_transaction",
    ],
)
def test_change_rejects_unverified_foreign_or_unscoped_targets(defect):
    env = ready(reducible=True)
    transaction_id, case_id = RA, RCASE
    if defect == "unverified":
        env.tools.db.session.verified_customers = []
    if defect == "foreign_transaction":
        env.tools.db.transactions[RA].customer_id = "foreign"
    if defect == "outside_case":
        transaction_id = "txn_804205"
    if defect == "foreign_case":
        env.tools.db.cases[RCASE].customer_id = "foreign"
    if defect == "unknown_transaction":
        transaction_id = "unknown"
    before = deepcopy(env.tools.db.model_dump())
    with pytest.raises(AssertionError):
        change(env, transaction_id=transaction_id, case_id=case_id)
    assert env.tools.db.model_dump() == before


def test_lowering_one_purchase_does_not_create_a_minimum_for_other_scope():
    env = ready(reducible=True)
    assert change(env)["status"] == "accepted"
    # The second purchase remains governed by its independent offered amount.
    assert shared_pay(env, "W")["total_amount"] == 14
    assert change(env, transaction_id=RB)["status"] == "declined"


def test_minimum_override_is_session_local_and_replays_from_explicit_receipt():
    live = ready(reducible=True)
    packet = released(live)
    fresh = ready(reducible=True)
    fresh.tools.db = deepcopy(live.tools.db)
    change_call = ToolCall(
        id="change-minimum",
        name="ask_client",
        arguments={
            "customer_id": C,
            "transaction_id": RA,
            "request": {
                "type": "reward_resolution_minimum_change",
                "case_id": RCASE,
                "minimum_amount": 17,
            },
        },
    )
    response = live.get_response(change_call)
    assert not response.error
    assert consent(live, packet)["status"] == "confirmed"
    assert consent(fresh, packet)["status"] == "declined"
    before = deepcopy(fresh.tools.db.model_dump())
    fresh.set_state(
        initialization_data=None,
        initialization_actions=None,
        message_history=[
            AssistantMessage(role="assistant", tool_calls=[change_call]),
            response,
        ],
    )
    assert consent(fresh, packet)["status"] == "confirmed"
    assert fresh.tools.db.model_dump() == before


def test_returning_to_initial_minimum_never_revives_an_old_consent():
    env = ready(reducible=True)
    packet = released(env)
    packet.update(
        offer_id="Z", fallback_operation_id=None, total_amount=24, component_ids=["Z"]
    )
    packet["payouts"] = [{"transaction_id": RA, "amount": 24}]
    packet["resulting_claims"] = [
        {**row, "amount": 1} if row["transaction_id"] == RA else row
        for row in packet["resulting_claims"]
    ]
    old = consent(env, packet)
    first = change(env)
    assert change(env, 18)["data"]["effective_minimum"] == 18
    assert execute(env, packet, old["confirmation_id"])["status"] == "consent_stale"
    second = change(env)
    assert first["answer_id"] != second["answer_id"]
    assert second["data"]["previous_minimum"] == 18
    current = consent(env, packet)
    # A same-value repeat is idempotent and does not make current consent stale.
    assert change(env) == second
    assert execute(env, packet, current["confirmation_id"])["status"] == "succeeded"


@pytest.mark.parametrize("amount", [16, 18.001, -1, float("inf")])
def test_unsupported_or_invalid_change_never_lowers_effective_minimum(amount):
    env = ready(reducible=True)
    before = deepcopy(env.tools.db.model_dump())
    response = env.get_response(
        ToolCall(
            id="invalid-change",
            name="ask_client",
            arguments={
                "customer_id": C,
                "transaction_id": RA,
                "request": {
                    "type": "reward_resolution_minimum_change",
                    "case_id": RCASE,
                    "minimum_amount": amount,
                },
            },
        )
    )
    if amount == 16:
        import json

        result = json.loads(response.content)
        assert result["status"] == "declined"
        assert result["data"]["effective_minimum"] == 18
    else:
        assert response.error
    assert env.tools.db.model_dump() == before
