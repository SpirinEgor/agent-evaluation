"""Percentage retention uses per-component half-even cents, independently of cashback rounding."""

from types import SimpleNamespace as NS

import pytest

from tests.test_domains.test_banking_ru.test_compatibility_certificate import (
    problem_offer,
)


@pytest.mark.parametrize(
    "gross,rate,percent,fixed",
    [
        (52, 0.125, 6, 34),
        (44, 0.125, 6, 26),
        (28, 0.125, 4, 12),
        (36, 0.125, 4, 20),
        (40, 0.1875, 8, 20),
        (24, 0.1875, 4, 8),
    ],
)
def test_percentage_ties_round_to_even_cent_per_component(gross, rate, percent, fixed):
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    p = NS(
        id="p",
        exclusive_group="g",
        amount=gross / 100,
        settlement_deduction=fixed / 100,
        settlement_deduction_rate=rate,
        bank_cost=gross / 200,
        partner_cost=gross / 200,
        allocation_first_amount=0.03,
        incompatible_with=[],
        pool_costs={},
    )
    o = NS(
        components=[p],
        selection_budget=NS(bank=gross / 200, partner=gross / 200, pools=[]),
        amount=0.12,
        transaction_ids=["a", "b"],
        allocation_first_transaction_id="a",
    )
    assert selected_amounts(o, ["p"]) == (12, {"a": 3, "b": 9})


def test_author_certificate_independently_uses_half_even_not_half_up():
    from bank2_resolution_oracle import compatibility_choices

    o = problem_offer()
    for p in o["components"]:
        gross, fixed = (52, 34) if p["exclusive_group"] == "g0" else (44, 26)
        p.update(
            amount=gross / 100,
            settlement_deduction=fixed / 100,
            settlement_deduction_rate=0.125,
            bank_cost=gross / 200,
            partner_cost=gross / 200,
        )
    o["selection_budget"].update(bank=0.48, partner=0.48)
    assert (
        compatibility_choices(o, {"a": 6, "b": 18}, certificate=["p00", "p11"])[0][
            "additional"
        ]
        == 24
    )


@pytest.mark.parametrize("rate", [-0.01, 1, float("nan"), float("inf")])
def test_typed_percentage_rate_must_be_finite_fraction(rate):
    from pydantic import ValidationError

    from tau2.domains.banking_ru.reward_resolution_models import (
        RewardResolutionComponent,
    )

    with pytest.raises(ValidationError):
        RewardResolutionComponent(
            id="p",
            exclusive_group="g",
            amount=0.52,
            settlement_deduction=0.34,
            settlement_deduction_rate=rate,
            bank_cost=0.26,
            partner_cost=0.26,
            allocation_first_amount=0.03,
        )
