"""Offline installation checks for the shipped banking release."""

import hashlib
import json
import shutil
from pathlib import Path

from tau2.domains.banking_ru.environment import (
    get_environment,
    get_tasks,
)
from tau2.domains.banking_ru.mcp_systems import TOOL_OWNERS
from tau2.domains.banking_ru.task_store import load_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
from tau2.registry import registry


def test_shipped_task_set_and_completion_contract(tmp_path):
    index = json.loads((BANKING_RU_DATA_DIR / "tasks.json").read_text())
    assert index == {
        "task_files": [
            "tasks/bank_002/scenario.json" if n == 2 else f"tasks/bank_{n:03d}.json"
            for n in range(1, 62)
        ]
    }
    # A released snapshot must carry both the index and its relative targets.
    shutil.copyfile(BANKING_RU_DATA_DIR / "tasks.json", tmp_path / "tasks.json")
    shutil.copytree(BANKING_RU_DATA_DIR / "tasks", tmp_path / "tasks")
    raw_tasks = load_tasks(tmp_path / "tasks.json")
    assert raw_tasks == load_tasks(BANKING_RU_DATA_DIR / "tasks.json")
    assert [t["id"] for t in raw_tasks] == [f"Bank {n}" for n in range(1, 62)]
    tasks = get_tasks("base")
    assert {t.id for t in tasks} == {f"Bank {n}" for n in range(1, 62)}
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
    # The shared tool surface evolves. Require every exposed API to have a
    # system route and every shipped access selector to target an available API.
    assert tools_a.keys() <= TOOL_OWNERS.keys()
    for task in get_tasks():
        assert (
            task.evaluation_criteria.tool_access_policy.tool_arguments.keys()
            <= tools_a.keys()
        )
    assert {
        "file_read",
        "file_search",
        "ask_client",
        "verify_identity",
    } <= tools_a.keys()
    assert a.policy == b.policy
    assert (
        a.run_metadata["startup"]["sha256"]
        == hashlib.sha256(a.policy.encode()).hexdigest()
    )
    assert (
        a.run_metadata["startup"]["document_sha256"]
        == hashlib.sha256(
            (BANKING_RU_DATA_DIR / "corpus/AGENTS.md").read_bytes()
        ).hexdigest()
    )
    assert str(BANKING_RU_DATA_DIR.resolve()).startswith(
        str(Path(__file__).resolve().parents[3])
    )


def test_bank2_split_files_are_not_exposed_through_agent_corpus():
    from scripts.banking_ru.run_codex_harness import build_prompt
    from tau2.platform.session import PlatformSession

    task = next(task for task in get_tasks() if task.id == "Bank 2")
    folder = BANKING_RU_DATA_DIR / "tasks/bank_002"
    assert task.ticket == (folder / "ticket.md").read_bytes().decode("utf-8")
    session = PlatformSession(task, get_environment, mcp_layout="systems")
    prompt = build_prompt(policy=session.policy, ticket=task.ticket)
    assert task.ticket in prompt
    for name in ("scenario.json", "evaluation.json"):
        assert (folder / name).read_text() not in prompt
        for path in (f"/tasks/bank_002/{name}", str(folder / name)):
            assert session.call(
                "file_read", {"path": path}, system="knowledge"
            ).is_error
    assert session.env.tools.corpus.find("evaluation.json")["paths"] == []
    assert session.env.tools.corpus.find("scenario.json")["paths"] == []
