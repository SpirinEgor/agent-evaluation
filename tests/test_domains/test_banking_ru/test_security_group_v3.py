"""Selection, scope and mixed access outcomes; actual API effects plus full grade."""

import json
from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize(
    "number,route,expected",
    [
        (1, "include_recognized", 0),
        (1, "single_link", 0),
        (1, "omit_amount", 1),
        (46, "no_order_selection", 1),
        (46, "declined_then_correct", 1),
        (47, "preserve_all_blocks", 0),
        (47, "restore_before_recognition", 0),
        (47, "restore_pending_too", 0),
        (47, "restore_unknown_too", 0),
        (47, "restore_before_case", 1),
    ],
)
def test_selection_scope(number, route, expected):
    task = next(t for t in get_tasks() if t.id == f"Bank {number}")
    s = PlatformSession(task, get_environment, max_calls=250, max_errors=0)
    actions = deepcopy(task.evaluation_criteria.actions)
    restore = next(
        (a for a in actions if a.action_id == "restore_clear_device_Bank 47"), None
    )
    if route in ("restore_before_case", "restore_before_recognition"):
        actions.remove(restore)
        target = (
            "create_case" if route == "restore_before_case" else "ask_client_1_Bank 47"
        )
        i = next(
            i
            for i, a in enumerate(actions)
            if a.name == target or a.action_id == target
        )
        actions.insert(i, restore)
    for a in actions:
        if a.name in ("file_read", "file_search"):
            continue
        if route == "no_order_selection" and a.action_id == "select_order_Bank 46":
            continue
        if (
            route == "preserve_all_blocks"
            and a.action_id == "restore_clear_device_Bank 47"
        ):
            continue
        args = deepcopy(a.arguments)
        if a.name == "create_case" and number == 1:
            if route == "include_recognized":
                args.update(
                    transaction_ids=["txn_774411", "txn_774412", "txn_774413"],
                    amount=29400,
                )
            if route == "single_link":
                args.pop("transaction_ids")
            if route == "omit_amount":
                args.pop("amount")
        if route == "declined_then_correct" and a.action_id == "select_order_Bank 46":
            s.call(
                "ask_client",
                {
                    "customer_id": "kovalev_n_2217",
                    "transaction_id": "txn_221702",
                    "request": {"type": "operation_confirmation", "amount": 48500},
                },
            )
            result = json.loads(s.log[-1][1].content)
            assert result["status"] == "declined"
            assert "confirmation_id" not in result
        result = s.call(a.name, args)
        assert not result.is_error
    if route in ("restore_pending_too", "restore_unknown_too"):
        result = s.call(
            "unblock_device",
            {"device_id": "dev_8821" if route == "restore_pending_too" else "dev_8822"},
        )
        assert not result.is_error  # The scorer, not the API, catches this.
    s.call("done", {})
    for r in (s.grade(), grade_full_session(s).reward_info):
        assert r.final_reward == expected, r.model_dump(mode="json")
    assert s.errors == 0
