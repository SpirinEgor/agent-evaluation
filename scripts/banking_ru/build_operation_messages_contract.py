"""Emit an ID-addressed banking-claims-v1 patch; never apply it automatically.

Task ownership is validated against the current access contract and provided DB.
The emitted schema has no hidden resource aliases: objects and amounts are bound
by the evaluator only from actual received evidence in that task's trajectory.
"""

import argparse
import difflib
import json
import re
from copy import deepcopy
from pathlib import Path

from build_access_contract import with_contract as with_access_contract
from build_confidentiality_contract import DATA, TASK_FILE, render_contract

from tau2.data_model.tasks import MessageClaimContract, ToolAccessPolicy

FIELD = "message_claim_contract"
CONTRACT = {
    "version": "banking-claims-v1",
    "families": ["card_block", "dispute", "transfer", "deposit_close"],
    "tool_arguments": {"ask_client": ["question"], "reply_to_ticket": ["text"]},
}


def validate_tasks(tasks: list[dict], db: dict) -> None:
    """Reject invalid ownership anywhere, including outside a selected patch."""
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Expected a nonempty task list")
    ids = [task["id"] for task in tasks]
    if any(not isinstance(task_id, str) or not task_id for task_id in ids):
        raise ValueError("Task IDs must be nonempty strings")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate task IDs")
    # Reuse the existing declaration of every owner selector, not a new subset
    # that could miss optional filters or the second endpoint of a transfer.
    expected = with_access_contract(
        [
            {
                "id": task["id"],
                "evaluation_criteria": {
                    "actions": task["evaluation_criteria"]["actions"]
                },
            }
            for task in tasks
        ],
        db,
    )
    for task, declared in zip(tasks, expected):
        criteria = task["evaluation_criteria"]
        policy = criteria.get("tool_access_policy")
        if policy is None:
            raise ValueError(f"Missing tool_access_policy in {task['id']}")
        try:
            ToolAccessPolicy.model_validate(policy)
            if criteria.get(FIELD) is not None:
                MessageClaimContract.model_validate(criteria[FIELD])
        except ValueError as error:
            raise ValueError(
                f"Invalid access/message configuration in {task['id']}"
            ) from error
        if policy != declared["evaluation_criteria"]["tool_access_policy"]:
            raise ValueError(f"Ownership contract is stale or invalid in {task['id']}")
        owner = policy["allowed_customer_ids"][0]
        if owner not in db["customers"]:
            raise ValueError(f"Unknown task owner in {task['id']}")
        auth_ids = set(policy["authentication_action_ids"])
        for action in criteria["actions"]:
            if action.get("action_id") in auth_ids and (
                action["name"] != "verify_identity"
                or action.get("requestor", "assistant") != "assistant"
                or action["arguments"].get("customer_id") != owner
            ):
                raise ValueError(f"Invalid authentication in {task['id']}")
            for argument, collection in (
                policy["tool_arguments"].get(action["name"], {}).items()
            ):
                value = action["arguments"].get(argument)
                if value is None:
                    continue
                owners = {
                    customer
                    for customer, resources in policy["resources_by_customer"][
                        collection
                    ].items()
                    if value in resources
                }
                # Unknown newly-created IDs stay subject to runtime reception
                # and access gates. Never reassign a known foreign resource.
                if owners and owners != {owner}:
                    raise ValueError(f"Foreign reference resource in {task['id']}")


def with_contract(tasks: list[dict], db: dict) -> list[dict]:
    """Attach only the shared catalog after validating each task's own scope."""
    validate_tasks(tasks, db)
    for task in tasks:
        task["evaluation_criteria"][FIELD] = (
            None
            if task["evaluation_criteria"].get("completion_mode") == "completion_only"
            else deepcopy(CONTRACT)
        )
    return tasks


def task_blocks(text: str):
    """Yield original JSON object text without reformatting unrelated fields."""
    decoder = json.JSONDecoder()
    position = text.index("[") + 1
    while True:
        while text[position].isspace() or text[position] == ",":
            position += 1
        if text[position] == "]":
            return
        task, end = decoder.raw_decode(text, position)
        yield task["id"], text[position:end]
        position = end


def contract_field(block: str) -> str | None:
    """Locate this field, including its comma, in the existing JSON layout."""
    match = re.search(r'(?m)^      "' + FIELD + r'":\s*', block)
    if match is None:
        return None
    _, end = json.JSONDecoder().raw_decode(block, match.end())
    if block[end : end + 1] == ",":
        end += 1
    return block[match.start() : end]


def emit_patch(
    original: str, tasks: list[dict], selected_ids: set[str], path: Path
) -> str:
    """Anchor each field insertion/replacement to its task, even identical ones."""
    rendered = render_contract(original, tasks, selected_ids, fields=(FIELD,))
    new_blocks = dict(task_blocks(rendered))
    lines = []
    for task_id, old_block in task_blocks(original):
        if task_id not in selected_ids:
            continue
        old_field = contract_field(old_block)
        new_field = contract_field(new_blocks[task_id])
        if old_field == new_field:
            continue
        lines.append(f'@@     "id": "{task_id}",')
        if old_field is None:
            lines.extend("+" + line for line in new_field.splitlines())
            lines.append('       "actions": [')
        else:
            # One bounded hunk per field. Multiple ordinary diff hunks could
            # resume in another task with the same shared tail.
            context = max(len(old_field.splitlines()), len(new_field.splitlines()))
            lines.extend(
                list(
                    difflib.unified_diff(
                        old_field.splitlines(), new_field.splitlines(), n=context
                    )
                )[3:]
            )
    if not lines:
        return ""
    return (
        "*** Begin Patch\n*** Update File: "
        + str(path)
        + "\n"
        + "\n".join(lines)
        + "\n*** End Patch\n"
    )


def main() -> int:
    """Check the full dataset by default, or emit a selected reversible patch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--task-id")
    parser.add_argument("--tasks", type=Path, default=TASK_FILE)
    parser.add_argument("--db", type=Path, default=DATA / "db.json")
    args = parser.parse_args()
    try:
        original = args.tasks.read_text(encoding="utf-8")
        tasks = json.loads(original)
        db = json.loads(args.db.read_text(encoding="utf-8"))
        validate_tasks(tasks, db)
        selected = (
            tasks
            if args.task_id is None
            else [t for t in tasks if t["id"] == args.task_id]
        )
        if not selected:
            raise ValueError("Unknown task ID")
        with_contract(selected, db)
        if args.check:
            if tasks != json.loads(original):
                print(
                    "Operation message contract is stale; regenerate and apply its patch."
                )
                return 1
            print(f"Operation message contract is current for {len(selected)} tasks.")
            return 0
        print(
            emit_patch(
                original, tasks, {t["id"] for t in selected}, args.tasks.resolve()
            ),
            end="",
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
