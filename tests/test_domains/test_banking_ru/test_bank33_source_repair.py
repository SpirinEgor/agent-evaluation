"""Bank33 exact closure sources and substantive protected paths, HALF/FULL."""

import copy
import json
import os
from pathlib import Path

import pytest

from tests.test_domains.test_banking_ru.helpers import execute, task


@pytest.mark.parametrize(
    "route,expected",
    [
        ("accounts_only", 1),
        ("kb_only", 1),
        ("missing_signature", 0),
        ("missing_readiness", 0),
        ("early_failed_attempt", 0),
    ],
    ids=[
        "accounts_only",
        "kb_only",
        "missing_signature",
        "missing_readiness",
        "early_failed_attempt",
    ],
)
def test_bank33_source_repair(route, expected):
    actions = copy.deepcopy(task(33).evaluation_criteria.actions)
    paths = {"/policies/accounts.md", "/knowledge/kb_128.md"}
    if route in {
        "accounts_only",
        "kb_only",
        "unrelated",
        "partial",
        "late",
        "early_failed_attempt",
    }:
        keep = "/knowledge/kb_128.md" if route == "kb_only" else "/policies/accounts.md"
        actions = [
            a
            for a in actions
            if not (a.name == "file_read" and a.arguments.get("path") in paths - {keep})
        ]
        source = next(
            a
            for a in actions
            if a.name == "file_read" and a.arguments.get("path") == keep
        )
        if route == "unrelated":
            source.arguments = {"path": "/policies/cards.md"}
        if route == "partial":
            source.arguments.update(start_line=1, end_line=1)
        if route == "late":
            actions.remove(source)
            actions.append(source)
        if route == "early_failed_attempt":
            early = copy.deepcopy(
                next(a for a in actions if a.name == "request_account_closure")
            )
            early.action_id = "early-failed"
            actions.insert(0, early)
    if route == "noop":
        actions = []
    if route == "missing_signature":
        actions = [a for a in actions if a.action_id != "sign_Bank 33"]
    if route == "missing_readiness":
        actions = [a for a in actions if a.name != "check_account_closure"]
    s = execute(33, actions)
    h = s.grade()
    if os.getenv("BANK33_RECEIPTS"):
        p = Path(os.environ["BANK33_RECEIPTS"])
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{route}.json").write_text(
            json.dumps(
                {
                    "route": route,
                    "expected": expected,
                    "pid": os.getpid(),
                    "session": s.token,
                    "half": h.model_dump(mode="json"),
                    "errors": s.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert h.reward == expected, h.info
