"""Customer binding AND tariff details, received in the authorized interval."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize(
    "route",
    [
        "direct",
        "profile_catalog",
        "catalog_profile",
        "catalog_only",
        "profile_only",
        "wrong_customer",
        "wrong_tariff",
        "before_identity",
        "after_write",
    ],
)
def test_bank41_tariff_evidence(route):
    task = next(t for t in get_tasks() if t.id == "Bank 41")
    session = PlatformSession(task, get_environment, max_errors=0)
    customer = "egorova_n_3358"
    profile = ("get_customer_profile", {"customer_id": customer})
    catalog = ("list_tariffs", {})
    pair = [profile, catalog]
    if route == "catalog_profile":
        pair.reverse()
    elif route == "catalog_only":
        pair = [catalog]
    elif route == "profile_only":
        pair = [profile]
    elif route == "wrong_customer":
        other = next(c for c in session.env.tools.db.customers if c != customer)
        pair = [("get_customer_profile", {"customer_id": other}), catalog]
    elif route == "wrong_tariff":
        # Test-only state: current product differs from the Classic catalogue row.
        other_tariff = next(
            t for t in session.env.tools.db.tariffs if t != "tariff_classic"
        )
        session.env.tools.db.customers[customer].tariff_id = other_tariff

    def read_pair():
        for name, args in pair:
            assert not session.call(name, args).is_error

    for action in deepcopy(task.evaluation_criteria.actions):
        if action.name == "get_tariff" and route != "direct":
            if route not in {"before_identity", "after_write"}:
                read_pair()
            continue
        assert not session.call(action.name, action.arguments).is_error
        if action.name == "find_customer" and route == "before_identity":
            read_pair()
        if action.name == "amend_cashback_investigation" and route == "after_write":
            read_pair()
    session.call("done", {})
    expected = int(route in {"direct", "profile_catalog", "catalog_profile"})
    for grade in [session.grade(), grade_full_session(session).reward_info]:
        assert grade.reward == expected, grade.info
        if not expected:
            assert grade.reward_breakdown["ACTION"] == 0, grade.info


def test_bank41_v3_noop():
    task = next(t for t in get_tasks() if t.id == "Bank 41")
    session = PlatformSession(task, get_environment, max_errors=0)
    session.call("done", {})
    for grade in [session.grade(), grade_full_session(session).reward_info]:
        assert grade.reward == 0
        assert grade.reward_breakdown["DB"] == 0
