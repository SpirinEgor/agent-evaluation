"""Common fee-state synchronization real success and error examples."""

from tests.test_domains.test_banking_ru.tool_contract_support import (
    identify,
    reject,
    succeed,
)


def fee_state(env):
    identify(env, "solovyova_a_7734")
    state = succeed(env, "get_fee_refund_state", transaction_id="txn_088219")
    assert state["customer_id"] == "solovyova_a_7734"
    assert state["id"] == "txn_088219"
    assert state["processing_refunds"] == 30


def unknown_fee(env):
    reject(env, "get_fee_refund_state", "ERR_NOT_FOUND", transaction_id="unknown_fee")


SUCCESS_SCENARIOS = {"get_fee_refund_state": fee_state}
ERROR_SCENARIOS = {"get_fee_refund_state": unknown_fee}
