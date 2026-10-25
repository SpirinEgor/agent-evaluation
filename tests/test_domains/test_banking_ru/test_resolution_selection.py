"""Selection authorization is separate from finding a customer optimum."""

from types import SimpleNamespace

import pytest


def menu():
    def part(ident, group, amount, bank, partner, first):
        return SimpleNamespace(
            id=ident,
            exclusive_group=group,
            amount=amount,
            bank_cost=bank,
            partner_cost=partner,
            allocation_first_amount=first,
        )

    return SimpleNamespace(
        transaction_ids=["a", "b"],
        allocation_first_transaction_id="a",
        amount=20,
        selection_budget=SimpleNamespace(bank=7, partner=8),
        components=[
            part("x", "one", 8, 4, 4, 2),
            part("y", "one", 9, 5, 4, 3),
            part("z", "two", 7, 3, 4, 1),
            part("w", "three", 8, 4, 4, 1),
        ],
    )


def test_selected_components_are_added_exactly_and_order_is_irrelevant():
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    assert selected_amounts(menu(), ["z", "x"]) == (1500, {"a": 300, "b": 1200})
    assert selected_amounts(menu(), ["x", "z"]) == (1500, {"a": 300, "b": 1200})
    # A legal suboptimal selection must remain executable; maximizing is the agent's job.
    assert selected_amounts(menu(), ["x"]) == (800, {"a": 200, "b": 600})


@pytest.mark.parametrize(
    "ids", [[], ["x", "x"], ["unknown"], ["x", "y"], ["y", "z"], ["x", "z", "w"]]
)
def test_invalid_selection_rejected(ids):
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    with pytest.raises(ValueError):
        selected_amounts(menu(), ids)
