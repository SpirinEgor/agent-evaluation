"""Real success/error contracts for common compensation authority APIs."""

from tests.test_domains.test_banking_ru.tool_contract_support import (
    identify,
    reject,
    succeed,
)

CUSTOMER = "gromov_a_1187"


def permission_read(env):
    identify(env, CUSTOMER)
    rows = succeed(env, "get_cashback_adjustment_permissions", customer_id=CUSTOMER)
    assert "adjustment-other-purchase" in str(rows)


def permission_read_error(env):
    reject(
        env,
        "get_cashback_adjustment_permissions",
        "ERR_NOT_VERIFIED",
        customer_id=CUSTOMER,
    )


def adjustment(env):
    identify(env, CUSTOMER)
    env.tools.db.cashback_adjustment_permissions["adjustment-other-purchase"][
        "transaction_id"
    ] = "txn_552290"
    before = env.tools.db.accounts["acc_1187"].balance
    succeed(
        env,
        "execute_cashback_adjustment",
        permission_id="adjustment-other-purchase",
        transaction_id="txn_552290",
        amount=30.0,
    )
    assert env.tools.db.accounts["acc_1187"].balance == before + 30


def adjustment_error(env):
    identify(env, CUSTOMER)
    reject(
        env,
        "execute_cashback_adjustment",
        "ERR_ADJUSTMENT_NOT_AUTHORIZED",
        permission_id="adjustment-other-purchase",
        transaction_id="txn_552290",
        amount=30.0,
    )


SUCCESS_SCENARIOS = {
    "get_cashback_adjustment_permissions": permission_read,
    "execute_cashback_adjustment": adjustment,
}
ERROR_SCENARIOS = {
    "get_cashback_adjustment_permissions": permission_read_error,
    "execute_cashback_adjustment": adjustment_error,
}
