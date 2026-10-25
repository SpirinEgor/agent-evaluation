"""A selected funding component needs one companion from every declared clause."""

import pytest

from tests.test_domains.test_banking_ru.test_compatibility_certificate import (
    problem_offer,
)
from tests.test_domains.test_banking_ru.test_resolution_funding_pools import offer


def test_companions_are_selected_simultaneously_and_cycles_are_allowed():
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    o = offer()
    o.components[0].required_companions = [["y"]]
    o.components[1].required_companions = [["x"]]
    assert selected_amounts(o, ["y", "x"]) == (24, {"a": 6, "b": 18})
    with pytest.raises(ValueError, match="зависим"):
        selected_amounts(o, ["x"])


@pytest.mark.parametrize(
    "clauses", [[["y"]], [["x", "y"], ["y"]], [[]], [["unknown", "y"]]]
)
def test_absent_or_invalid_dependency_cannot_be_auto_added(clauses):
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    o = offer()
    o.components[0].required_companions = clauses
    with pytest.raises(ValueError, match="зависим"):
        selected_amounts(o, ["x"])


def test_each_clause_is_or_and_unselected_components_impose_no_obligations():
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    o = offer()
    o.components[0].required_companions = [["x", "y"]]
    o.components[1].required_companions = [["x"]]
    assert selected_amounts(o, ["x"]) == (12, {"a": 3, "b": 9})


def test_author_certificate_rejects_financially_correct_but_unmet_dependency():
    from bank2_resolution_oracle import compatibility_choices

    o = problem_offer()
    next(r for r in o["components"] if r["id"] == "p00")["required_companions"] = [
        ["p10"]
    ]
    with pytest.raises(AssertionError):
        compatibility_choices(o, {"a": 6, "b": 18}, certificate=["p00", "p11"])
    next(r for r in o["components"] if r["id"] == "p00")["required_companions"] = [
        ["p10", "p11"]
    ]
    assert (
        compatibility_choices(o, {"a": 6, "b": 18}, certificate=["p00", "p11"])[0][
            "additional"
        ]
        == 24
    )


def test_typed_legacy_component_has_no_dependency():
    from tau2.domains.banking_ru.reward_resolution_models import (
        RewardResolutionComponent,
    )

    assert (
        RewardResolutionComponent(**vars(offer().components[0])).required_companions
        == []
    )


def test_specialized_quarter_solver_does_not_ignore_companions():
    from bank2_resolution_oracle import quarter_menu_choices

    o = problem_offer()
    o["components"][0]["required_companions"] = [["p10"]]
    with pytest.raises(ValueError):
        quarter_menu_choices(o, {"a": 6, "b": 18})
