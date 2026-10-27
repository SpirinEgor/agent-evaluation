from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment
from tests.test_domains.test_banking_ru.tool_contract_support import (
    identify,
    reject,
    succeed,
)


def test_cashback_investigation_records_items_without_paying():
    env = get_environment()
    identify(env, "gromov_a_1187")
    before = deepcopy(env.tools.db.accounts)
    result = succeed(
        env,
        "create_cashback_investigation",
        customer_id="gromov_a_1187",
        program="standard_cashback",
        amount=30,
        items=[{"transaction_id": "txn_552290", "amount": 30}],
    )
    assert result["amount"] == 30
    assert result["reward_items"] == [{"transaction_id": "txn_552290", "amount": 30}]
    assert env.tools.db.accounts == before
    assert len(env.tools.db.cases) == 1
    again = succeed(
        env,
        "create_cashback_investigation",
        customer_id="gromov_a_1187",
        program="standard_cashback",
        amount=30,
        items=[{"transaction_id": "txn_552290", "amount": 30}],
    )
    assert again["id"] == result["id"]
    assert len(env.tools.db.cases) == 1
    assert not env.tools.assert_no_duplicate_cashback_investigation_attempts(
        "gromov_a_1187"
    )


def test_cashback_investigation_requires_consistent_total():
    env = get_environment()
    identify(env, "gromov_a_1187")
    reject(
        env,
        "create_cashback_investigation",
        "ERR_INVALID_ARGUMENT",
        customer_id="gromov_a_1187",
        program="standard_cashback",
        amount=31,
        items=[{"transaction_id": "txn_552290", "amount": 30}],
    )


def test_bank_date_uses_scenario_state():
    env = get_environment()
    env.tools.db.today = "2026-09-02"
    assert succeed(env, "get_bank_date") == {"date": "2026-09-02"}


# Standalone test data for the investigation API.
pytestmark = pytest.mark.usefixtures("investigation_api_scenarios")
