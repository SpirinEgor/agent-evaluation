from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("sufficient_sources", 1),
        ("extra_ro05", 1),
        ("missing_cards", 0),
        ("missing_kb", 0),
        ("pending_change", 0),
        ("noop", 0),
    ],
)
def test_bank19_sources(mode, route, expected):
    task = next(t for t in get_tasks() if t.id == "Bank 19")
    actions = deepcopy(task.evaluation_criteria.actions)
    omit = {"/policies/transfers_fraud.md"}
    if route == "missing_cards":
        omit.add("/policies/cards.md")
    if route == "missing_kb":
        omit.add("/knowledge/kb_200.md")
    actions = [
        a
        for a in actions
        if not (a.name == "file_read" and a.arguments.get("path") in omit)
    ]
    s = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    if route == "extra_ro05":
        s.call("file_read", {"path": "/policies/transfers_fraud.md"})
    if route != "noop":
        s.replay_actions(actions)
    if route == "pending_change":
        s.call(
            "set_limit", {"card_id": "card_5581", "limit_type": "sbp", "amount": 150000}
        )
    s.call("done", {})
    reward = (
        s.grade().final_reward
        if mode == "HALF"
        else grade_full_session(s).reward_info.final_reward
    )
    assert reward == expected
