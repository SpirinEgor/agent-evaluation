"""ID-bound Bank47 optional access protection with received risk evidence."""

import argparse
import json
from copy import deepcopy
from pathlib import Path


def with_contract(tasks):
    result = deepcopy(tasks)
    for task in result:
        if task["id"] != "Bank 47":
            continue
        c = task["evaluation_criteria"]
        target = "optional_access_protection_Bank 47"
        c["forbidden_argument_patterns"] = [
            p
            for p in c["forbidden_argument_patterns"]
            if not p["rule_id"].startswith("bank47_app_support_")
        ]
        c["result_evidence"] = [
            e
            for e in c["result_evidence"]
            if not e["evidence_id"].startswith("bank47_app_support_")
        ]
        c["forbidden_actions"] = [
            a for a in c["forbidden_actions"] if a["name"] != "escalate_to_human"
        ]
        c["forbidden_actions"].append(
            {
                "action_id": "bank47_access_protection_missing_request",
                "name": "escalate_to_human",
                "arguments": {},
                "compare_args": [],
                "argument_options": {"request": [None]},
            }
        )
        c["db_alternative_action_additions"] = [
            [
                {
                    "action_id": target,
                    "name": "escalate_to_human",
                    "arguments": {
                        "customer_id": "titova_a_7729",
                        "request": {"type": "access_protection"},
                    },
                    "required_for_reward": False,
                    "compare_args": [],
                    "db_bind_arguments": [],
                }
            ]
        ]
        reversed_order = deepcopy(c["db_alternative_action_additions"][0])
        reversed_order[0]["db_reference_before_action_id"] = "create_case_Bank 47"
        c["db_alternative_action_additions"].append(reversed_order)
        rules = [
            ("type", "request", r'\A(?!\{"type": "access_protection"\}\Z)[\s\S]*\Z'),
            ("unit", "responsible_unit", r"\A(?!support\Z)[\s\S]*\Z"),
        ]
        rules += [
            (field, field, r"[\s\S]*")
            for field in ("case_id", "transaction_id", "card_id", "loan_id")
        ]
        replacements = {
            "bank47_access_protection_" + rid: {
                "rule_id": "bank47_access_protection_" + rid,
                "tool_name": "escalate_to_human",
                "argument_name": field,
                "pattern": pattern,
            }
            for rid, field, pattern in rules
        }
        replacements["bank47_access_protection_type"]["require_object"] = True
        # Preserve shared privacy rule order, so generators remain mutually current.
        c["forbidden_argument_patterns"] = [
            replacements.pop(p["rule_id"], p) for p in c["forbidden_argument_patterns"]
        ] + list(replacements.values())
        c["action_order"] = [
            p for p in c["action_order"] if p["after_action_id"] != target
        ]
        for path in (
            "/policies/general.md",
            "/policies/security_escalation.md",
            "/knowledge/kb_184.md",
        ):
            source = next(
                e
                for e in c["result_evidence"]
                if e["expected_fields"].get("path") == path
                and e.get("before_action_id") == "create_case_Bank 47"
            )
            e = deepcopy(source)
            e.update(
                evidence_id="bank47_access_protection_" + source["record_id"],
                before_action_id=target,
                only_if_target_attempted=True,
            )
            existing = next(
                (
                    i
                    for i, item in enumerate(c["result_evidence"])
                    if item["evidence_id"] == e["evidence_id"]
                ),
                None,
            )
            if existing is None:
                c["result_evidence"].append(e)
            else:
                c["result_evidence"][existing] = e
        # Every attempt needs received device/risk and recognition evidence.
        for original in list(c["result_evidence"]):
            if original["evidence_id"] not in {
                "bank47_device:dev_8821",
                "bank47_device:dev_8822",
                "bank47_device:dev_4002",
                "bank47_recognition_receipt",
            }:
                continue
            e = deepcopy(original)
            e.update(
                evidence_id="bank47_access_protection_" + original["evidence_id"],
                before_action_id=target,
                only_if_target_attempted=True,
            )
            existing = next(
                (
                    i
                    for i, item in enumerate(c["result_evidence"])
                    if item["evidence_id"] == e["evidence_id"]
                ),
                None,
            )
            if existing is None:
                c["result_evidence"].append(e)
            else:
                c["result_evidence"][existing] = e
        # Common builders place their blocks last. Preserve that canonical order.
        for field, key, prefixes in (
            ("forbidden_argument_patterns", "rule_id", ("common_otp_", "common_card_")),
            ("result_evidence", "evidence_id", ("common_discovery_",)),
        ):
            c[field] = [p for p in c[field] if not p[key].startswith(prefixes)] + [
                p for p in c[field] if p[key].startswith(prefixes)
            ]
        note = " Допускается одна дополнительная структурированная передача access_protection своему клиенту после получения источников, устройств с неустранённым риском и ответа о принадлежности; блокировки сохраняются."
        if note not in task["description"]["notes"]:
            task["description"]["notes"] += note

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.check == args.apply:
        parser.error("Choose --check or --apply")
    path = args.data_dir / "tasks.json"
    before = path.read_text()
    tasks = json.loads(before)
    updated = with_contract(tasks)
    if updated != tasks:
        if args.check:
            raise SystemExit("Bank47 access protection contract stale")
        if path.read_text() != before:
            raise RuntimeError("Concurrent task modification")
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    print("Bank47 access protection contract current")


if __name__ == "__main__":
    main()
