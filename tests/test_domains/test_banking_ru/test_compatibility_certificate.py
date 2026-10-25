"""Author certificates certify feasibility; monetary bounds remain independently checked."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))


def problem_offer():
    return dict(
        transaction_ids=["a", "b"],
        allocation_first_transaction_id="a",
        amount=0.24,
        selection_budget=dict(bank=0.12, partner=0.12),
        components=[
            dict(
                id=f"p{i}{a}",
                exclusive_group=f"g{i}",
                amount=0.12,
                bank_cost=0.06,
                partner_cost=0.06,
                allocation_first_amount=0.03,
                incompatible_with=["p10"] if (i, a) == (0, 0) else [],
            )
            for i in range(2)
            for a in range(3)
        ],
    )


@pytest.mark.parametrize(
    "ids", [["p00", "p10"], ["p00", "p00"], ["p00"], ["unknown", "p11"]]
)
def test_invalid_author_certificate_rejected(ids):
    from bank2_resolution_oracle import compatibility_choices

    with pytest.raises((AssertionError, ValueError, KeyError)):
        compatibility_choices(problem_offer(), {"a": 100, "b": 100}, certificate=ids)


def test_certificate_proves_cap_without_search_and_does_not_override_bounds():
    from bank2_resolution_oracle import compatibility_choices

    offer = problem_offer()
    result = compatibility_choices(offer, {"a": 6, "b": 18}, certificate=["p00", "p11"])
    assert result[0]["additional"] == 24 and result[0]["allocation"] == {
        "a": 6,
        "b": 18,
    }
    with pytest.raises(AssertionError):
        compatibility_choices(offer, {"a": 5, "b": 18}, certificate=["p00", "p11"])
    offer["selection_budget"]["bank"] = 0.11
    with pytest.raises(AssertionError):
        compatibility_choices(offer, {"a": 6, "b": 18}, certificate=["p00", "p11"])


def test_heterogeneous_funding_certificate_is_checked_per_selected_component():
    from bank2_resolution_oracle import compatibility_choices

    offer = problem_offer()
    for row in offer["components"]:
        bank = {"p00": 1, "p11": 10}.get(row["id"], 6)
        row.update(bank_cost=bank / 100, partner_cost=(12 - bank) / 100)
    offer["selection_budget"] = {"bank": 0.11, "partner": 0.13}
    assert (
        compatibility_choices(offer, {"a": 6, "b": 18}, certificate=["p00", "p11"])[0][
            "additional"
        ]
        == 24
    )
    for budget in [{"bank": 0.10, "partner": 0.14}, {"bank": 0.12, "partner": 0.12}]:
        offer["selection_budget"] = budget
        with pytest.raises(AssertionError):
            compatibility_choices(offer, {"a": 6, "b": 18}, certificate=["p00", "p11"])


def test_certificate_checks_named_program_budgets_independently():
    from bank2_resolution_oracle import compatibility_choices

    o = problem_offer()
    o["selection_budget"]["pools"] = [
        dict(id=src + str(i), source=src, amount=0.06)
        for src in ["bank", "partner"]
        for i in [0, 1]
    ]
    for r in o["components"]:
        group = r["exclusive_group"][-1]
        r["pool_costs"] = {"bank" + group: 0.06, "partner" + group: 0.06}
    assert (
        compatibility_choices(o, {"a": 6, "b": 18}, certificate=["p00", "p11"])[0][
            "additional"
        ]
        == 24
    )
    o["selection_budget"]["pools"][0]["amount"] = 0.05
    with pytest.raises(AssertionError):
        compatibility_choices(o, {"a": 6, "b": 18}, certificate=["p00", "p11"])


def weighted_offer():
    offer = problem_offer()
    for row in offer["components"]:
        amount = {"p00": 8, "p11": 16}.get(row["id"], 12)
        row.update(
            amount=amount / 100,
            bank_cost=amount / 200,
            partner_cost=amount / 200,
            allocation_first_amount=amount / 400,
        )
    return offer


def test_weighted_certificate_attains_budget_bound_not_group_count_bound():
    from itertools import product

    from bank2_resolution_oracle import compatibility_choices

    offer = weighted_offer()
    result = compatibility_choices(offer, {"a": 6, "b": 18}, certificate=["p00", "p11"])
    assert result[0]["additional"] == 24 and result[0]["allocation"] == {
        "a": 6,
        "b": 18,
    }
    groups = [
        [None] + [r for r in offer["components"] if r["exclusive_group"] == g]
        for g in ["g0", "g1"]
    ]
    feasible = []
    for selection in product(*groups):
        rows = [r for r in selection if r]
        ids = {r["id"] for r in rows}
        if any(ids.intersection(r["incompatible_with"]) for r in rows):
            continue
        amount = sum(round(r["amount"] * 100) for r in rows)
        bank = sum(round(r["bank_cost"] * 100) for r in rows)
        partner = sum(round(r["partner_cost"] * 100) for r in rows)
        first = sum(round(r["allocation_first_amount"] * 100) for r in rows)
        if (
            amount <= 24
            and bank <= 12
            and partner <= 12
            and first <= 6
            and amount - first <= 18
        ):
            feasible.append(amount)
    assert max(feasible) == result[0]["additional"]


def test_feasible_but_suboptimal_weighted_certificate_is_not_maximum_proof():
    from bank2_resolution_oracle import compatibility_choices

    with pytest.raises(AssertionError):
        compatibility_choices(
            weighted_offer(), {"a": 6, "b": 18}, certificate=["p00", "p12"]
        )


def test_weighted_certificate_cannot_override_offer_cap():
    from bank2_resolution_oracle import compatibility_choices

    offer = weighted_offer()
    offer["amount"] = 0.23
    with pytest.raises(AssertionError):
        compatibility_choices(offer, {"a": 6, "b": 18}, certificate=["p00", "p11"])


def test_optimal_certificate_can_omit_a_group_when_funding_cap_is_attained():
    from bank2_resolution_oracle import compatibility_choices

    offer = problem_offer()
    row = next(r for r in offer["components"] if r["id"] == "p00")
    row.update(
        amount=0.24, bank_cost=0.12, partner_cost=0.12, allocation_first_amount=0.06
    )
    result = compatibility_choices(offer, {"a": 6, "b": 18}, certificate=["p00"])
    assert result[0]["component_ids"] == ["p00"]
    assert result[0]["additional"] == 24 and result[0]["allocation"] == {
        "a": 6,
        "b": 18,
    }
