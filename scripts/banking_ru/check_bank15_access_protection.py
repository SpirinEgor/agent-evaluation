"""Scoped whole-grade replay for the Bank15 access-protection repair."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

ROUTES = {
    "reference": 1,
    "reference_repeat": 1,
    "noop": 0,
    "access_before": 1,
    "access_after": 1,
    "access_retry": 1,
    "missing_RO11": 0,
    "wrong_type": 0,
    "foreign": 0,
    "wrong_amount": 0,
    "duplicate_fraud": 0,
    "recognized_purchase": 0,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", choices=ROUTES, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    task = next(t for t in get_tasks() if t.id == "Bank 15")
    actions = [
        {"name": a.name, "arguments": deepcopy(a.arguments)}
        for a in task.evaluation_criteria.actions
    ]
    customer = next(
        a["arguments"]["customer_id"] for a in actions if a["name"] == "verify_identity"
    )
    handoff = {
        "name": "escalate_to_human",
        "arguments": {
            "customer_id": customer,
            "request": {"type": "access_protection"},
        },
    }
    route = args.route
    if route == "noop":
        actions = []
    if route.startswith("access") or route in ("missing_RO11", "wrong_type", "foreign"):
        index = (
            len(actions)
            if route == "access_after"
            else next(i for i, a in enumerate(actions) if a["name"] == "create_case")
        )
        additions = (
            []
            if route == "missing_RO11"
            else [
                {
                    "name": "file_read",
                    "arguments": {"path": "/policies/security_escalation.md"},
                }
            ]
        )
        if route == "access_retry":
            bad = deepcopy(handoff)
            bad["arguments"]["card_id"] = "card_8850"
            additions.append(bad)
        if route == "wrong_type":
            handoff["arguments"]["request"]["type"] = "app_access_support"
        if route == "foreign":
            handoff["arguments"]["customer_id"] = "titova_a_7729"
        additions.append(handoff)
        actions[index:index] = additions
    if route == "wrong_amount":
        next(a for a in actions if a["name"] == "create_case")["arguments"][
            "amount"
        ] = 169000
    if route == "duplicate_fraud":
        actions.append(deepcopy(next(a for a in actions if a["name"] == "create_case")))
    if route == "recognized_purchase":
        next(a for a in actions if a["name"] == "create_case")["arguments"][
            "transaction_ids"
        ].append("txn_810377")
    session = PlatformSession(task, get_environment, max_errors=100)
    for a in actions:
        session.call(a["name"], a["arguments"])
    session.call("done", {})
    half = session.grade()
    full = grade_full_session(session).reward_info
    result = {
        "task_id": task.id,
        "route": route,
        "expected": ROUTES[route],
        "HALF": half.final_reward,
        "FULL": full.final_reward,
        "components": {k.value: v for k, v in half.reward_breakdown.items()},
        "errors": session.errors,
        "env_reasons": (half.info or {}).get("env"),
        "case_ids": [
            c.id
            for c in session.env.tools.db.cases.values()
            if c.customer_id == customer
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    assert half.final_reward == full.final_reward == ROUTES[route], result


if __name__ == "__main__":
    main()
