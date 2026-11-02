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


def execute(env, partial=False):
    identify(env, C)
    if partial:
        env.tools.db.reward_resolution_partial_profiles["offer_610723"] = "txn_804227"
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
    assert result["status"] == ("execution_unknown" if partial else "succeeded")
    assert result["total_amount"] == (11 if partial else 24)
    return result


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


def reprocessing_fixture(env):
    from tau2.domains.banking_ru.reward_resolution_models import RewardReprocessingCase

    identify(env, C)
    env.tools.db.reward_reprocessing_cases["repair-api"] = RewardReprocessingCase(
        id="repair-api",
        customer_id=C,
        case_id=CASE,
        contract_id="rc_610482",
        period="2026-07",
        bound_card_id="card_1187",
        transaction_ids=["txn_804219", "txn_804227"],
        recovery_rates={"standard_cashback": 0.005, "partner_cashback": 0.001},
    )


def reprocessing_read(env):
    reprocessing_fixture(env)
    result = succeed(env, "get_reward_reprocessing_cases", customer_id=C)
    assert result[0]["id"] == "repair-api"


def repair_binding(env):
    reprocessing_fixture(env)
    result = succeed(
        env,
        "repair_reward_binding",
        reprocessing_id="repair-api",
        contract_id="rc_610482",
        replacement_card_id="card_804217",
    )
    assert result["status"] == "repaired"


def reprocess(env):
    repair_binding(env)
    result = succeed(
        env,
        "reprocess_rewards",
        reprocessing_id="repair-api",
        transaction_ids=["txn_804219", "txn_804227"],
    )
    assert result["status"] == "reprocessed" and len(result["ledger"]) == 2


def retry_resolution(env):
    partial = execute(env, partial=True)
    result = succeed(
        env, "retry_reward_resolution", resolution_operation_id=partial["id"]
    )
    assert result["status"] == "succeeded" and result["total_amount"] == 24


SUCCESS_SCENARIOS.update(
    {
        "get_reward_reprocessing_cases": reprocessing_read,
        "repair_reward_binding": repair_binding,
        "reprocess_rewards": reprocess,
        "retry_reward_resolution": retry_resolution,
    }
)
ERROR_SCENARIOS.update(
    {
        "get_reward_reprocessing_cases": lambda env: reject(
            env, "get_reward_reprocessing_cases", "ERR_NOT_VERIFIED", customer_id=C
        ),
        "repair_reward_binding": lambda env: reject(
            env,
            "repair_reward_binding",
            "ERR_NOT_FOUND",
            reprocessing_id="missing",
            contract_id="rc_610482",
            replacement_card_id="card_804217",
        ),
        "reprocess_rewards": lambda env: reject(
            env,
            "reprocess_rewards",
            "ERR_NOT_FOUND",
            reprocessing_id="missing",
            transaction_ids=["txn_804219"],
        ),
        "retry_reward_resolution": lambda env: reject(
            env,
            "retry_reward_resolution",
            "ERR_NOT_FOUND",
            resolution_operation_id="missing",
        ),
    }
)
