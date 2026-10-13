"""ID-bound Bank15 access protection without a duplicate monetary statement."""

import argparse
import fcntl
import json
from copy import deepcopy
from pathlib import Path


def with_contract(tasks, data_dir):
    result = deepcopy(tasks)
    selected = [task for task in result if task["id"] == "Bank 15"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 15")
    task = selected[0]
    c = task["evaluation_criteria"]
    target = "optional_access_protection_Bank 15"
    customer = "morozov_s_8850"
    c["forbidden_actions"] = [
        a for a in c["forbidden_actions"] if a["name"] != "escalate_to_human"
    ] + [
        {
            "action_id": "bank15_access_missing_request",
            "name": "escalate_to_human",
            "arguments": {},
            "compare_args": [],
            "argument_options": {"request": [None]},
        }
    ]
    addition = {
        "action_id": target,
        "name": "escalate_to_human",
        "arguments": {
            "customer_id": customer,
            "request": {"type": "access_protection"},
        },
        "required_for_reward": False,
        "compare_args": [],
        "db_bind_arguments": [],
    }
    early = {
        **deepcopy(addition),
        "db_reference_before_action_id": "create_case_Bank 15",
    }
    c["db_alternative_action_additions"] = [[addition], [early]]
    # Customer + exact returned statement facts identify the received record.
    # The case counter may advance after an independent access-protection case.
    received = next(
        e for e in c["result_evidence"] if e["evidence_id"] == "bank15_received_case"
    )
    received["record_id"] = customer
    rules = [
        ("type", "request", r'\A(?!\{"type": "access_protection"\}\Z)[\s\S]*\Z'),
        ("unit", "responsible_unit", r"\A(?!support\Z)[\s\S]*\Z"),
        # A failed call with this own card is a recoverable API-shape error.
        ("card", "card_id", r"\A(?!card_8850\Z)[\s\S]*\Z"),
        *(
            (field, field, r"[\s\S]*")
            for field in ("case_id", "transaction_id", "loan_id")
        ),
    ]
    replacements = {
        "bank15_access_" + rid: {
            "rule_id": "bank15_access_" + rid,
            "tool_name": "escalate_to_human",
            "argument_name": field,
            "pattern": pattern,
        }
        for rid, field, pattern in rules
    }
    replacements["bank15_access_type"]["require_object"] = True
    c["forbidden_argument_patterns"] = [
        replacements.pop(p["rule_id"], p) for p in c["forbidden_argument_patterns"]
    ] + list(replacements.values())
    evidence = []
    for path in (
        "/policies/general.md",
        "/policies/transfers_fraud.md",
        "/knowledge/kb_113.md",
    ):
        e = deepcopy(
            next(
                e
                for e in c["result_evidence"]
                if e["expected_fields"].get("path") == path
                and e.get("before_action_id") == "create_case_Bank 15"
            )
        )
        e.update(
            evidence_id="bank15_access_source_" + e["record_id"],
            before_action_id=target,
            only_if_target_attempted=True,
        )
        evidence.append(e)
    path = "/policies/security_escalation.md"
    descriptor = json.loads((data_dir / "manifest.json").read_text())["documents"][path]
    evidence.append(
        {
            "evidence_id": "bank15_access_source_RO11",
            "tool_names": ["file_read"],
            "record_id": descriptor["metadata"]["id"],
            "expected_fields": {
                "path": path,
                "sha256": descriptor["sha256"],
                "complete": True,
                "content": (data_dir / "corpus" / path.lstrip("/")).read_text(),
            },
            "before_action_id": target,
            "only_if_target_attempted": True,
        }
    )
    replacements = {e["evidence_id"]: e for e in evidence}
    c["result_evidence"] = [
        replacements.pop(e["evidence_id"], e) for e in c["result_evidence"]
    ] + list(replacements.values())
    # Common generators keep these shared blocks at the end.
    for field, key, prefixes in (
        ("forbidden_argument_patterns", "rule_id", ("common_otp_", "common_card_")),
        ("result_evidence", "evidence_id", ("common_discovery_",)),
    ):
        c[field] = [p for p in c[field] if not p[key].startswith(prefixes)] + [
            p for p in c[field] if p[key].startswith(prefixes)
        ]
    note = " Допускается одна дополнительная передача access_protection своему клиенту после получения РО-01/05/11 и kb_113; денежное заявление остаётся единственным."
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
    with path.with_name(path.name + ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = path.read_text()
        original = json.loads(before)
        updated = with_contract(original, args.data_dir)
        if original != updated:
            if args.check:
                raise SystemExit("Bank15 access protection contract stale")
            if path.read_text() != before:
                raise RuntimeError("Concurrent task modification")
            path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    print("Bank15 access protection contract current")


if __name__ == "__main__":
    main()
