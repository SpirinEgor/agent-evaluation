"""Emit one runtime numeric-message contract for every banking task.

Initial settings are per owned card; successful received writes update only
their own channel. Reference financial operations and old contracts stay intact.
"""

import argparse
import difflib
import json
import re

from build_confidentiality_contract import DATA, TASK_FILE

from tau2.domains.banking_ru.limit_claims import numeric_limit_claim_patterns


def with_contract(tasks: list[dict], db: dict) -> list[dict]:
    """Derive actual resource aliases/settings without copying a reference amount."""
    for task in tasks:
        criteria = task["evaluation_criteria"]
        # A completion-only contract has no final-prose assertions. This
        # selection uses the declared contract, never a task ID.
        if criteria.get("completion_mode") == "completion_only" or any(
            a["name"] == "reply_to_ticket" and a.get("compare_args") == []
            for a in criteria.get("forbidden_actions", [])
        ):
            criteria["numeric_message_claims"] = []
            continue
        identities = [
            action
            for action in criteria["actions"]
            if action["name"] == "verify_identity"
        ]
        if len(identities) != 1:
            raise ValueError("Expected one declared customer identity")
        owner = identities[0]["arguments"]["customer_id"]
        cards = {
            key: card
            for key, card in db["cards"].items()
            if card["customer_id"] == owner
        }
        additions = []
        for name, (pattern, field, completed) in numeric_limit_claim_patterns().items():
            stored_field = "sbp_limit" if field == "sbp" else field
            values = {
                key: db["card_limits"][key][stored_field]
                for key in cards
                if key in db.get("card_limits", {})
                and db["card_limits"][key].get(stored_field) is not None
            }
            aliases = {}
            for key in values:
                aliases.setdefault(cards[key]["last4"], []).append(key)
            additions.append(
                {
                    "rule_id": "common_limit_" + name,
                    "pattern": pattern,
                    "initial_values": values,
                    "resource_aliases": aliases,
                    "resource_pattern": r"\bкарт[аыуе]\s+(?:\*|…|\.\.\.)?(?P<resource>\d{4})\b",
                    "value_aliases": {
                        "миллион": 1000000,
                        "миллиона": 1000000,
                        "миллионов": 1000000,
                    },
                    "update_tool": "set_limit",
                    "update_arguments": {"limit_type": field},
                    "resource_argument": "card_id",
                    "value_argument": "amount",
                    "result_resource_field": "card_id",
                    "result_value_field": stored_field,
                    "require_update": completed,
                    **({"witness_capture": "witness"} if completed else {}),
                    "tool_arguments": {
                        "ask_client": ["question"],
                        "reply_to_ticket": ["text"],
                    },
                }
            )
        criteria["numeric_message_claims"] = [
            rule
            for rule in criteria.get("numeric_message_claims", [])
            if not rule["rule_id"].startswith("common_limit_")
        ] + additions
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--task-id")
    args = parser.parse_args()
    original = TASK_FILE.read_text(encoding="utf-8")
    tasks = json.loads(original)
    selected = (
        tasks
        if args.task_id is None
        else [task for task in tasks if task["id"] == args.task_id]
    )
    if not selected:
        raise ValueError("Unknown task ID")
    with_contract(selected, json.loads((DATA / "db.json").read_text(encoding="utf-8")))
    if args.check:
        if json.loads(original) != tasks:
            print(
                "Numeric limit message contract is stale; regenerate and apply its patch."
            )
            return 1
        print(f"Numeric limit message contract is current for {len(selected)} tasks.")
        return 0
    lines = []
    decoder = json.JSONDecoder()
    position = original.index("[") + 1
    updates = {task["id"]: task for task in selected}
    while True:
        while original[position].isspace() or original[position] == ",":
            position += 1
        if original[position] == "]":
            break
        old, end = decoder.raw_decode(original, position)
        block = original[position:end]
        position = end
        if old["id"] not in updates:
            continue
        task = updates[old["id"]]
        value = task["evaluation_criteria"]["numeric_message_claims"]
        if old["evaluation_criteria"].get("numeric_message_claims") == value:
            continue
        rendered = '      "numeric_message_claims": ' + json.dumps(
            value, ensure_ascii=False, indent=2
        ).replace("\n", "\n      ")
        existing = re.search(r'(?m)^      "numeric_message_claims":\s*', block)
        if existing is None:
            lines.append(f'@@     "id": "{task["id"]}",')
            lines.extend("+" + line for line in (rendered + ",").splitlines())
            lines.append('       "actions": [')
            continue
        _, field_end = decoder.raw_decode(block, existing.end())
        if block[field_end : field_end + 1] == ",":
            field_end += 1
            rendered += ","
        old_field = block[existing.start() : field_end]
        anchored = False
        for line in list(
            difflib.unified_diff(old_field.splitlines(), rendered.splitlines(), n=3)
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
