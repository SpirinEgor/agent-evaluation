"""ID-scoped Bank40 two-basis refund migration. Run by the integrator only."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

CUSTOMER = "solovyova_a_7734"
REFUNDS = [
    {"transaction_id": "txn_088214", "amount": 199.0},
    {"transaction_id": "txn_088219", "amount": 169.0},
]
TICKET = "Здравствуйте! Я Анна Соловьёва, телефон +7 916 773-40-56. На выбранной карте с окончанием 7735 вижу три платы за обслуживание: 199 ₽ 27 августа, затем две по 249 ₽ 28 августа. По другой карте есть ещё 199 ₽, её платёж сейчас не оспариваю. По одному из начислений уже зачислено 80 ₽, рядом возврат 30 ₽ в обработке. Проверьте договорные условия и периоды каждой комиссии, подтвердите ошибки только по выбранной карте и согласуйте со мной общую сумму и состав возвратов перед их выполнением. Верните лишь ещё не возмещённые ошибочные суммы, правомерные комиссии сохраните."


def with_contract(tasks, db, intake):
    tasks, db, intake = deepcopy(tasks), deepcopy(db), deepcopy(intake)
    selected = [t for t in tasks if t["id"] == "Bank 40"]
    if len(selected) != 1:
        raise ValueError("Expected exactly Bank 40")
    task = selected[0]
    history = db["customers"][CUSTOMER]["servicing_contract_history"]
    history[0]["avg_monthly_balance"] = 10000.0
    history[1]["effective_to"] = "2026-09-27"
    future = deepcopy(history[1])
    future.update(
        effective_from="2026-09-28",
        effective_to=None,
        revision_id="standard-cycle-2026-09-28-future",
        monthly_fee=299.0,
        free_min_balance=15000.0,
        free_min_turnover=40000.0,
        billing_period={"start": "2026-09-28", "end": "2026-10-27"},
        qualifying_period={"start": "2026-08-28", "end": "2026-09-27"},
        avg_monthly_balance=4200.0,
        monthly_turnover=8000.0,
    )
    future.pop("avg_monthly_balance", None)
    future.pop("monthly_turnover", None)
    history[:] = [row for row in history if row["effective_from"] != "2026-09-28"] + [
        future
    ]
    task["ticket"] = TICKET
    ins = task["user_scenario"]["instructions"]
    ins["reason_for_call"] = TICKET
    ins["known_info"] = (
        TICKET
        + " Кодовое слово — мозаика; дата рождения — 1992-07-08. Называйте только по запросу идентификации. Разрешаю согласованный набор возвратов общей суммой 368 ₽."
    )
    task["description"]["purpose"] = (
        "Выявить ошибочную комиссию бесплатного периода и повторную комиссию, согласовать и вернуть их невозмещённые части."
    )
    task["description"]["notes"] = (
        "completion_only. txn_088214: qualifying average10000 meets inclusive10000 threshold, return199; txn_088215 rightful249; txn_088219 duplicate249 less posted80 =169; processing30 unchanged. Target card7735 only. Obtain exact received consent for refund set199/169,total368 before either attempt; initial3782+368=4150. Independent reads and either refund order allowed; all other DB records preserved. Published future tariff effective2026-09-28 is not applicable. Основание действия: request.type=fee_correction; свободная причина не передаётся."
    )
    c = task["evaluation_criteria"]
    prior_criteria = deepcopy(c)
    actions = c["actions"]
    actions[:] = [
        a
        for a in actions
        if a["action_id"]
        not in {
            "bank40:read-periods",
            "bank40:read-reconciliation",
            "bank40:consent",
            "bank40:refund-free-period",
        }
    ]
    refund = next(a for a in actions if a["name"] == "refund_fee")
    refund.update(
        compare_args=[],
        argument_values={
            "request.type": "fee_correction",
            "transaction_id": "txn_088219",
            "amount": 169.0,
        },
    )
    refund["arguments"]["confirmation_id"] = "fee-refund-confirmation-1"
    consent = {
        "action_id": "bank40:consent",
        "name": "ask_client",
        "arguments": {
            "customer_id": CUSTOMER,
            "request": {
                "type": "fee_refund_confirmation",
                "card_id": "card_7735",
                "refunds": deepcopy(REFUNDS),
                "total_amount": 368.0,
            },
        },
        "compare_args": ["customer_id"],
        "argument_values": {"request.type": "fee_refund_confirmation"},
        "required_for_reward": True,
    }
    extra = deepcopy(refund)
    extra["action_id"] = "bank40:refund-free-period"
    extra["arguments"].update(transaction_id="txn_088214", amount=199.0)
    extra["argument_values"].update(transaction_id="txn_088214", amount=199.0)
    i = actions.index(refund)
    actions[i:i] = [
        {
            "action_id": "bank40:read-periods",
            "name": "file_read",
            "arguments": {"path": "/knowledge/kb_fee_contract_periods.md"},
            "required_for_reward": False,
        },
        {
            "action_id": "bank40:read-reconciliation",
            "name": "file_read",
            "arguments": {"path": "/knowledge/kb_fee_refund_reconciliation.md"},
            "required_for_reward": False,
        },
        consent,
        extra,
    ]
    c["forbidden_argument_patterns"][:] = [
        r
        for r in c["forbidden_argument_patterns"]
        if r["rule_id"]
        not in {
            "wrong_refund_fee_transaction_id_Bank 40",
            "wrong_refund_fee_amount_Bank 40",
        }
    ]
    c["forbidden_argument_patterns"].append(
        {
            "rule_id": "wrong_refund_fee_transaction_id_Bank 40",
            "tool_name": "refund_fee",
            "argument_name": "transaction_id",
            "pattern": r"^(?!(?:txn_088214|txn_088219)$).+",
        }
    )
    c["env_assertions"] = [
        {
            "env_type": "assistant",
            "func_name": "assert_no_excess_fee_refund_attempts",
            "arguments": {"transaction_id": r["transaction_id"]},
            "assert_value": True,
        }
        for r in REFUNDS
    ]
    c["result_argument_bindings"][:] = [
        b
        for b in c["result_argument_bindings"]
        if not b["binding_id"].startswith("bank40:consent:")
    ]
    c["action_order"][:] = [
        p for p in c["action_order"] if p["before_action_id"] != "bank40:consent"
    ]
    for target in [extra["action_id"], refund["action_id"]]:
        c["result_argument_bindings"].append(
            {
                "binding_id": "bank40:consent:" + target,
                "source_action_id": "bank40:consent",
                "target_action_id": target,
                "argument_name": "confirmation_id",
                "result_path": ["confirmation_id"],
                "case_insensitive": False,
            }
        )
        c["action_order"].append(
            {
                "before_action_id": "bank40:consent",
                "after_action_id": target,
                "require_result_before_target": True,
            }
        )
    # Exact reversed reference state preserves every transaction and generated ID.
    swapped_extra, swapped_refund = deepcopy(extra), deepcopy(refund)
    swapped_extra["arguments"], swapped_refund["arguments"] = (
        deepcopy(refund["arguments"]),
        deepcopy(extra["arguments"]),
    )
    c["db_alternative_action_replacements"] = [
        {extra["action_id"]: swapped_extra, refund["action_id"]: swapped_refund}
    ]
    # The same complete data must precede both monetary attempts and consent.
    evidence = c["result_evidence"]
    evidence[:] = [
        e
        for e in evidence
        if not e["evidence_id"].startswith(("bank40:copy:", "bank40:new-source:"))
        and e.get("record_id") != "kb_121"
    ]
    for e in evidence:
        if e.get("record_id") == CUSTOMER and e["tool_names"] == [
            "get_customer_profile"
        ]:
            e["expected_fields"] = {"servicing_contract_history": deepcopy(history)}
    originals = deepcopy(evidence)
    for e in originals:
        if e.get("before_action_id") == refund["action_id"]:
            for target in [extra["action_id"], "bank40:consent"]:
                new = deepcopy(e)
                new.update(
                    evidence_id="bank40:copy:" + target + ":" + e["evidence_id"],
                    before_action_id=target,
                )
                evidence.append(new)
    answers = intake.setdefault(CUSTOMER, [])
    answers[:] = [a for a in answers if not a.get("fee_refund_confirmation")]
    answers.append(
        {
            "keywords": ["возврат комиссии"],
            "answer": "Согласна вернуть 199 ₽ по ошибке бесплатного периода и 169 ₽ по повторной комиссии выбранной карты. Всего 368 ₽.",
            "fee_refund_confirmation": {
                "card_id": "card_7735",
                "refunds": deepcopy(REFUNDS),
                "total_amount": 368.0,
                "confirmed": True,
            },
        }
    )
    for field, key in [
        ("forbidden_argument_patterns", "rule_id"),
        ("result_argument_bindings", "binding_id"),
        ("result_evidence", "evidence_id"),
    ]:
        by_id = {row[key]: row for row in c[field]}
        prior_ids = [row[key] for row in prior_criteria[field]]
        c[field] = [
            by_id.pop(identifier) for identifier in prior_ids if identifier in by_id
        ] + list(by_id.values())
    # RO-02 must precede identification; the RO-01 framework precedes business execution.
    for evidence in task["evaluation_criteria"]["result_evidence"]:
        if evidence.get("record_id") == "bank-policy" and evidence.get(
            "before_action_id", ""
        ).startswith("verify"):
            evidence["before_action_id"] = "bank40:consent"
    return tasks, db, intake


def migrate(data):
    names = ["tasks.json", "db.json", "intake_answers.json"]
    before = [json.loads((data / n).read_text()) for n in names]
    after = with_contract(*before)
    # Assert every other task and every other customer/transaction unchanged.
    assert [t for t in before[0] if t["id"] != "Bank 40"] == [
        t for t in after[0] if t["id"] != "Bank 40"
    ]
    for key in before[1]:
        if key != "customers":
            assert before[1][key] == after[1][key], key
    assert {k: v for k, v in before[1]["customers"].items() if k != CUSTOMER} == {
        k: v for k, v in after[1]["customers"].items() if k != CUSTOMER
    }
    manifest_current = json.loads((data / "manifest.json").read_text())
    selected = next(t for t in after[0] if t["id"] == "Bank 40")
    evidence = selected["evaluation_criteria"]["result_evidence"]
    evidence[:] = [
        e for e in evidence if not e["evidence_id"].startswith("bank40:new-source:")
    ]
    for name in ["kb_fee_contract_periods", "kb_fee_refund_reconciliation"]:
        virtual = "/knowledge/" + name + ".md"
        descriptor = manifest_current["documents"][virtual]
        body = (data / "corpus" / virtual.lstrip("/")).read_text()
        for target in [
            "bank40:consent",
            "bank40:refund-free-period",
            "refund_fee_Bank 40",
        ]:
            evidence.append(
                {
                    "evidence_id": "bank40:new-source:" + name + ":" + target,
                    "tool_names": ["file_read"],
                    "record_id": name,
                    "expected_fields": {
                        "path": virtual,
                        "sha256": descriptor["sha256"],
                        "complete": True,
                        "content": body,
                    },
                    "before_action_id": target,
                }
            )
    previous_evidence = next(t for t in before[0] if t["id"] == "Bank 40")[
        "evaluation_criteria"
    ]["result_evidence"]
    by_id = {row["evidence_id"]: row for row in evidence}
    evidence[:] = [
        by_id.pop(row["evidence_id"])
        for row in previous_evidence
        if row["evidence_id"] in by_id
    ] + list(by_id.values())
    for n, value in zip(names, after):
        (data / n).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    manifest = json.loads((data / "manifest.json").read_text())
    manifest["intake_answers_sha256"] = hashlib.sha256(
        (data / "intake_answers.json").read_bytes()
    ).hexdigest()
    (data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", type=Path, required=True)
    a = p.parse_args()
    migrate(a.data_dir)
