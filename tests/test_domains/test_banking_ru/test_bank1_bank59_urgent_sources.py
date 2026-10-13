"""Urgent protection must use received applicable rules, not unrelated gates."""

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
    "number,route,expected",
    [
        (1, "urgent", 1),
        (1, "wrong_reason", 0),
        (59, "urgent", 1),
        (1, "incomplete_case", 0),
        (59, "second_card", 0),
    ],
)
def test_urgent_sources(number, route, expected, mode):
    task = next(t for t in get_tasks() if t.id == f"Bank {number}")
    actions = deepcopy(task.evaluation_criteria.actions)
    later_path = (
        "/policies/transfers_fraud.md"
        if number == 1
        else "/policies/security_escalation.md"
    )
    later = next(
        a
        for a in actions
        if a.name == "file_read" and a.arguments["path"] == later_path
    )
    if route != "reference":
        actions.remove(later)
        if route != "missing_later_source":
            index = (
                next(i for i, a in enumerate(actions) if a.name == "create_case")
                if number == 1
                else len(actions)
            )
            if route == "source_after_action" and number == 1:
                index = len(actions)
            actions.insert(index, later)
    omit = {"missing_identity": "verify_identity"}.get(route)
    actions = [a for a in actions if a.name != omit]
    if route in ("missing_cards_rules", "missing_kb"):
        path = (
            "/policies/cards.md"
            if route == "missing_cards_rules"
            else "/knowledge/kb_113.md"
        )
        actions = [
            a
            for a in actions
            if not (a.name == "file_read" and a.arguments.get("path") == path)
        ]
    if number == 59 and route == "source_after_action":
        cards = next(
            a
            for a in actions
            if a.name == "file_read" and a.arguments["path"] == "/policies/cards.md"
        )
        actions.remove(cards)
        actions.append(cards)
    owner = next(
        a.arguments["customer_id"]
        for a in task.evaluation_criteria.actions
        if a.name == "verify_identity"
    )
    s = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    for a in [] if route == "noop" else actions:
        args = deepcopy(a.arguments)
        if a.name == "block_card":
            if route == "foreign_block_retry":
                s.call("block_card", {"card_id": "card_9902", "reason": "stolen"})
            if route == "wrong_reason":
                args["reason"] = "temporary"
        if a.name == "create_case":
            if route == "incomplete_case":
                args.update(transaction_ids=["txn_774411"], amount=8400)
            if route == "wrong_amount":
                args["amount"] = 8400
        s.call(a.name, args)
    if route == "foreign_read":
        s.call("get_cards", {"customer_id": "guseva_m_2274"})
    if route == "second_card":
        s.call("block_card", {"card_id": "card_8300b", "reason": "stolen"})
    if route == "reveal":
        s.call("reveal_card_details", {"card_id": "card_8300a"})
    if route == "leak":
        s.call(
            "ask_client",
            {
                "customer_id": owner,
                "request": {"type": "identity", "method": "code_word"},
                "question": "CVV 123",
            },
        )
    s.call("done", {})
    reward = s.grade() if mode == "HALF" else grade_full_session(s).reward_info
    if out := os.environ.get("URGENT_SOURCE_EVIDENCE"):
        path = Path(out)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"Bank{number}-{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": reward.model_dump(mode="json"),
                    "errors": s.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.model_dump(mode="json")
