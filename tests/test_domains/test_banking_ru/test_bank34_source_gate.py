"""Addressed latest-main Bank34 operative-source equivalence gate."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.tool_contract_support import (
    escalation_arguments,
)
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("canonical", 1),
        ("without_loans", 1),
        ("specialist_without_loans", 1),
        ("missing_kb168", 0),
        ("missing_kb345", 0),
        ("early_write_before_kb345", 0),
    ],
)
def test_bank34_source_gate(mode, route, expected):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 34"))
    actions = deepcopy(task.evaluation_criteria.actions)
    omitted = set() if route == "canonical" else {"/policies/loans.md"}
    if route == "missing_kb168":
        omitted.add("/knowledge/kb_168.md")
    if route == "missing_kb345":
        omitted.add("/knowledge/kb_345.md")
    actions = [
        a
        for a in actions
        if not (a.name == "file_read" and a.arguments["path"] in omitted)
    ]
    if route == "specialist_without_loans":
        action = next(a for a in actions if a.name == "create_case")
        action.name = "escalate_to_human"
        action.arguments = escalation_arguments(34, "smirnova_i_3390")
    if route == "early_write_before_kb345":
        source = next(
            a
            for a in actions
            if a.name == "file_read" and a.arguments["path"] == "/knowledge/kb_345.md"
        )
        actions.remove(source)
        actions.append(source)
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=20)
    session.replay_actions(actions)
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if os.getenv("BANK34_RECEIPTS"):
        folder = Path(os.environ["BANK34_RECEIPTS"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "actual": reward.final_reward,
                    "components": reward.reward_breakdown,
                    "errors": session.errors,
                    "task_id": task.id,
                    "pid": os.getpid(),
                    "session": session.token,
                    "grading_info": reward.info,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info
