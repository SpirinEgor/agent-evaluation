"""Bank41 exact operative N40 sources replace the redundant rewards policy."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["HALF"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("atomic_only", 1),
        ("bundle_only", 1),
        ("overview_only", 0),
        ("partial_bundle", 0),
    ],
    ids=["atomic_only", "bundle_only", "overview_only", "partial_bundle"],
)
def test_bank41_reward_source(mode, route, expected):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 41"))
    actions = deepcopy(task.evaluation_criteria.actions)
    if route != "reference":
        actions = [
            a
            for a in actions
            if not (
                a.name == "file_read"
                and a.arguments.get("path") == "/policies/fees_rewards.md"
            )
        ]
    if route in ["bundle_only", "overview_only", "partial_bundle", "unrelated"]:
        actions = [
            a
            for a in actions
            if not (
                a.name == "file_read"
                and a.arguments.get("path", "").startswith("/knowledge/kb_3")
            )
        ]
        if route != "overview_only":
            source = deepcopy(
                next(
                    a
                    for a in actions
                    if a.name == "file_read"
                    and a.arguments.get("path") == "/knowledge/kb_final_41.md"
                )
            )
            source.arguments = {
                "path": "/knowledge/kb_final_41_part_1.md"
                if route != "unrelated"
                else "/knowledge/kb_final_44.md"
            }
            if route == "partial_bundle":
                source.arguments.update(start_line=1, end_line=1)
            actions.insert(0, source)
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    if route == "early_private":
        session.call("get_accounts", {"customer_id": "egorova_n_3358"})
    if route != "noop":
        session.replay_actions(actions)
    if route == "unauthorized_reward":
        session.call("grant_cashback", {"transaction_id": "txn_077031", "amount": 130})
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if dest := os.environ.get("BANK41_EVIDENCE"):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "route": route,
                "mode": mode,
                "expected": expected,
                "reward": reward.model_dump(mode="json"),
                "errors": session.errors,
                "calls": [
                    {
                        "call": c.model_dump(mode="json"),
                        "result": r.model_dump(mode="json"),
                    }
                    for c, r in session.log
                ],
            },
            ensure_ascii=False,
            indent=2,
        ).replace("мускат", "[MASKED]")
        (path / f"{route}-{mode}.json").write_text(payload)
    assert reward.final_reward == expected, reward.info
