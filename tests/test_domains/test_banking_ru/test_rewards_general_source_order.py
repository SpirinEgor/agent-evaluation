"""RO-01 may follow completed RO-02 identification, before business execution."""

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("task_id", ["Bank 2", "Bank 40", "Bank 41"])
def test_general_framework_after_identification_is_allowed(task_id):
    task = next(task for task in get_tasks() if task.id == task_id)
    actions = list(task.evaluation_criteria.actions)
    general = next(
        action
        for action in actions
        if action.name == "file_read"
        and action.arguments.get("path") == "/policies/general.md"
    )
    actions.remove(general)
    identity_index = next(
        index
        for index, action in enumerate(actions)
        if action.name == "verify_identity"
    )
    actions.insert(identity_index + 1, general)
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    for action in actions:
        session.call(action.name, action.arguments)
    session.call("done", {})
    assert session.errors == 0
    reward = grade_full_session(session).reward_info
    assert reward.final_reward == 1, reward.info


@pytest.fixture(autouse=True)
def historical_bank2_composite(request):
    """This module records composite-v1 behavior, not the new v2 scenario."""
    params = getattr(getattr(request.node, "callspec", None), "params", {})
    if params.get("task_id") == "Bank 2" or request.node.originalname in []:
        request.getfixturevalue("bank2_composite_v1_scenarios")
