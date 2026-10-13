"""Real finance writes: money conservation, persisted products and atomic errors.

The small financial ledger is seeded in memory, never in benchmark data. Identity
helpers are backend setup, not evidence of an evaluator-valid customer dialogue.
Policy traps remain executable; mechanical validation is tested separately.
"""

import pytest

from tau2.domains.banking_ru.data_model import (
    Account,
    CashbackRules,
    Deposit,
    Loan,
    Penalty,
    Transaction,
)
from tau2.domains.banking_ru.environment import get_environment
from tau2.environment.environment import Environment
from tests.test_domains.test_banking_ru.tool_contract_support import (
    authorize_otp,
    identify,
    reject,
    snapshot,
    succeed,
)

CUSTOMER = "belova_n_2201"
FOREIGN_CUSTOMER = "dorohov_v_6630"


def seed_ledger(env: Environment):
    """Use literal money, dates and IDs so expectations do not mirror builders."""
    db = env.tools.db
    db.today = "2026-08-31"
    db.accounts = {
        "acc_1000": Account(
            id="acc_1000", customer_id=CUSTOMER, account_type="current", balance=200000
        ),
        "acc_1001": Account(
            id="acc_1001", customer_id=CUSTOMER, account_type="savings", balance=1000
        ),
        "acc_1002": Account(
            id="acc_1002",
            customer_id=FOREIGN_CUSTOMER,
            account_type="current",
            balance=500,
        ),
    }
    db.deposits = {
        "dep_1000": Deposit(
            id="dep_1000",
            customer_id=CUSTOMER,
            name="Старт",
            amount=10000,
            rate=0.07,
            early_rate=0.01,
            opened_at="2026-06-01",
            matures_at="2026-09-01",
            payout_account_id="acc_1000",
            early_withdrawal_payout=10024.93,
            maturity_payout=10176.44,
        ),
    }
    db.loans = {
        "ln_1000": Loan(
            id="ln_1000",
            customer_id=CUSTOMER,
            principal=10000,
            accrued_interest=200,
            rate=0.12,
            monthly_payment=500,
            days_overdue=2,
            next_payment_date="2026-09-10",
            penalties=[
                Penalty(id="pen_due", amount=50, accrued_at="2026-08-01"),
                Penalty(
                    id="pen_waived", amount=70, accrued_at="2026-07-01", waived=True
                ),
                Penalty(id="pen_paid", amount=80, accrued_at="2026-07-02", paid=True),
            ],
        ),
    }
    db.transactions = {
        "txn_100001": Transaction(
            id="txn_100001",
            customer_id=CUSTOMER,
            account_id="acc_1000",
            date="2026-08-20",
            amount=199,
            merchant="Обслуживание",
            kind="fee",
        ),
        "txn_100002": Transaction(
            id="txn_100002",
            customer_id=CUSTOMER,
            account_id="acc_1000",
            date="2026-08-21",
            amount=1200,
            merchant="Магазин",
            fee_amount=30,
        ),
    }
    db.cashback_rules = {
        CUSTOMER: CashbackRules(customer_id=CUSTOMER, accrued_current_period=5),
    }
    db.statements = {}


@pytest.fixture
def env():
    environment = get_environment()
    seed_ledger(environment)
    return environment


def wire_values(expected):
    """Represent literal expectations on the dictionary tool-result boundary.

    This formats expected scalars only; no production money/date/ID helper is
    used. Model-returning tools have their native JSON numbers asserted directly.
    """
    if isinstance(expected, dict):
        return {key: wire_values(value) for key, value in expected.items()}
    if isinstance(expected, list):
        return [wire_values(value) for value in expected]
    if isinstance(expected, (float, int, bool)):
        return str(expected)
    return expected


def assert_credit(env, *, amount, kind, merchant, account_id="acc_1000"):
    """Catch missing/duplicate ledger entry or credit booked to the wrong owner."""
    assert set(env.tools.db.transactions) == {"txn_100001", "txn_100002", "txn_100003"}
    transaction = env.tools.db.transactions["txn_100003"]
    assert transaction.model_dump() == {
        **Transaction(
            id="txn_100003",
            customer_id=CUSTOMER,
            account_id=account_id,
            date="2026-08-31",
            amount=amount,
            merchant=merchant,
            kind=kind,
            channel="online",
        ).model_dump(),
    }


def open_deposit_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    result = succeed(
        env,
        "open_deposit",
        customer_id=CUSTOMER,
        source_account_id="acc_1000",
        name="Старт",
        amount=10000,
        term_months=3,
    )
    # August 31 -> November 30 is 91 days: 10000 + 10000*.07*91/365.
    assert result["account_balance"] == wire_values(190000.0)
    assert result["funding_transaction_id"] == wire_values("txn_100003")
    deposit = env.tools.db.deposits["dep_1001"]
    assert result["deposit"] == wire_values(deposit.model_dump())
    assert deposit.id == "dep_1001"
    assert deposit.customer_id == CUSTOMER
    assert deposit.name == "Старт"
    assert deposit.closed_at is None
    assert deposit.amount == 10000
    assert deposit.rate == 0.07
    assert deposit.early_rate == 0.01
    assert deposit.opened_at == "2026-08-31"
    assert deposit.matures_at == "2026-11-30"
    assert deposit.payout_account_id == "acc_1000"
    assert deposit.maturity_payout == 10174.52
    assert deposit.early_withdrawal_payout == 10000
    assert deposit.status == "active"
    assert env.tools.db.accounts["acc_1000"].balance == 190000
    assert_credit(
        env,
        amount=10000,
        kind="deposit_funding",
        merchant="Первый взнос по вкладу dep_1001",
    )


def close_deposit_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    result = succeed(
        env, "close_deposit", deposit_id="dep_1000", payout_account_id="acc_1001"
    )
    assert result["payout"] == wire_values(10024.93)
    assert result["early_withdrawal"] == wire_values(True)
    assert result["account_balance"] == wire_values(11024.93)
    deposit = env.tools.db.deposits["dep_1000"]
    assert result["deposit"] == wire_values(deposit.model_dump())
    assert deposit.status == "closed"
    assert deposit.closed_at == "2026-08-31"
    assert env.tools.db.accounts["acc_1001"].balance == 11024.93
    assert env.tools.db.accounts["acc_1000"].balance == 200000
    assert_credit(
        env,
        amount=10024.93,
        kind="deposit_payout",
        merchant="Выплата по вкладу dep_1000",
        account_id="acc_1001",
    )


def early_repayment_success(env):
    seed_ledger(env)
    authorize_otp(env, CUSTOMER)
    result = succeed(
        env, "early_repayment", loan_id="ln_1000", amount=1000, mode="reduce_payment"
    )
    loan = env.tools.db.loans["ln_1000"]
    assert result == wire_values(
        {
            "loan": loan.model_dump(),
            "charged": 1000.0,
            "mode": "reduce_payment",
            "account_balance": 199000.0,
        }
    )
    # 50 penalty, 200 interest, 750 principal; waived/paid charges stay skipped.
    assert loan.id == "ln_1000"
    assert loan.customer_id == CUSTOMER
    assert loan.next_payment_date == "2026-09-10"
    assert loan.principal == 9250
    assert loan.accrued_interest == 0
    assert loan.monthly_payment == 462.5
    assert loan.status == "active"
    assert [(p.waived, p.paid) for p in loan.penalties] == [
        (False, True),
        (True, False),
        (False, True),
    ]
    assert env.tools.db.accounts["acc_1000"].balance == 199000
    assert_credit(
        env,
        amount=1000,
        kind="loan_repayment",
        merchant="Досрочное погашение кредита ln_1000",
    )


def waive_penalty_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    before = snapshot(env)
    result = succeed(env, "waive_penalty", loan_id="ln_1000", penalty_id="pen_due")
    loan = env.tools.db.loans["ln_1000"]
    assert result == wire_values(
        {"loan": loan.model_dump(), "waived_penalty_id": "pen_due"}
    )
    assert loan.id == "ln_1000"
    assert loan.customer_id == CUSTOMER
    assert loan.penalties[0].id == "pen_due"
    assert loan.penalties[0].amount == 50
    assert loan.penalties[0].waived is True
    assert loan.penalties[0].paid is False
    assert loan.waivers_used == 1
    assert loan.principal == 10000
    assert loan.accrued_interest == 200
    assert snapshot(env)["db"]["accounts"] == before["db"]["accounts"]
    assert snapshot(env)["db"]["transactions"] == before["db"]["transactions"]


def refund_fee_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    result = succeed(
        env,
        "refund_fee",
        transaction_id="txn_100002",
        request={"type": "fee_correction"},
        amount=12.34,
    )
    assert result == wire_values(
        {
            "refunded": 12.34,
            "request": {"type": "fee_correction"},
            "account_balance": 200012.34,
        }
    )
    assert env.tools.db.accounts["acc_1000"].balance == 200012.34
    assert env.tools.db.transactions["txn_100002"].fee_refunded is True
    assert env.tools.db.transactions["txn_100001"].fee_refunded is False
    assert_credit(
        env,
        amount=12.34,
        kind="fee_refund",
        merchant="Возврат комиссии по операции txn_100002",
    )


def refund_transaction_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    result = succeed(
        env,
        "refund_transaction",
        transaction_id="txn_100002",
        request={"type": "purchase_refund"},
    )
    assert result == wire_values(
        {
            "refunded": 1200.0,
            "request": {"type": "purchase_refund"},
            "account_balance": 201200.0,
        }
    )
    assert env.tools.db.accounts["acc_1000"].balance == 201200
    assert env.tools.db.transactions["txn_100002"].amount == 1200
    assert_credit(
        env, amount=1200, kind="fee_refund", merchant="Возврат по операции txn_100002"
    )


def grant_cashback_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    result = succeed(env, "grant_cashback", transaction_id="txn_100002", amount=24)
    assert result == wire_values({"granted": 24, "account_balance": 200024.0})
    assert env.tools.db.accounts["acc_1000"].balance == 200024
    assert env.tools.db.transactions["txn_100002"].cashback_granted is True
    assert env.tools.db.cashback_rules[CUSTOMER].accrued_current_period == 29
    assert_credit(
        env, amount=24, kind="cashback", merchant="Кешбэк по операции txn_100002"
    )


def open_account_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    before = snapshot(env)
    result = succeed(
        env,
        "open_account",
        customer_id=CUSTOMER,
        currency="usd",
        account_type="savings",
    )
    assert result == {
        "id": "acc_1003",
        "customer_id": CUSTOMER,
        "currency": "USD",
        "account_type": "savings",
        "balance": 0,
        "status": "active",
        "debt": 0,
    }
    assert env.tools.db.accounts["acc_1003"].model_dump() == result
    assert set(env.tools.db.accounts) == {
        "acc_1000",
        "acc_1001",
        "acc_1002",
        "acc_1003",
    }
    for account_id in ("acc_1000", "acc_1001", "acc_1002"):
        assert (
            env.tools.db.accounts[account_id].model_dump()
            == before["db"]["accounts"][account_id]
        )


def close_account_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    env.tools.db.accounts["acc_1001"].balance = 0
    before = snapshot(env)
    result = succeed(env, "close_account", account_id="acc_1001")
    assert result == {
        "id": "acc_1001",
        "customer_id": CUSTOMER,
        "currency": "RUB",
        "account_type": "savings",
        "balance": 0,
        "status": "closed",
        "debt": 0,
    }
    assert env.tools.db.accounts["acc_1001"].model_dump() == result
    assert (
        env.tools.db.accounts["acc_1000"].model_dump()
        == before["db"]["accounts"]["acc_1000"]
    )


def transfer_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    result = succeed(
        env,
        "transfer_between_own_accounts",
        from_account_id="acc_1000",
        to_account_id="acc_1001",
        amount=123.45,
    )
    assert result == wire_values(
        {"from_balance": 199876.55, "to_balance": 1123.45, "amount": 123.45}
    )
    assert env.tools.db.accounts["acc_1000"].balance == 199876.55
    assert env.tools.db.accounts["acc_1001"].balance == 1123.45
    assert env.tools.db.accounts["acc_1002"].balance == 500
    assert len(env.tools.db.transactions) == 2


def order_statement_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    before = snapshot(env)
    result = succeed(
        env,
        "order_statement",
        account_id="acc_1000",
        date_from="2026-08-01",
        date_to="2026-08-31",
        email="  BELOVA.N@EXAMPLE.RU  ",
    )
    assert result == {
        "id": "stm_1000",
        "customer_id": CUSTOMER,
        "account_id": "acc_1000",
        "date_from": "2026-08-01",
        "date_to": "2026-08-31",
        "email": "belova.n@example.ru",
        "created_at": "2026-08-31",
    }
    assert env.tools.db.statements["stm_1000"].model_dump() == result
    assert set(env.tools.db.statements) == {"stm_1000"}
    assert snapshot(env)["db"]["accounts"] == before["db"]["accounts"]


def change_tariff_success(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    result = succeed(
        env, "change_tariff", customer_id=CUSTOMER, new_tariff_id="tariff_comfort"
    )
    customer = env.tools.db.customers[CUSTOMER]
    assert result == customer.model_dump()
    assert customer.tariff_id == "tariff_classic"
    assert customer.pending_tariff_id == "tariff_comfort"
    assert customer.pending_tariff_from == "2026-09-01"


SUCCESS_SCENARIOS = {
    "open_deposit": open_deposit_success,
    "close_deposit": close_deposit_success,
    "early_repayment": early_repayment_success,
    "waive_penalty": waive_penalty_success,
    "refund_fee": refund_fee_success,
    "refund_transaction": refund_transaction_success,
    "grant_cashback": grant_cashback_success,
    "open_account": open_account_success,
    "close_account": close_account_success,
    "transfer_between_own_accounts": transfer_success,
    "order_statement": order_statement_success,
    "change_tariff": change_tariff_success,
}


# Every call is well-formed enough to reach a domain error, not a schema error.
WRITE_ARGUMENTS = {
    "open_deposit": dict(
        customer_id=CUSTOMER,
        source_account_id="acc_1000",
        name="Старт",
        amount=10000,
        term_months=3,
    ),
    "close_deposit": dict(deposit_id="dep_1000", payout_account_id="acc_1001"),
    "early_repayment": dict(loan_id="ln_1000", amount=1000, mode="reduce_term"),
    "waive_penalty": dict(loan_id="ln_1000", penalty_id="pen_due"),
    "refund_fee": dict(transaction_id="txn_100001", request={"type": "fee_correction"}),
    "refund_transaction": dict(
        transaction_id="txn_100002", request={"type": "purchase_refund"}
    ),
    "grant_cashback": dict(transaction_id="txn_100002", amount=24),
    "open_account": dict(customer_id=CUSTOMER, currency="USD"),
    "close_account": dict(account_id="acc_1001"),
    "transfer_between_own_accounts": dict(
        from_account_id="acc_1000", to_account_id="acc_1001", amount=123.45
    ),
    "order_statement": dict(
        account_id="acc_1000",
        date_from="2026-08-01",
        date_to="2026-08-31",
        email="belova.n@example.ru",
    ),
    "change_tariff": dict(customer_id=CUSTOMER, new_tariff_id="tariff_comfort"),
}


def rejected_write(env, name, code, /, **overrides):
    seed_ledger(env)
    identify(env, CUSTOMER)
    if name == "early_repayment":
        authorize_otp(env, CUSTOMER)
    reject(env, name, code, **(WRITE_ARGUMENTS[name] | overrides))


def open_deposit_error(env):
    rejected_write(env, "open_deposit", "ERR_INSUFFICIENT_FUNDS", amount=200001)


def close_deposit_error(env):
    rejected_write(env, "close_deposit", "ERR_NOT_FOUND", payout_account_id="missing")


def early_repayment_error(env):
    rejected_write(env, "early_repayment", "ERR_INSUFFICIENT_FUNDS", amount=200001)


def waive_penalty_error(env):
    rejected_write(env, "waive_penalty", "ERR_NOT_FOUND", penalty_id="missing")


def refund_fee_error(env):
    rejected_write(env, "refund_fee", "ERR_INVALID_ARGUMENT", amount=200)


def refund_transaction_error(env):
    seed_ledger(env)
    identify(env, CUSTOMER)
    env.tools.db.transactions["txn_100002"].status = "hold"
    reject(
        env,
        "refund_transaction",
        "ERR_INVALID_ARGUMENT",
        **WRITE_ARGUMENTS["refund_transaction"],
    )


def grant_cashback_error(env):
    rejected_write(env, "grant_cashback", "ERR_INVALID_ARGUMENT", amount=0)


def open_account_error(env):
    rejected_write(env, "open_account", "ERR_NOT_FOUND", customer_id="missing")


def close_account_error(env):
    rejected_write(env, "close_account", "ERR_ACCOUNT_NOT_EMPTY")


def transfer_error(env):
    rejected_write(
        env,
        "transfer_between_own_accounts",
        "ERR_FOREIGN_ACCOUNT",
        to_account_id="acc_1002",
    )


def order_statement_error(env):
    rejected_write(
        env, "order_statement", "ERR_EMAIL_NOT_CONFIRMED", email="other@example.ru"
    )


def change_tariff_error(env):
    rejected_write(env, "change_tariff", "ERR_NOT_FOUND", new_tariff_id="missing")


ERROR_SCENARIOS = {
    "open_deposit": open_deposit_error,
    "close_deposit": close_deposit_error,
    "early_repayment": early_repayment_error,
    "waive_penalty": waive_penalty_error,
    "refund_fee": refund_fee_error,
    "refund_transaction": refund_transaction_error,
    "grant_cashback": grant_cashback_error,
    "open_account": open_account_error,
    "close_account": close_account_error,
    "transfer_between_own_accounts": transfer_error,
    "order_statement": order_statement_error,
    "change_tariff": change_tariff_error,
}
