"""Nominal funding differs from guaranteed customer cash after an explicit deduction."""

from copy import deepcopy

import pytest

from tests.test_domains.test_banking_ru.test_compatibility_certificate import (
    problem_offer,
)
from tests.test_domains.test_banking_ru.test_resolution_funding_pools import offer


def deducted_offer():
    o = offer()
    for i, row in enumerate(o.components):
        fee = (i + 1) * 4
        row.amount = (12 + fee) / 100
        row.settlement_deduction = fee / 100
        row.bank_cost = row.partner_cost = (12 + fee) / 200
        row.pool_costs = {k: (12 + fee) / 200 for k in row.pool_costs}
        for p in o.selection_budget.pools:
            if p.id in row.pool_costs:
                p.amount = (12 + fee) / 200
    o.selection_budget.bank = o.selection_budget.partner = 0.18
    return o


def test_net_payment_uses_nominal_funding_and_net_offer_cap():
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    assert selected_amounts(deducted_offer(), ["x", "y"]) == (24, {"a": 6, "b": 18})
    assert selected_amounts(deducted_offer(), ["x"]) == (12, {"a": 3, "b": 9})


@pytest.mark.parametrize(
    "defect",
    [
        "negative",
        "zero_net",
        "negative_net",
        "allocation_exceeds_net",
        "nominal_funding_short",
        "net_cap",
    ],
)
def test_deduction_does_not_bypass_financial_guards(defect):
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    o = deducted_offer()
    r = o.components[0]
    if defect == "negative":
        r.settlement_deduction = -0.01
    if defect == "zero_net":
        r.settlement_deduction = r.amount
    if defect == "negative_net":
        r.settlement_deduction = r.amount + 0.01
    if defect == "allocation_exceeds_net":
        r.settlement_deduction = 0.14
    if defect == "nominal_funding_short":
        r.bank_cost = 0.04
    if defect == "net_cap":
        o.amount = 0.23
    with pytest.raises(ValueError):
        selected_amounts(o, ["x", "y"])


def test_typed_component_exposes_explicit_deduction_and_legacy_zero():
    from tau2.domains.banking_ru.reward_resolution_models import (
        RewardResolutionComponent,
    )

    raw = vars(deducted_offer().components[0])
    assert RewardResolutionComponent(**raw).settlement_deduction == 0.04
    raw.pop("settlement_deduction")
    assert RewardResolutionComponent(**raw).settlement_deduction == 0


def test_author_proof_optimizes_net_and_never_accepts_gross_as_payment():
    from bank2_resolution_oracle import compatibility_choices

    o = problem_offer()
    for r in o["components"]:
        fee = 4 if r["exclusive_group"] == "g0" else 8
        r.update(
            amount=(12 + fee) / 100,
            settlement_deduction=fee / 100,
            bank_cost=(12 + fee) / 200,
            partner_cost=(12 + fee) / 200,
        )
    o["selection_budget"].update(bank=0.18, partner=0.18)
    result = compatibility_choices(o, {"a": 6, "b": 18}, certificate=["p00", "p11"])
    assert result[0]["additional"] == 24 and result[0]["allocation"] == {
        "a": 6,
        "b": 18,
    }
    bad = deepcopy(o)
    bad["amount"] = 0.36
    # Feasible 24-cent net witness cannot prove a 36-cent optimum; even its
    # sum-of-group-max upper bound is 24, so the returned value stays net.
    assert (
        compatibility_choices(bad, {"a": 100, "b": 100}, certificate=["p00", "p11"])[0][
            "additional"
        ]
        == 24
    )
