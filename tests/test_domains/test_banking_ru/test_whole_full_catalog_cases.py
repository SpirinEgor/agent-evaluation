"""Basic FULL/ALL reference and no-op checks for every banking task.

These cases provide whole-grade evidence for declared conditions only. They do
not establish exhaustive coverage of all semantic alternatives or secret encodings.
"""

from copy import deepcopy

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

TASKS = {task.id: task for task in get_tasks()}

REVIEW_REFERENCES: dict[str, tuple[str, str]] = {}


def _assert_final_status(reward, task_id, raw):
    findings = ((reward.info or {}).get("action") or {}).get("message_claims", [])
    reviews = {
        (f["family"], f["reason"])
        for f in findings
        if f["disposition"] == "needs_review"
    }
    if task_id in REVIEW_REFERENCES:
        assert reviews == {REVIEW_REFERENCES[task_id]}
        assert reward.grading_status == "needs_review"
        assert reward.final_reward is None
    else:
        assert not reviews
        assert reward.grading_status == "final"
        assert reward.final_reward == raw


@pytest.mark.parametrize("task_id", sorted(TASKS))
@pytest.mark.parametrize("route", ["reference", "noop"])
def test_each_task_original_whole_full_baseline(task_id, route):
    task = TASKS[task_id]
    session = PlatformSession(task, get_environment)
    actions = deepcopy(task.evaluation_criteria.actions)
    if route == "noop":
        actions = []
    session.replay_actions(actions)
    session.call("done", {})
    assert session.errors == 0
    reward = grade_full_session(session).reward_info
    assert session._reward_info is None
    if route == "noop":
        assert reward.reward_breakdown[RewardType.ACTION] == 0.0
        assert reward.final_reward == 0.0
        assert reward.grading_status == "final"
    else:
        assert reward.reward_breakdown == {
            RewardType.DB: 1.0,
            RewardType.ENV_ASSERTION: 1.0,
            RewardType.ACTION: 1.0,
        }
        _assert_final_status(reward, task_id, 1.0)
