"""Emit an apply_patch patch for equivalent own discovery and received identity evidence."""

import argparse
import difflib
import json

from build_access_contract import discovery_catalog
from build_confidentiality_contract import DATA, TASK_FILE, render_contract


def with_contract(tasks: list[dict], db: dict) -> list[dict]:
    """Preserve oracle arguments/state; remove only incidental search spelling."""
    discovery = discovery_catalog(db)
    for task in tasks:
        criteria = task["evaluation_criteria"]
        actions = criteria["actions"]
        finds = [a for a in actions if a["name"] == "find_customer"]
        verifies = [a for a in actions if a["name"] == "verify_identity"]
        if len(finds) != 1 or len(verifies) != 1:
            raise ValueError(f"Ambiguous discovery/identity in {task['id']}")
        find, verify = finds[0], verifies[0]
        customer_id = verify["arguments"]["customer_id"]
        question = next(
            a
            for a in reversed(actions[: actions.index(verify)])
            if a["name"] == "ask_client"
        )
        find["compare_args"] = []
        find["argument_patterns"] = {}
        find["result_contains"] = []
        find["attempt_requires_result"] = False
        criteria["tool_access_policy"]["discovery_tools"] = discovery
        criteria["result_evidence"] = [
            e
            for e in criteria.get("result_evidence", [])
            if not e["evidence_id"].startswith("common_discovery_")
        ] + [
            {
                "evidence_id": "common_discovery_" + task["id"],
                "tool_names": ["find_customer"],
                "record_id": customer_id,
                "expected_fields": {
                    "full_name": db["customers"][customer_id]["full_name"]
                },
                "before_action_id": question["action_id"],
            }
        ]
        if not question.get("required_for_reward", True):
            criteria["result_evidence"][-1]["only_if_target_attempted"] = True
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--task-id")
    args = parser.parse_args()
    original = TASK_FILE.read_text(encoding="utf-8")
    tasks = json.loads(original)
    selected = (
        tasks if args.task_id is None else [t for t in tasks if t["id"] == args.task_id]
    )
    if not selected:
        raise ValueError("Unknown task ID")
    with_contract(selected, json.loads((DATA / "db.json").read_text(encoding="utf-8")))
    if args.check:
        if json.loads(original) != tasks:
            print("Discovery contract is stale; regenerate and apply its patch.")
            return 1
        print(f"Discovery contract is current for {len(selected)} tasks.")
        return 0
    rendered = render_contract(
        original,
        tasks,
        {t["id"] for t in selected},
        fields=("tool_access_policy", "actions", "result_evidence"),
    )
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
