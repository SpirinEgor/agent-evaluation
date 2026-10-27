"""Addressed compensation authority controls, using real domain APIs."""

import pytest

from tau2.data_model.message import ToolCall
from tau2.domains.banking_ru.environment import get_environment

CUSTOMER = "gromov_a_1187"
TARGET = "txn_552290"


def call(env, name, **args):
    return env.get_response(ToolCall(id=name, name=name, arguments=args))


def test_shared_adjustment_api_is_registered():
    names = {t.name for t in get_environment().get_tools()}
    assert {
        "get_cashback_adjustment_permissions",
        "execute_cashback_adjustment",
    } <= names


def test_compensation_authority_enforces_transaction_program_and_ceiling():
    env = get_environment()
    env.tools.db.session.verified_customers.append(CUSTOMER)
    permission = {
        "id": "authority-test",
        "customer_id": CUSTOMER,
        "transaction_id": TARGET,
        "program": "standard_cashback",
        "max_amount": 30.0,
        "status": "approved",
        "valid_from": "2026-08-01",
        "valid_to": "2026-08-31",
        "used": False,
    }
    env.tools.db.cashback_adjustment_permissions["authority-test"] = permission
    before = env.tools.db.accounts["acc_1187"].balance
    bad = call(
        env,
        "execute_cashback_adjustment",
        permission_id="authority-test",
        transaction_id=TARGET,
        amount=31.0,
    )
    assert bad.error
    assert env.tools.db.accounts["acc_1187"].balance == before
    good = call(
        env,
        "execute_cashback_adjustment",
        permission_id="authority-test",
        transaction_id=TARGET,
        amount=30.0,
    )
    assert not good.error, good
    assert env.tools.db.accounts["acc_1187"].balance == before + 30
    again = call(
        env,
        "execute_cashback_adjustment",
        permission_id="authority-test",
        transaction_id=TARGET,
        amount=30.0,
    )
    assert again.error
    assert env.tools.db.accounts["acc_1187"].balance == before + 30


def test_authorized_adjustment_has_linked_ledger_entry():
    env = get_environment()
    env.tools.db.session.verified_customers.append(CUSTOMER)
    env.tools.db.cashback_adjustment_permissions["adjustment-other-purchase"][
        "transaction_id"
    ] = TARGET
    before = set(env.tools.db.transactions)
    result = call(
        env,
        "execute_cashback_adjustment",
        permission_id="adjustment-other-purchase",
        transaction_id=TARGET,
        amount=30.0,
    )
    assert not result.error
    added = [
        env.tools.db.transactions[x] for x in set(env.tools.db.transactions) - before
    ]
    assert len(added) == 1
    tx = added[0]
    assert tx.original_transaction_id == TARGET
    assert tx.card_id == env.tools.db.transactions[TARGET].card_id
    assert tx.account_id == env.tools.db.transactions[TARGET].account_id


# Standalone test data for the shared banking APIs.
pytestmark = pytest.mark.usefixtures("operation_api_scenarios")
