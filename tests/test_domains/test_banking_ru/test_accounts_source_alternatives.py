"""Exact applicable account process sources are alternatives, with unchanged timing."""

import copy

import pytest

from tau2.data_model.tasks import Action
from tests.test_domains.test_banking_ru.helpers import execute, task


@pytest.mark.parametrize("number", [30])
@pytest.mark.parametrize(
    "variant,expected",
    [("kb_only", 1), ("accounts_only", 1), ("neither", 0)],
    ids=["kb_only", "accounts_only", "neither"],
)
def test_exact_source_alternatives_whole(number, variant, expected):
    actions = copy.deepcopy(task(number).evaluation_criteria.actions)
    paths = {"/knowledge/kb_128.md", "/policies/accounts.md"}
    source = (
        "/policies/accounts.md"
        if variant == "accounts_only"
        else "/knowledge/kb_128.md"
    )
    actions = [
        a
        for a in actions
        if not (
            a.name == "file_read"
            and a.arguments.get("path") in paths
            and a.arguments.get("path") != source
        )
    ]
    if variant == "neither":
        actions = [
            a
            for a in actions
            if not (a.name == "file_read" and a.arguments.get("path") in paths)
        ]
    if variant in ["late_kb", "failed_kb_then_late_retry"]:
        a = next(
            a
            for a in actions
            if a.name == "file_read" and a.arguments.get("path") == source
        )
        actions.remove(a)
        i = next(
            i for i, a in enumerate(actions) if a.action_id == f"sign_Bank {number}"
        )
        actions.insert(i + 1, a)
        if variant == "failed_kb_then_late_retry":
            actions.insert(
                i,
                Action(
                    action_id="failed-source",
                    name="file_read",
                    arguments={"path": source, "start_line": 201, "end_line": 201},
                ),
            )
    if variant == "partial_kb":
        next(
            a
            for a in actions
            if a.name == "file_read" and a.arguments.get("path") == source
        ).arguments.update(start_line=1, end_line=1)
    if number == 30:
        i = next(
            i
            for i, a in enumerate(actions)
            if a.action_id == "closure_operations_Bank 30"
        )
        actions[i] = Action(
            action_id=actions[i].action_id,
            name="check_account_closure",
            arguments={"account_id": "acc_9034", "payout_account_id": "acc_1180"},
        )
    s = execute(number, actions)
    half = s.grade()
    import json
    import os
    import pathlib

    if os.environ.get("SOURCE_OR_LOG_DIR"):
        p = pathlib.Path(os.environ["SOURCE_OR_LOG_DIR"])
        p.mkdir(exist_ok=True, parents=True)
        payload = json.dumps(
            {
                "number": number,
                "variant": variant,
                "expected": expected,
                "pid": os.getpid(),
                "session_id": s.token,
                "half": half.model_dump(mode="json"),
                "errors": s.errors,
                "messages": [m.model_dump(mode="json") for m in s.trajectory()],
            },
            ensure_ascii=False,
            indent=2,
        )
        for a in actions:
            if a.name == "verify_identity":
                payload = payload.replace(a.arguments["credential"], "[MASKED]")
        (p / f"Bank-{number}-{variant}.json").write_text(payload)
    assert half.reward == expected
