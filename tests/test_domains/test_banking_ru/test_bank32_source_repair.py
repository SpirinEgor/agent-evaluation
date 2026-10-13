"""Deposit payout investigation must not depend on account closure procedures."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.data_model.tasks import Action
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["HALF"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("singleton", 1),
        ("category_other", 1),
        ("missing_contract", 0),
        ("missing_account", 0),
        ("missing_payment", 0),
        ("historical_payment", 0),
    ],
)
def test_bank32_source_repair(mode, route, expected):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 32"))
    actions = deepcopy(task.evaluation_criteria.actions)
    # Include the applicable deposit procedure even before the data repair.
    if not any(a.arguments.get("path") == "/policies/deposits.md" for a in actions):
        actions.insert(
            0,
            Action(
                action_id="deposit_rules",
                name="file_read",
                arguments={"path": "/policies/deposits.md"},
            ),
        )
    omitted = set() if route == "canonical" else {"/policies/accounts.md"}
    if route == "missing_deposits":
        omitted.add("/policies/deposits.md")
    if route == "missing_kb350":
        omitted.add("/knowledge/kb_350.md")
    actions = [a for a in actions if a.arguments.get("path") not in omitted]
    sources = {
        "contract": "get_deposit",
        "account": "get_accounts",
        "payment": "get_transactions",
    }
    for source, name in sources.items():
        if route == f"missing_{source}":
            actions = [a for a in actions if a.name != name]
        if route == f"early_{source}":
            read = next(a for a in actions if a.name == name)
            actions.remove(read)
            actions.append(read)
    case = next(a for a in actions if a.name == "create_case")
    changes = {
        "singleton": {"transaction_id": None, "transaction_ids": ["txn_405140"]},
        "category_other": {"category": "other"},
        "wrong_payment": {"transaction_id": "txn_405139"},
        "historical_payment": {"transaction_id": "txn_405120", "amount": 3000.0},
        "wrong_amount": {"amount": 3300.0},
        "wrong_unit": {"responsible_unit": "credit_support"},
        "wrong_date": {"next_contact_date": "2026-09-01"},
        "wrong_channel": {"notification_channel": "sms"},
    }
    case.arguments.update(changes.get(route, {}))
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=20)
    if route != "noop":
        session.replay_actions(actions)
    if route == "manual_transfer":
        session.call(
            "transfer_between_own_accounts",
            {
                "from_account_id": "acc_8844",
                "to_account_id": "acc_8843",
                "amount": 3200.0,
            },
        )
    if route == "duplicate_case":
        session.call("create_case", case.arguments)
    if route == "close_deposit":
        session.call(
            "close_deposit", {"deposit_id": "dep_4051", "payout_account_id": "acc_8844"}
        )
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if os.getenv("BANK32_RECEIPTS"):
        folder = Path(os.environ["BANK32_RECEIPTS"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "actual": reward.final_reward,
                    "reward": reward.model_dump(mode="json"),
                    "errors": session.errors,
                    "pid": os.getpid(),
                    "session": session.token,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info
