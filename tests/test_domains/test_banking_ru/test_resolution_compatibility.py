"""Cross-group exclusions apply to every menu, in either declaration direction."""

from types import SimpleNamespace

import pytest

from tau2.domains.banking_ru.reward_resolution import selected_amounts
from tau2.domains.banking_ru.reward_resolution_models import RewardResolutionComponent


def menu():
    rows = [
        dict(
            id=ident,
            exclusive_group=ident,
            amount=4,
            bank_cost=2,
            partner_cost=2,
            allocation_first_amount=1,
            incompatible_with=bad,
        )
        for ident, bad in [("x", ["y"]), ("y", []), ("z", [])]
    ]
    return SimpleNamespace(
        components=[RewardResolutionComponent(**r) for r in rows],
        transaction_ids=["a", "b"],
        allocation_first_transaction_id="a",
        amount=12,
        selection_budget=SimpleNamespace(bank=6, partner=6),
    )


@pytest.mark.parametrize("ids", [["x", "y"], ["y", "x"], ["z", "y", "x"]])
def test_incompatible_selection_rejected_in_either_order(ids):
    with pytest.raises(ValueError, match="Несовместимые компоненты"):
        selected_amounts(menu(), ids)


@pytest.mark.parametrize("ids", [["x", "z"], ["y", "z"], ["z", "x"], ["z", "y"]])
def test_different_compatible_selections_remain_executable(ids):
    assert selected_amounts(menu(), ids) == (800, {"a": 200, "b": 600})


def test_unselected_conflicting_component_does_not_block_single_choice():
    assert selected_amounts(menu(), ["x"]) == (400, {"a": 100, "b": 300})


def test_legacy_component_defaults_to_no_cross_group_exclusions():
    component = RewardResolutionComponent(
        id="legacy",
        exclusive_group="g",
        amount=4,
        bank_cost=2,
        partner_cost=2,
        allocation_first_amount=1,
    )
    assert component.incompatible_with == []
