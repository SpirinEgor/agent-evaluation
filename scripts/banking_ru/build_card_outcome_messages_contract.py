"""Emit scoped message rules for the two declared dual-card outcome templates."""

import argparse
import difflib
import json

from build_confidentiality_contract import DATA, PREFIXES, TASK_FILE, render_contract

from tau2.domains.banking_ru.card_claims import (
    card_hold_message_patterns,
    dual_card_message_patterns,
)
from tau2.domains.banking_ru.limit_claims import limit_message_patterns

ASSERTIONS = {"assert_dual_card_debt_reply", "assert_card_hold_composite_reply"}


def eligible(task: dict) -> bool:
    """Select explicitly declared outcome invariants, not every card task."""
    return any(
        a["func_name"] in ASSERTIONS
        for a in task["evaluation_criteria"].get("env_assertions", [])
    )


def with_contract(tasks: list[dict], db: dict) -> list[dict]:
    """Derive entities from each template and preserve all other obligations."""
    for task in tasks:
        if not eligible(task):
            continue
        criteria = task["evaluation_criteria"]
        assertions = [
            a for a in criteria["env_assertions"] if a["func_name"] in ASSERTIONS
        ]
        if len(assertions) != 1:
            raise ValueError("Expected one declared dual-card outcome template")
        assertion = assertions[0]
        arguments = assertion["arguments"]
        owner = arguments["customer_id"]
        cards = {k: v for k, v in db["cards"].items() if v["customer_id"] == owner}
        selected = {}
        for role in ["lost", "temporary"]:
            matches = [
                k for k, v in cards.items() if v["last4"] == arguments[role + "_last4"]
            ]
            if len(matches) != 1:
                raise ValueError(
                    "Card outcome needs one owned card for each declared last4"
                )
            selected[role] = matches[0]
        if selected["lost"] == selected["temporary"]:
            raise ValueError("Lost and temporary cards must be distinct")
        temporary = cards[selected["temporary"]]
        if (
            temporary["status"] != "blocked"
            or temporary.get("block_reason") != "temporary"
        ):
            raise ValueError("Temporary-card template needs an initial temporary block")
        if any(cards[k].get("reissue_status") is not None for k in selected.values()):
            raise ValueError("These templates do not describe an existing reissue")
        actions = criteria["actions"]
        if any(a["name"] in {"set_limit", "reissue_card"} for a in actions):
            raise ValueError(
                "These outcome templates cannot declare a reference limit write or reissue"
            )
        unblock = [a for a in actions if a["name"] == "unblock_card"]
        if (
            len(unblock) != 1
            or unblock[0]["arguments"].get("card_id") != selected["temporary"]
            or unblock[0].get("requestor", "assistant") != "assistant"
        ):
            raise ValueError("Expected exactly the declared temporary-card unblock")
        block = [
            a
            for a in actions
            if a["name"] == "block_card"
            and a["arguments"].get("card_id") == selected["lost"]
            and a["arguments"].get("reason") == "lost"
            and a.get("requestor", "assistant") == "assistant"
        ]
        lost = cards[selected["lost"]]
        initially_lost = (
            lost["status"] == "blocked" and lost.get("block_reason") == "lost"
        )
        if not initially_lost and (lost["status"] != "active" or len(block) != 1):
            raise ValueError(
                "Lost card must be initially blocked or have one declared agent loss block"
            )
        if any(k not in db.get("card_limits", {}) for k in cards):
            raise ValueError("Every owned card needs its actual monetary limits")
        values = {
            (
                db["card_limits"][k]["daily_cash_withdrawal"],
                db["card_limits"][k]["sbp_limit"],
            )
            for k in cards
            if k in db.get("card_limits", {})
        }
        if len(values) != 1:
            raise ValueError(
                "Missing or heterogeneous monetary limits require explicit scoping"
            )
        patterns = limit_message_patterns(*next(iter(values)))
        if assertion["func_name"] == "assert_dual_card_debt_reply":
            patterns.update(
                dual_card_message_patterns(
                    arguments["lost_last4"], arguments["temporary_last4"]
                )
            )
        else:
            releases = [a for a in actions if a["name"] == "release_hold"]
            old_ids = {
                a["arguments"]["transaction_id"]
                for a in criteria["env_assertions"]
                if a["func_name"] == "assert_transaction_status"
                and a["arguments"].get("expected_status") == "declined"
            }
            fresh_ids = {
                a["arguments"]["transaction_id"]
                for a in criteria["env_assertions"]
                if a["func_name"] == "assert_hold_intact"
            }
            if (
                len(old_ids) != 1
                or len(fresh_ids) != 1
                or old_ids & fresh_ids
                or len(releases) != 1
                or releases[0]["arguments"].get("transaction_id") not in old_ids
                or releases[0].get("requestor", "assistant") != "assistant"
            ):
                raise ValueError(
                    "Hold template must release only its declared old hold"
                )
            for ids, role in [(old_ids, "temporary"), (fresh_ids, "lost")]:
                transaction = db.get("transactions", {}).get(next(iter(ids)), {})
                if (
                    transaction.get("card_id") != selected[role]
                    or transaction.get("customer_id") != owner
                    or transaction.get("status") != "hold"
                ):
                    raise ValueError(
                        "Hold invariants need actual owned initial holds on the declared cards"
                    )
            patterns.update(
                card_hold_message_patterns(
                    arguments["lost_last4"],
                    arguments["temporary_last4"],
                    arguments["fresh_hold_date"],
                )
            )
        own = {task["id"] + "_" + name for name in patterns}
        rules = [
            r
            for r in criteria.get("forbidden_message_patterns", [])
            if r["rule_id"] not in own
        ]
        additions = [
            {
                "rule_id": task["id"] + "_" + name,
                "pattern": pattern,
                "native_text_scope": "utterance",
                "tool_arguments": {
                    "ask_client": ["question"],
                    "reply_to_ticket": ["text"],
                },
                **(
                    {"only_after_action_id": unblock[0]["action_id"]}
                    if name == "no_temporary_card_blocked"
                    else {"only_after_action_id": block[0]["action_id"]}
                    if name == "no_lost_card_active" and not initially_lost
                    else {}
                ),
            }
            for name, pattern in patterns.items()
        ]
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
        raise ValueError("Task does not declare a dual-card outcome template")
    with_contract(selected, json.loads((DATA / "db.json").read_text(encoding="utf-8")))
    if args.check:
        if json.loads(original) != tasks:
            print(
                "Card outcome message contract is stale; regenerate and apply its patch."
            )
            return 1
        print(f"Card outcome message contract is current for {len(selected)} tasks.")
        return 0
    lines = []
    for task in selected:
        updated = [task if t["id"] == task["id"] else t for t in json.loads(original)]
        rendered = render_contract(
            original, updated, {task["id"]}, fields=("forbidden_message_patterns",)
        )
        anchored = False
        for line in list(
            difflib.unified_diff(original.splitlines(), rendered.splitlines(), n=3)
        )[2:]:
            if line.startswith("@@"):
                lines.append(f'@@     "id": "{task["id"]}",' if not anchored else "@@")
                anchored = True
            else:
                lines.append(line)
    if lines:
        print("*** Begin Patch\n*** Update File: " + str(TASK_FILE))
        print("\n".join(lines))
        print("*** End Patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
