"""Named funding budgets are independent limits, not extra payments."""

from types import SimpleNamespace as NS

import pytest


def offer():
    rows = [
        NS(
            id="x",
            exclusive_group="x",
            amount=0.12,
            bank_cost=0.06,
            partner_cost=0.06,
            allocation_first_amount=0.03,
            incompatible_with=[],
            pool_costs={"bank_a": 0.06, "partner_a": 0.06},
        ),
        NS(
            id="y",
            exclusive_group="y",
            amount=0.12,
            bank_cost=0.06,
            partner_cost=0.06,
            allocation_first_amount=0.03,
            incompatible_with=[],
            pool_costs={"bank_b": 0.06, "partner_b": 0.06},
        ),
    ]
    pools = [
        NS(id=k, source=k.split("_")[0], amount=0.06)
        for k in ["bank_a", "bank_b", "partner_a", "partner_b"]
    ]
    return NS(
        components=rows,
        selection_budget=NS(bank=0.12, partner=0.12, pools=pools),
        amount=0.24,
        transaction_ids=["a", "b"],
        allocation_first_transaction_id="a",
    )


def test_independent_pools_accept_full_and_smaller_legal_selections():
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    assert selected_amounts(offer(), ["x", "y"]) == (24, {"a": 6, "b": 18})
    assert selected_amounts(offer(), ["x"]) == (12, {"a": 3, "b": 9})


@pytest.mark.parametrize(
    "defect",
    ["bank_over", "partner_over", "unknown", "negative", "missing", "duplicate_pool"],
)
def test_invalid_pool_funding_rejected_despite_valid_global_budgets(defect):
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    o = offer()
    if defect == "bank_over":
        o.components[1].pool_costs = {"bank_a": 0.06, "partner_b": 0.06}
    if defect == "partner_over":
        o.components[1].pool_costs = {"bank_b": 0.06, "partner_a": 0.06}
    if defect == "unknown":
        o.components[1].pool_costs = {"unknown": 0.06, "partner_b": 0.06}
    if defect == "negative":
        o.components[1].pool_costs = {"bank_b": -0.06, "partner_b": 0.18}
    if defect == "missing":
        o.components[1].pool_costs = {}
    if defect == "duplicate_pool":
        o.selection_budget.pools.append(o.selection_budget.pools[0])
    with pytest.raises(ValueError):
        selected_amounts(o, ["x", "y"])
