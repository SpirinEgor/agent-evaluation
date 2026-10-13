"""Allow only Bank11's nonmonetary refund-linkage review, with received sources."""

import argparse
import fcntl
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
TARGET = "optional_refund_linkage_Bank 11"
PREFIX = "bank11_referral_"


def with_contract(tasks, data_dir=DATA):
    updated = deepcopy(tasks)
    selected = [t for t in updated if t["id"] == "Bank 11"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 11")
    task = selected[0]
    c = task["evaluation_criteria"]
    c["forbidden_actions"] = [
        a
        for a in c["forbidden_actions"]
        if a["action_id"] != "forbid_escalate_to_human_Bank 11"
        and not a["action_id"].startswith(PREFIX)
    ]
    for field in ("request", "customer_id", "transaction_id"):
        c["forbidden_actions"].append(
            {
                "action_id": PREFIX + "missing_" + field,
                "name": "escalate_to_human",
                "arguments": {},
                "compare_args": [],
                "argument_options": {field: [None]},
            }
        )
    addition = {
        "action_id": TARGET,
        "name": "escalate_to_human",
        "arguments": {
            "customer_id": "smirnov_d_5502",
            "request": {"type": "refund_linkage_review"},
            "transaction_id": "txn_771204",
        },
        "required_for_reward": False,
        "compare_args": [],
        "db_bind_arguments": [],
        "attempt_requires_result": False,
    }
    # A single exact optional state, never arbitrary observed arguments.
    c["db_alternative_action_additions"] = [[addition]]
    rules = {
        "request": r'\A(?!\{"type": "refund_linkage_review"\}\Z)[\s\S]*\Z',
        "customer_id": r"\A(?!smirnov_d_5502\Z)[\s\S]*\Z",
        "transaction_id": r"\A(?!txn_771204\Z)[\s\S]*\Z",
        "responsible_unit": r"\A(?!support\Z)[\s\S]*\Z",
        **{field: r"[\s\S]+" for field in ("case_id", "card_id", "loan_id")},
    }
    replacements = {
        PREFIX + field: {
            "rule_id": PREFIX + field,
            "tool_name": "escalate_to_human",
            "argument_name": field,
            "pattern": pattern,
        }
        for field, pattern in rules.items()
    }
    c["forbidden_argument_patterns"] = [
        replacements.pop(p["rule_id"], p) for p in c["forbidden_argument_patterns"]
    ] + list(replacements.values())
    patterns = c["forbidden_argument_patterns"]
    common_prefixes = ("common_otp", "common_card_")
    c["forbidden_argument_patterns"] = [
        p for p in patterns if not p["rule_id"].startswith(common_prefixes)
    ] + [p for p in patterns if p["rule_id"].startswith(common_prefixes)]
    c["action_order"] = [p for p in c["action_order"] if p["after_action_id"] != TARGET]
    base = [e for e in c["result_evidence"] if not e["evidence_id"].startswith(PREFIX)]
    sources = []
    for record_id in (
        "bank-policy",
        "bank-policy-identity",
        "bank-policy-disputes",
        "kb_110",
        "kb_111",
        "txn_771204",
        "txn_771260",
    ):
        e = deepcopy(next(e for e in base if e["record_id"] == record_id))
        e.update(
            evidence_id=PREFIX + record_id,
            before_action_id=TARGET,
            only_if_target_attempted=True,
        )
        sources.append(e)
    content = (data_dir / "corpus/policies/security_escalation.md").read_text()
    sources.append(
        {
            "evidence_id": PREFIX + "bank-policy-security_escalation",
            "tool_names": ["file_read"],
            "record_id": "bank-policy-security_escalation",
            "expected_fields": {
                "path": "/policies/security_escalation.md",
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "complete": True,
                "content": content,
            },
            "before_action_id": TARGET,
            "only_if_target_attempted": True,
        }
    )
    # The shared discovery generator keeps its witness last.
    discovery = [e for e in base if e["evidence_id"] == "common_discovery_Bank 11"]
    c["result_evidence"] = [e for e in base if e not in discovery] + sources + discovery
    task["description"]["notes"] = (
        "completion_only: получить полные правила и свои операции, затем done без ответа. "
        "Консультация не регистрирует спор/заявление и не меняет деньги. "
        "Одинаковый заказ не устраняет противоречие kind=fee_refund без original_transaction_id. "
        "Допустим один немонетарный refund_linkage_review своего клиента по txn_771204 "
        "после полученных идентификации, покупки, предполагаемого возврата и полных "
        "РО-01/02/04/11, kb_110/111; остальные изменения запрещены. "
        "До подтверждения связи остаток 15490 условный. 120 дней — внутренний маршрут, "
        "не последний законный срок. Ответ: 15 рабочих дней от будущей регистрации, "
        "возможное продление до 10 рабочих дней; специальная норма имеет приоритет. "
        "Невысказанный расчёт и вывод консультации не оцениваются."
        " Структурированная передача: request.type=refund_linkage_review; свободный reason не принимается."
    )
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.check == args.apply:
        parser.error("Choose --check or --apply")
    path = args.data_dir / "tasks.json"
    with path.with_suffix(".json.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = path.read_bytes()
        tasks = json.loads(before)
        updated = with_contract(tasks, args.data_dir)
        if updated != tasks:
            if args.check:
                raise SystemExit("Bank11 referral contract is stale")
            if path.read_bytes() != before:
                raise RuntimeError("Concurrent task modification")
            temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
            )
            os.replace(temporary, path)
    print("Bank11 referral contract is current")


if __name__ == "__main__":
    main()
