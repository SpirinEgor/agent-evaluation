"""Bank40: a received fee refund inherits the original transaction's owner."""

import json
import os
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def make_session(route):
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    if route == "noop":
        return session
    for action in task.evaluation_criteria.actions:
        if route == "missing_identity" and action.name == "verify_identity":
            continue
        args = dict(action.arguments)
        if action.name == "refund_fee" and route.startswith("excess"):
            session.call(action.name, {**args, "amount": 199})
            if route == "excess_199":
                continue
        session.call(action.name, args)
    if route in {"refund_details", "refund_details_repeat"}:
        refund = next(r for c, r in session.log if c.name == "refund_fee")
        payload = json.loads(refund.content)
        assert float(payload["refunded"]) == 169
        assert payload["original_transaction_id"] == "txn_088219"
        session.call(
            "get_transaction_details",
            {"transaction_id": payload["refund_transaction_id"]},
        )
    if route in {"foreign_read", "unknown_retry"}:
        policy = task.evaluation_criteria.tool_access_policy
        foreign = next(
            ids[0]
            for owner, ids in policy.resources_by_customer["transactions"].items()
            if owner not in policy.allowed_customer_ids and ids
        )
        session.call(
            "get_transaction_details",
            {"transaction_id": foreign if route == "foreign_read" else "txn_invented"},
        )
    return session


@pytest.mark.parametrize("mode", ["HALF"])
@pytest.mark.parametrize(
    "route,expected",
    [("refund_details", 1), ("excess_then_correct", 0), ("unknown_retry", 1)],
    ids=["refund_details", "excess_then_correct", "unknown_retry"],
)
def test_bank40_routes(route, expected, mode):
    session = make_session(route)
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if dest := os.environ.get("BANK40_EVIDENCE"):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": reward.model_dump(mode="json"),
                    "errors": session.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info
