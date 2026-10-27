"""Derive explicit common privacy rules from the bank's deterministic secrets.

Print an apply_patch patch; do not write task files. With --check, fail when
the persisted contract is stale. Existing task-specific rules are preserved.
"""

import argparse
import difflib
import json
import re
from pathlib import Path

from tau2.domains.banking_ru.task_store import (
    is_task_index,
    read_task_text,
    render_task_patch,
)
from tau2.evaluator.banking_confidentiality import (
    DIGIT_WORD_ALIASES,
    PREFIXES,
    common_patterns,
)

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
TASK_FILE = DATA / "tasks.json"


def with_contract(tasks: list[dict], db: dict) -> list[dict]:
    """Render legacy rules; opted-in tasks use the runtime shared contract."""
    patterns = common_patterns(db)
    channels = {"ask_client": ["question"], "reply_to_ticket": ["text"]}
    for task in tasks:
        criteria = task["evaluation_criteria"]
        rules = [
            rule
            for rule in criteria.get("forbidden_argument_patterns", [])
            if not rule["rule_id"].startswith(PREFIXES)
        ]
        if criteria.get("banking_confidentiality_contract") == "banking_v1":
            if "forbidden_argument_patterns" in criteria:
                criteria["forbidden_argument_patterns"] = rules
            if "forbidden_message_patterns" in criteria:
                criteria["forbidden_message_patterns"] = [
                    rule
                    for rule in criteria["forbidden_message_patterns"]
                    if not rule["rule_id"].startswith(PREFIXES)
                ]
            continue
        for name, pattern in patterns.items():
            for tool, arguments in channels.items():
                for argument in arguments:
                    rules.append(
                        {
                            "rule_id": name + "_" + tool,
                            "tool_name": tool,
                            "argument_name": argument,
                            "pattern": pattern,
                            "digit_word_aliases": DIGIT_WORD_ALIASES,
                        }
                    )
        criteria["forbidden_argument_patterns"] = rules
        messages = [
            rule
            for rule in criteria.get("forbidden_message_patterns", [])
            if not rule["rule_id"].startswith(PREFIXES)
        ]
        criteria["forbidden_message_patterns"] = [
            *messages,
            *(
                {
                    "rule_id": name + "_message",
                    "pattern": pattern,
                    "digit_word_aliases": DIGIT_WORD_ALIASES,
                    "tool_arguments": channels,
                    "all_tool_arguments": True,
                }
                for name, pattern in patterns.items()
            ),
        ]
    return tasks


def render_contract(
    original: str,
    updated: list[dict],
    selected_ids: set[str],
    fields=("forbidden_argument_patterns", "forbidden_message_patterns"),
) -> str:
    """Replace selected criteria fields, retaining unrelated hand-formatted JSON."""
    decoder = json.JSONDecoder()
    position = original.index("[") + 1
    replacements = []
    while True:
        while original[position].isspace() or original[position] == ",":
            position += 1
        if original[position] == "]":
            break
        old_task, end = decoder.raw_decode(original, position)
        if old_task["id"] in selected_ids:
            criteria = next(
                task["evaluation_criteria"]
                for task in updated
                if task["id"] == old_task["id"]
            )
            text = original[position:end]
            for field in fields:
                if field not in criteria:
                    continue
                value = json.dumps(
                    criteria[field], ensure_ascii=False, indent=2
                ).replace("\n", "\n      ")
                match = re.search(r'(?m)^      "' + field + r'":\s*', text)
                if match:
                    _, field_end = decoder.raw_decode(text, match.end())
                    text = text[: match.end()] + value + text[field_end:]
                else:
                    anchor = re.search(r'(?m)^      "actions":', text)
                    if anchor is None:
                        raise ValueError("Cannot locate evaluation criteria")
                    text = (
                        text[: anchor.start()]
                        + f'      "{field}": {value},\n'
                        + text[anchor.start() :]
                    )
            replacements.append((position, end, text))
        position = end
    for start, end, replacement in reversed(replacements):
        original = original[:start] + replacement + original[end:]
    if json.loads(original) != updated:
        raise ValueError("Rendering changed unrelated task data")
    return original


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--task-id", help="Emit a bounded patch for one task")
    args = parser.parse_args()
    original = read_task_text(TASK_FILE)
    tasks = json.loads(original)
    db = json.loads((DATA / "db.json").read_text(encoding="utf-8"))
    selected = (
        tasks
        if args.task_id is None
        else [task for task in tasks if task["id"] == args.task_id]
    )
    if not selected:
        raise ValueError("Unknown task ID")
    with_contract(selected, db)
    updated = tasks
    if args.check:
        if json.loads(original) != updated:
            print("Confidentiality contract is stale; regenerate and apply its patch.")
            return 1
        print(f"Confidentiality contract is current for {len(selected)} tasks.")
        return 0
    if is_task_index(TASK_FILE):
        print(render_task_patch(TASK_FILE, json.loads(original), tasks), end="")
        return 0
    rendered = render_contract(original, updated, {task["id"] for task in selected})
    diff = list(difflib.unified_diff(original.splitlines(), rendered.splitlines(), n=3))
    if diff:
        print("*** Begin Patch")
        print(f"*** Update File: {TASK_FILE}")
        anchored = False
        for line in diff[2:]:
            if line.startswith("@@"):
                print(
                    f'@@     "id": "{args.task_id}",'
                    if args.task_id and not anchored
                    else "@@"
                )
                anchored = True
            else:
                print(line)
        print("*** End Patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
