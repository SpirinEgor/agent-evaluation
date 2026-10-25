"""Real public success/error examples for shared resolution tools."""

from tests.test_domains.test_banking_ru.tool_contract_support import (
    identify,
    reject,
    succeed,
)

C = "gromov_a_1187"
CASE = "case_610903"


def offers(env):
    identify(env, C)
    rows = succeed(env, "get_reward_resolution_offers", customer_id=C, case_id=CASE)
    assert len(rows) == 4 and any(x["status"] == "under_review" for x in rows)


def offers_error(env):
    reject(
        env,
        "get_reward_resolution_offers",
        "ERR_NOT_VERIFIED",
        customer_id=C,
        case_id=CASE,
    )


def execute(env):
    identify(env, C)
    succeed(env, "refresh_cashback_settlement", customer_id=C, period="2026-07")
    packet = dict(
        type="reward_resolution_confirmation",
        offer_id="offer_610723",
        offer_version=1,
        payouts=[
            dict(transaction_id="txn_804219", amount=11),
            dict(transaction_id="txn_804227", amount=13),
        ],
        total_amount=24,
        case_id=CASE,
        resulting_claims=[
            dict(transaction_id="txn_804219", amount=14),
            dict(transaction_id="txn_804227", amount=40),
            dict(transaction_id="txn_804205", amount=7),
        ],
        state_revision=2,
    )
    consent = succeed(env, "ask_client", customer_id=C, request=packet)
    result = succeed(
        env,
        "execute_reward_resolution",
        **{
            k: v
            for k, v in packet.items()
            if k not in ("type", "resulting_claims", "state_revision")
        },
        expected_revision=2,
        confirmation_id=consent["confirmation_id"],
        idempotency_key="catalog-resolution",
    )
    assert result["status"] == "succeeded" and result["total_amount"] == 24


def execute_error(env):
    reject(
        env,
        "execute_reward_resolution",
        "ERR_NOT_FOUND",
        offer_id="unknown",
        offer_version=1,
        payouts=[dict(transaction_id="txn_804219", amount=11)],
        total_amount=11,
        case_id=CASE,
        confirmation_id="unknown",
        expected_revision=1,
        idempotency_key="catalog-unknown",
    )


SUCCESS_SCENARIOS = {
    "get_reward_resolution_offers": offers,
    "execute_reward_resolution": execute,
}
ERROR_SCENARIOS = {
    "get_reward_resolution_offers": offers_error,
    "execute_reward_resolution": execute_error,
}
