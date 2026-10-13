"""Migrate narrative operation grounds to shared structured requests."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
KINDS = {
    "cancel_dispute": "customer_withdrawal",
    "refund_fee": "fee_correction",
    "refund_transaction": "purchase_refund",
}
TARGETS = {"Bank 13": "cancel_dispute", "Bank 40": "refund_fee"}


def with_contract(tasks):
    tasks = deepcopy(tasks)
    for task in tasks:
        c = task["evaluation_criteria"]

        def walk(value):
            if isinstance(value, dict):
                name = value.get("name")
                if name in KINDS and value.get("arguments"):
                    if task["id"] not in TARGETS or name != TARGETS[task["id"]]:
                        raise ValueError(
                            f"Undeclared operation migration: {task['id']} {name}"
                        )
                    args = value["arguments"]
                    args.pop("reason", None)
                    args["request"] = {"type": KINDS[name]}
                    value["compare_args"] = (
                        ["dispute_id"] if task["id"] == "Bank 13" else []
                    )
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        for key in [
            "actions",
            "db_alternative_action_overrides",
            "db_alternative_action_additions",
            "db_alternative_action_replacements",
        ]:
            walk(c.get(key, []))
        rules = c.setdefault("forbidden_argument_patterns", [])
        for name, kind in KINDS.items():
            additions = [
                {
                    "rule_id": f"structured_{name}_no_reason",
                    "tool_name": name,
                    "argument_name": "reason",
                    "pattern": r"[\s\S]+",
                },
                {
                    "rule_id": f"structured_{name}_request",
                    "tool_name": name,
                    "argument_name": "request",
                    "pattern": r'^(?!\{"type": "' + kind + r'"\}$)[\s\S]+',
                },
            ]
            for rule in additions:
                if not any(p["rule_id"] == rule["rule_id"] for p in rules):
                    rules.append(rule)
        if task["id"] in TARGETS:
            note = f" Основание действия: request.type={KINDS[TARGETS[task['id']]]}; свободная причина не передаётся."
            if note not in task["description"]["notes"]:
                task["description"]["notes"] += note
    return tasks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    p = args.data_dir / "tasks.json"
    original = p.read_text()
    tasks = json.loads(original)
    updated = with_contract(tasks)
    if args.check:
        if tasks != updated:
            raise SystemExit("Operation request contracts are stale")
    else:
        if p.read_text() != original:
            raise RuntimeError("Concurrent task change")
        p.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    print("Operation request contracts current")


if __name__ == "__main__":
    main()
