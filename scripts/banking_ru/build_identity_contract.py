"""Emit a patch binding identity credentials to actual received client answers.

The reference keeps its original question and credential. Only evaluation
metadata changes: both backend-supported identity alternatives are accepted,
and every verification attempt must use the received client's secret.
"""

import argparse
import difflib
import json
import re

from build_confidentiality_contract import DATA, TASK_FILE, render_contract

PREFIX = "common_identity_secret_"
CLIENT_PATTERN = (
    r"Клиент:\s*«(?:Кодовое слово|Дата рождения)\s*—\s*(?P<value>[^»\r\n]+)»\.?"
)
BIRTH_DATE_ALIASES = [
    "дату рождения",
    "даты рождения",
    "дата рождения подтверждение личности",
    "дату рождения подтверждение личности",
    "даты рождения подтверждение личности",
]


def with_client_sources(db: dict) -> dict:
    """Keep explicit birth-date questions from losing to generic identity keywords."""
    for customer_id, customer in db["customers"].items():
        sources = [
            a
            for a in db["client_answers"].get(customer_id, [])
            if a["answer"] == "Дата рождения — " + customer["birth_date"]
        ]
        if len(sources) != 1:
            raise ValueError(f"Expected one birth-date answer for {customer_id}")
        keywords = sources[0]["keywords"]
        keywords.extend(alias for alias in BIRTH_DATE_ALIASES if alias not in keywords)
    return db


def render_client_sources(original: str, updated: dict) -> str:
    """Replace only client_answers, retaining all unrelated state and formatting."""
    match = re.search(r'"client_answers":\s*', original)
    if match is None:
        raise ValueError("Missing client answers")
    _, end = json.JSONDecoder().raw_decode(original, match.end())
    value = json.dumps(updated["client_answers"], ensure_ascii=False, indent=2).replace(
        "\n", "\n  "
    )
    rendered = original[: match.end()] + value + original[end:]
    if json.loads(rendered) != updated:
        raise ValueError("Rendering changed unrelated banking state")
    return rendered


def with_contract(tasks: list[dict]) -> list[dict]:
    """Replace route-specific identity gates with received-value binding."""
    for task in tasks:
        criteria = task["evaluation_criteria"]
        actions = criteria["actions"]
        checks = [a for a in actions if a["name"] == "verify_identity"]
        if len(checks) != 1:
            raise ValueError(f"Task {task['id']} needs an explicit identity route")
        target = checks[0]
        customer_id = target["arguments"]["customer_id"]
        sources = [
            a
            for a in actions[: actions.index(target)]
            if a["name"] == "ask_client"
            and a["arguments"].get("customer_id") == customer_id
        ]
        if not sources:
            raise ValueError(f"Task {task['id']} has no client identity source")
        source = sources[-1]
        source.update(
            compare_args=["customer_id"],
            result_contains=[],
            argument_patterns={},
            attempt_requires_result=False,
        )
        if task["id"] in {f"Bank {n}" for n in range(1, 62)}:
            source["argument_values"] = {"request.type": "identity"}
        target.update(compare_args=["customer_id"], attempt_requires_result=False)
        # The binding's full-result pattern identifies the source's topic.
        # Do not force the question to use a particular set of synonyms.
        bindings = [
            b
            for b in criteria.get("result_argument_bindings", [])
            if not b["binding_id"].startswith((PREFIX, "common_identity_receipt_"))
        ]
        criteria["result_argument_bindings"] = [
            *bindings,
            {
                "binding_id": PREFIX + task["id"],
                "source_action_id": source["action_id"],
                "target_action_id": target["action_id"],
                "argument_name": "credential",
                "result_pattern": CLIENT_PATTERN,
                "case_insensitive": True,
            },
        ]
        if task["id"] in {f"Bank {n}" for n in range(1, 62)}:
            binding = criteria["result_argument_bindings"][-1]
            binding.pop("result_pattern")
            binding["result_path"] = ["data", "credential"]
            criteria["result_argument_bindings"].append(
                {
                    "binding_id": "common_identity_receipt_" + task["id"],
                    "source_action_id": source["action_id"],
                    "target_action_id": target["action_id"],
                    "argument_name": "answer_id",
                    "result_path": ["answer_id"],
                    "case_insensitive": False,
                }
            )
        if not target.get("required_for_reward", True):
            for binding in criteria["result_argument_bindings"]:
                if binding["binding_id"].startswith(
                    (PREFIX, "common_identity_receipt_")
                ):
                    binding["only_if_target_attempted"] = True
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--task-id")
    args = parser.parse_args()
    original = TASK_FILE.read_text(encoding="utf-8")
    db_file = DATA / "db.json"
    original_db = db_file.read_text(encoding="utf-8")
    db = with_client_sources(json.loads(original_db))
    tasks = json.loads(original)
    selected = (
        tasks if args.task_id is None else [t for t in tasks if t["id"] == args.task_id]
    )
    if not selected:
        raise ValueError("Unknown task ID")
    with_contract(selected)
    if args.check:
        if json.loads(original) != tasks or json.loads(original_db) != db:
            print("Identity contract is stale; regenerate and apply its patch.")
            return 1
        print(f"Identity contract is current for {len(selected)} tasks.")
        return 0
    rendered = render_contract(
        original,
        tasks,
        {t["id"] for t in selected},
        fields=("actions", "result_argument_bindings"),
    )
    db_rendered = render_client_sources(original_db, db)
    changes = [(TASK_FILE, original, rendered), (db_file, original_db, db_rendered)]
    if any(before != after for _, before, after in changes):
        print("*** Begin Patch")
        for path, before, after in changes:
            diff = list(
                difflib.unified_diff(before.splitlines(), after.splitlines(), n=3)
            )
            if diff:
                print(f"*** Update File: {path}")
                anchored = False
                for line in diff[2:]:
                    if line.startswith("@@"):
                        print(
                            f'@@     "id": "{args.task_id}",'
                            if args.task_id and path == TASK_FILE and not anchored
                            else "@@"
                        )
                        anchored = True
                    else:
                        print(line)
        print("*** End Patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
