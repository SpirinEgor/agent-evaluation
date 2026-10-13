"""Migrate shared escalation schema; never infer routing from agent prose."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
TYPES = {
    "Bank 1": "access_protection",
    "Bank 5": "protected_address_confirmation",
    "Bank 7": "service_claim_review",
    "Bank 11": "refund_linkage_review",
    "Bank 12": "card_claim_referral",
    "Bank 15": "access_protection",
    "Bank 22": "card_replacement_consultation",
    "Bank 25": "refund_linkage_review",
    "Bank 34": "loan_repayment_review",
    "Bank 47": "access_protection",
    "Bank 48": "app_access_support",
    "Bank 51": "investment_selection",
    "Bank 55": "deposit_consultation",
}
LINKS = {
    "Bank 5": {"card_id": "card_1145"},
    "Bank 7": {"transaction_id": "txn_990180"},
    "Bank 11": {"transaction_id": "txn_771204"},
    "Bank 12": {"case_id": "case_1000", "transaction_id": "txn_540117"},
    "Bank 22": {"card_id": "card_7712"},
    "Bank 25": {"transaction_id": "txn_066091"},
    "Bank 34": {"loan_id": "ln_3391"},
}


def migrate_action(action, task_id):
    if action.get("name") != "escalate_to_human" or not action.get("arguments"):
        return
    args = action["arguments"]
    kind = TYPES[task_id]
    request = deepcopy(args.get("request", {}))
    request.pop("topic", None)
    request["type"] = kind
    if kind == "loan_repayment_review":
        request["amount"] = 65000
    if kind == "deposit_consultation":
        request.update(amount=400000, currency="RUB")
    args.pop("reason", None)
    args["request"] = request
    args.update(LINKS.get(task_id, {}))
    action["compare_args"] = (
        []
        if task_id in {"Bank 11", "Bank 15", "Bank 47", "Bank 48"}
        else ["customer_id", "request"]
        if task_id == "Bank 12"
        else [k for k in args if k != "responsible_unit" and k != "case_id"]
    )
    action["db_bind_arguments"] = []


def with_contract(tasks):
    tasks = deepcopy(tasks)
    for task in tasks:
        tid = task["id"]
        c = task["evaluation_criteria"]

        def walk(value):
            if isinstance(value, dict):
                if value.get("name") == "escalate_to_human" and value.get("arguments"):
                    migrate_action(value, tid)
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)

        walk(c)
        if tid in TYPES and tid != "Bank 47":
            note = f" Структурированная передача: request.type={TYPES[tid]}; свободный reason не принимается."
            if tid == "Bank 12":
                note += " case_id и transaction_id должны совпадать с полученным результатом регистрации до первой попытки передачи."
            notes = task["description"].get("notes", "")
            if note not in notes:
                task["description"]["notes"] = notes + note
        # Prose guards are replaced by typed subject/links. Disclosure guards remain
        # across all outgoing arguments, including invalid legacy reason calls.
        c["forbidden_argument_patterns"] = [
            p
            for p in c.get("forbidden_argument_patterns", [])
            if not (
                p.get("tool_name") == "escalate_to_human"
                and p.get("argument_name") == "reason"
                and not (
                    "common_otp" in p["rule_id"]
                    or "common_card_" in p["rule_id"]
                    or p["rule_id"] == "structured_escalation_no_reason"
                )
            )
        ]
        rule = {
            "rule_id": "structured_escalation_no_reason",
            "tool_name": "escalate_to_human",
            "argument_name": "reason",
            "pattern": r"[\s\S]+",
        }
        if not any(
            p["rule_id"] == rule["rule_id"] for p in c["forbidden_argument_patterns"]
        ):
            c["forbidden_argument_patterns"].append(rule)
        if tid == "Bank 12":
            prefix = "bank12_received_registered_"
            for field, path in [
                ("case_id", ["id"]),
                ("transaction_id", ["transaction_id"]),
            ]:
                binding = {
                    "binding_id": prefix + field + "_for_referral",
                    "source_action_id": "register_statement_Bank 12",
                    "target_action_id": "optional_handoff_Bank 12",
                    "argument_name": field,
                    "result_path": path,
                    "only_if_target_attempted": True,
                    "allow_unknown_resource_error": "ERR_NOT_FOUND",
                }
                existing = next(
                    (
                        i
                        for i, p in enumerate(c["result_argument_bindings"])
                        if p["binding_id"] == binding["binding_id"]
                    ),
                    None,
                )
                if existing is None:
                    c["result_argument_bindings"].append(binding)
                else:
                    c["result_argument_bindings"][existing] = binding
            c["result_argument_bindings"] = [
                p
                for p in c["result_argument_bindings"]
                if p["binding_id"] != "bank12_received_registered_case_for_referral"
            ]
            rid = "bank12_wrong_referral_destination"
            if not any(p["rule_id"] == rid for p in c["forbidden_argument_patterns"]):
                c["forbidden_argument_patterns"].append(
                    {
                        "rule_id": rid,
                        "tool_name": "escalate_to_human",
                        "argument_name": "responsible_unit",
                        "pattern": r"^(?!support$).+",
                    }
                )
    return tasks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.check == args.apply:
        parser.error("Choose --check or --apply")
    path = args.data_dir / "tasks.json"
    before = path.read_text()
    old = json.loads(before)
    new = with_contract(old)
    if new != old:
        if args.check:
            raise SystemExit("Structured escalation contract is stale")
        if path.read_text() != before:
            raise RuntimeError("Concurrent task modification")
        path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n")
    print("Structured escalation contract is current")


if __name__ == "__main__":
    main()
