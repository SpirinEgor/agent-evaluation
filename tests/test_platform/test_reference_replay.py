import json

import pytest

pytest.importorskip("mcp")

from tau2.domains.banking_ru.environment import get_tasks
from tau2.platform.client import PlatformClient
from test_platform.conftest import run_with_app

TASKS = get_tasks("base")


@pytest.mark.parametrize(
    "task", [next((t for t in TASKS if t.id == "Bank 21"))], ids=["Bank 21"]
)
def test_reference_trajectory_scores_one_through_mcp(app, settings, task):
    """Real MCP references retain raw components and their adjudicated final label."""

    async def go(http):
        client = PlatformClient("http://platform", http_client=http)
        return await client.replay_actions(task.id, task.evaluation_criteria.actions)

    result = run_with_app(app, go)
    assert result["status"] == "done"
    assert result["reward_breakdown"] == {
        "DB": 1.0,
        "ENV_ASSERTION": 1.0,
        "ACTION": 1.0,
    }

    # Inspect this actual persisted run, not a synthetic diagnostic or task ID
    # alone: an arbitrary None result must never satisfy a review expectation.
    saved = json.loads((settings.results_dir / f"{result['token']}.json").read_text())
    assert saved["id"] == result["token"]
    assert saved["task_id"] == result["task_id"] == task.id
    assert not any(m.get("error") for m in saved["messages"] if m["role"] == "tool")
    oracle = saved["reward_info"]
    assert oracle["reward"] == 1.0
    assert oracle["reward_breakdown"] == result["reward_breakdown"]
    action_info = oracle["info"]["action"]
    assert action_info is None or isinstance(action_info, dict)
    findings = [] if action_info is None else action_info.get("message_claims", [])
    assert not any(f["disposition"] == "violation" for f in findings)
    reviews = {
        (f["family"], f["reason"])
        for f in findings
        if f["disposition"] == "needs_review"
    }
    # Later source bindings already prove the ten formerly ambiguous references
    # in both modes (test_reference_bindings). The MCP boundary must publish the
    # same final grade, rather than retain the superseded Task4 review labels.
    assert reviews == set()
    assert oracle["info"].get("grading_status") != "needs_review"
    assert result["reward"] == 1.0
    assert result.get("grading_status") != "needs_review"
