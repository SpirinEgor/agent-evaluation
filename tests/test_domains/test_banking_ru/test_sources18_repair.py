"""Scoped full-grader counterexamples for sources18 repairs."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize(
    "task_id,route,expected",
    [
        ("Bank 20", "alternative", 1),
        ("Bank 35", "alternative", 1),
        ("Bank 53", "alternative", 1),
        ("Bank 56", "alternative", 1),
    ],
)
@pytest.mark.parametrize("mode", ["HALF"])
def test_sources18(task_id, mode, route, expected):
    task = next(t for t in get_tasks() if t.id == task_id)
    actions = deepcopy(task.evaluation_criteria.actions)
    omit = {
        "Bank 20": {"/policies/disputes.md"},
        "Bank 35": {"/policies/loans.md"},
        "Bank 53": {"/policies/deposits.md", "/policies/loans.md"},
    }.get(task_id, set())
    if route == "alternative":
        actions = [
            a
            for a in actions
            if not (a.name == "file_read" and a.arguments.get("path") in omit)
        ]
        if task_id in ["Bank 41", "Bank 53"]:
            paths = []
            for a in actions:
                p = a.arguments.get("path", "")
                if a.name == "file_read" and (
                    p.startswith("/knowledge/kb_3")
                    or task_id == "Bank 53"
                    and p in ["/knowledge/kb_167.md", "/knowledge/kb_168.md"]
                ):
                    paths.append(a)
            for a in paths:
                actions.remove(a)
            meta = json.load(open(BANKING_RU_DATA_DIR / "manifest.json"))["documents"][
                f"/knowledge/kb_final_{task_id.split()[1]}.md"
            ]["metadata"]
            index = (
                next(
                    i
                    for i, a in enumerate(actions)
                    if a.name == "file_read"
                    and a.arguments.get("path")
                    == f"/knowledge/kb_final_{task_id.split()[1]}.md"
                )
                + 1
            )
            template = deepcopy(actions[index - 1])
            for p in reversed(meta["related_bundle_paths"]):
                a = deepcopy(template)
                a.arguments = {"path": p}
                actions.insert(index, a)
    if route == "missing_source":
        missing = {
            "Bank 20": "/policies/transfers_fraud.md",
            "Bank 35": "/knowledge/kb_168.md",
            "Bank 41": "/knowledge/kb_320.md",
            "Bank 53": "/knowledge/kb_340.md",
            "Bank 56": "/knowledge/kb_110.md",
            "Bank 57": "/policies/subscriptions.md",
        }[task_id]
        actions = [
            a
            for a in actions
            if not (a.name == "file_read" and a.arguments.get("path") == missing)
        ]
    s = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    if route == "early_private":
        cid = task.evaluation_criteria.tool_access_policy.allowed_customer_ids[0]
        s.call("get_accounts", {"customer_id": cid})
    if route != "noop":
        s.replay_actions(actions)
    s.call("done", {})
    result = s.grade() if mode == "HALF" else grade_full_session(s).reward_info
    reward = result.final_reward
    if output := os.environ.get("SOURCES18_EVIDENCE"):
        p = Path(output)
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{task_id.replace(' ', '-')}-{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "route": route,
                    "mode": mode,
                    "reward": result.model_dump(mode="json"),
                    "calls": [
                        {
                            "call": c.model_dump(mode="json"),
                            "result": r.model_dump(mode="json"),
                        }
                        for c, r in s.log
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward == expected


@pytest.mark.parametrize("mode", ["HALF"])
@pytest.mark.parametrize(
    "task_id,route",
    [
        ("Bank 35", "no_consent"),
        ("Bank 35", "late_source"),
        ("Bank 53", "wrong_calculation"),
    ],
)
def test_sources18_material_wrong_route(task_id, route, mode):
    task = next(t for t in get_tasks() if t.id == task_id)
    s = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    actions = deepcopy(task.evaluation_criteria.actions)
    delayed = None
    if route == "no_consent":
        actions = [a for a in actions if a.action_id != "confirm_repayment_Bank 35"]
    if route == "late_source":
        delayed = next(
            a
            for a in actions
            if a.name == "file_read"
            and a.arguments.get("path") == "/knowledge/kb_168.md"
        )
        actions.remove(delayed)
    if route == "wrong_calculation":
        next(a for a in actions if a.action_id == "calculate_2_Bank 53").arguments = {
            "expression": "1+1"
        }
    s.replay_actions(actions)
    if delayed:
        s.call(delayed.name, delayed.arguments)
    s.call("done", {})
    reward = s.grade() if mode == "HALF" else grade_full_session(s).reward_info
    if output := os.environ.get("SOURCES18_EVIDENCE"):
        (Path(output) / f"{task_id.replace(' ', '-')}-{route}-{mode}.json").write_text(
            json.dumps(reward.model_dump(mode="json"), ensure_ascii=False, indent=2)
        )
    assert reward.final_reward == 0
