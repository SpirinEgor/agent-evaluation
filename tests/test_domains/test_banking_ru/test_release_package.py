"""Offline installation checks for the shipped banking release."""

import shutil
from pathlib import Path

from tau2.domains.banking_ru.environment import (
    get_environment,
    get_tasks,
    get_tasks_split,
)
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
from tau2.registry import registry


def test_shipped_task_set_and_completion_contract():
    tasks = get_tasks("base")
    assert {t.id for t in tasks} == {f"Bank {n}" for n in range(1, 62)}
    assert set(get_tasks_split()["base"]) == {t.id for t in tasks}
    for task in tasks:
        criteria = task.evaluation_criteria
        assert criteria.completion_mode == "completion_only"
        assert criteria.tool_access_policy is not None
        assert not criteria.communicate_info and not criteria.nl_assertions
        assert criteria.message_claim_contract is None
        assert not any(
            a.name == "reply_to_ticket" and a.required_for_reward
            for a in criteria.actions
        )
    assert registry.get_tasks_loader("banking_ru") is not None


def test_corpus_and_tools_are_shipped_and_available():
    assert shutil.which("rg"), (
        "Install ripgrep before running file_search (see banking README)"
    )
    for name in ["db.json", "tasks.json", "intake_answers.json", "manifest.json"]:
        assert (BANKING_RU_DATA_DIR / name).is_file()
    a = get_environment(task_id="Bank 2")
    b = get_environment(task_id="Bank 7")
    tools_a = {t.name: t.params.model_json_schema() for t in a.get_tools()}
    assert tools_a == {t.name: t.params.model_json_schema() for t in b.get_tools()}
    assert len(tools_a) == 65
    assert {
        "file_read",
        "file_search",
        "ask_client",
        "verify_identity",
    } <= tools_a.keys()
    assert a.policy == b.policy
    assert str(BANKING_RU_DATA_DIR.resolve()).startswith(
        str(Path(__file__).resolve().parents[3])
    )
