"""Emit scoped message contracts for declared pending-limit scenarios only."""

import argparse
import difflib
import json

from build_confidentiality_contract import DATA, PREFIXES, TASK_FILE, render_contract

from tau2.domains.banking_ru.limit_claims import limit_message_patterns

ASSERTIONS = {
    "assert_pending_limit_reply",
    "assert_two_pending_limits_reply",
    "assert_autopayment_pending_limit_reply",
}


def eligible(task: dict) -> bool:
    """Recognize the explicit existing policy invariant, not a hardcoded task ID."""
    return any(
        a["func_name"] in ASSERTIONS
        for a in task["evaluation_criteria"].get("env_assertions", [])
    )


def with_contract(tasks: list[dict], db: dict) -> list[dict]:
    """Preserve oracle/state and derive current amounts separately for each customer."""
    for task in tasks:
        if not eligible(task):
            continue
        criteria = task["evaluation_criteria"]
        actions = criteria["actions"]
        if any(a["name"] == "set_limit" for a in actions):
            raise ValueError("A pending-limit refusal cannot use a reference set_limit")
        replies = [a for a in actions if a["name"] == "reply_to_ticket"]
        if len(replies) != 1:
            raise ValueError("Expected one final ticket reply")
        customer_id = replies[0]["arguments"]["customer_id"]
        values = {
            (
                db["card_limits"][card_id]["daily_cash_withdrawal"],
                db["card_limits"][card_id]["sbp_limit"],
            )
            for card_id, card in db["cards"].items()
            if card["customer_id"] == customer_id
            and card_id in db.get("card_limits", {})
        }
        if len(values) != 1:
            raise ValueError(
                "Missing or heterogeneous own limits require explicit card scoping"
            )
        patterns = limit_message_patterns(*next(iter(values)))
        own_rule_ids = {task["id"] + "_" + name for name in patterns}
        rules = [
            r
            for r in criteria.get("forbidden_message_patterns", [])
            if r["rule_id"] not in own_rule_ids
        ]
        additions = [
            {
                "rule_id": task["id"] + "_" + name,
                "native_text_scope": "utterance",
                "pattern": pattern,
                "tool_arguments": {
                    "ask_client": ["question"],
                    "reply_to_ticket": ["text"],
                },
            }
            for name, pattern in patterns.items()
        ]
        # Privacy regenerates its own common rules at the end of this list.
        # Keep task-specific rules before that tail so both builders converge.
        position = next(
            (i for i, r in enumerate(rules) if r["rule_id"].startswith(PREFIXES)),
            len(rules),
        )
        rules[position:position] = additions
        criteria["forbidden_message_patterns"] = rules
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--task-id")
    args = parser.parse_args()
    original = TASK_FILE.read_text(encoding="utf-8")
    tasks = json.loads(original)
    selected = [
        t
        for t in tasks
        if eligible(t) and (args.task_id is None or t["id"] == args.task_id)
    ]
    if args.task_id and not selected:
        raise ValueError("Task ID does not declare a pending-limit message contract")
    with_contract(selected, json.loads((DATA / "db.json").read_text(encoding="utf-8")))
    if args.check:
        if json.loads(original) != tasks:
            print(
                "Pending-limit message contract is stale; regenerate and apply its patch."
            )
            return 1
        print(f"Pending-limit message contract is current for {len(selected)} tasks.")
        return 0
    patch_lines = []
    for task in selected:
        # Anchor each task independently even in an all-task patch. Common
        # privacy tails can otherwise redirect an insertion to an earlier task.
        one_update = [
            task if t["id"] == task["id"] else t for t in json.loads(original)
        ]
        rendered = render_contract(
            original, one_update, {task["id"]}, fields=("forbidden_message_patterns",)
        )
        diff = list(
            difflib.unified_diff(original.splitlines(), rendered.splitlines(), n=3)
        )
        anchored = False
        for line in diff[2:]:
            if line.startswith("@@"):
                patch_lines.append(
                    f'@@     "id": "{task["id"]}",' if not anchored else "@@"
                )
                anchored = True
            else:
                patch_lines.append(line)
    if patch_lines:
        print("*** Begin Patch")
        print(f"*** Update File: {TASK_FILE}")
        print("\n".join(patch_lines))
        print("*** End Patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
