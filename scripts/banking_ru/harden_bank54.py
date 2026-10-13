"""Address Bank54's linked partial refund claim, without running models.

Only Bank54 and its own rows change. Refresh common source, discovery, access,
identity and confidentiality receipts after applying to a reviewed snapshot.
"""

import argparse
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OWNER = "kuznetsov_i_3140"
TARGET = "txn_314101"
PROOF = "doc_3140_fitness_cancel"
CONTRACT = "fitness-3140-main"
PROOF_BODY = (
    "Подтверждение продавца «Фитнес Клуб». Клиент Кузнецов Игорь Валерьевич; "
    "договор fitness-3140-main. Услуга отменена 2026-07-20; оплаченный доступ "
    "завершён 2026-07-31. Отказ от использования сохранённых платёжных "
    "реквизитов получен продавцом 2026-07-22. Банковское разрешение sub_3141 "
    "отменено 2026-07-22. Августовское продление заказа fitness-3140-aug "
    "соответствует покупке txn_314101 от 2026-08-05 на 2900 RUB. "
    "Сумма фактического возврата по нему txn_314104 — 700 RUB; ещё 500 RUB "
    "txn_314105 находятся в обработке. Возврат txn_314106 на 700 RUB "
    "относится к июньскому заказу fitness-3140-jun, покупке txn_314107. "
    "Подтверждение фиксирует факты, но не решение банка о неправомерности. "
    "Клиентский документ."
)
TICKET = (
    "Я Кузнецов Игорь Валерьевич, мой телефон +7 921 314-00-58. Здравствуйте. "
    "5 августа списали 2 900 ₽ за «Фитнес Клуб». Фитнес я отменил ещё 20 июля, "
    "оплаченный доступ был до конца июля. Есть подтверждение продавца. "
    "Теперь вижу два зачисления по 700 ₽ и ещё 500 ₽ в обработке, но не понимаю, "
    "какие возвраты относятся к августовскому списанию. Проверьте данные и "
    "зарегистрируйте одну претензию только на невозмещённую часть августовского "
    "списания фитнеса. Будущую оплату фитнеса я уже отключил, повторять отмену "
    "не прошу. «Яндекс Плюс» продолжаю пользоваться, списание 399 ₽ признаю, "
    "его не оспаривайте и не отключайте. Другие покупки признаю."
)


def _evidence(record, fields, tools):
    return {
        "evidence_id": f"bank54:{record}:accept_claim",
        "tool_names": tools,
        "record_id": record,
        "expected_fields": fields,
        "before_action_id": "accept_claim_Bank 54",
        "after_action_id": "verify_Bank 54",
    }


def with_contract(tasks, db):
    """Return a scoped, idempotent copy of the subject task and its records."""
    tasks, db = deepcopy(tasks), deepcopy(db)
    matches = [t for t in tasks if t["id"] == "Bank 54"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one Bank 54")
    task = matches[0]
    task["ticket"] = TICKET
    ins = task["user_scenario"]["instructions"]
    ins["reason_for_call"] = TICKET
    ins["known_info"] = (
        TICKET
        + " Кодовое слово — омуль; дата рождения — 1985-02-17. Только по запросу идентификации."
    )
    task["description"]["purpose"] = (
        "Принять одно требование на невозмещённую часть августовского фитнеса по связанным возвратам и независимым доказательствам отмены."
    )
    task["description"]["notes"] = (
        "completion_only: идентификация, РО06 и применимая KB111; покупка2900, "
        "linked posted700, linked processing500 и другой июньский posted700, "
        "документ продавца с отменой услуги20.07, доступом до31.07 и отзывом "
        "платёжных реквизитов22.07 до первой попытки. Одно требование2200 по "
        "txn_314101 через open_dispute или связанное create_case. Долг не "
        "блокирует приём. Не отменять разрешения, не возвращать деньги. "
        "Списки/детали и прямое чтение источников допустимы, calculate необязателен. "
        "Получить результат и done. Итоговый ответ не требуется."
    )
    tx = db["transactions"]
    for collection, ids in [
        ("transactions", ["txn_314104", "txn_314105", "txn_314106", "txn_314107"]),
        ("documents", [PROOF]),
    ]:
        for record in ids:
            if (
                record in db[collection]
                and db[collection][record].get("customer_id") != OWNER
            ):
                raise ValueError(f"Bank54 record owner collision: {record}")
    if (
        tx[TARGET]["customer_id"] != OWNER
        or db["subscriptions"]["sub_3141"]["customer_id"] != OWNER
    ):
        raise ValueError("Bank54 source owner differs")
    tx[TARGET].update(order_id="fitness-3140-aug", is_subscription=True)
    db["subscriptions"]["sub_3141"].update(
        cancelled_at="2026-07-22",
        merchant_id="merchant_fitness",
        contract_reference=CONTRACT,
    )
    older = deepcopy(tx[TARGET])
    older.update(
        id="txn_314107", date="2026-06-05", order_id="fitness-3140-jun", dispute_id=None
    )
    tx["txn_314107"] = older
    for id, amount, status, original, order in [
        ("txn_314104", 700, "posted", TARGET, "fitness-3140-aug"),
        ("txn_314105", 500, "processing", TARGET, "fitness-3140-aug"),
        ("txn_314106", 700, "posted", "txn_314107", "fitness-3140-jun"),
    ]:
        row = deepcopy(tx[TARGET])
        row.update(
            id=id,
            amount=float(amount),
            status=status,
            kind="fee_refund",
            date="2026-08-24",
            merchant="Возврат Фитнес Клуб",
            original_transaction_id=original,
            order_id=order,
            is_subscription=False,
            dispute_id=None,
        )
        tx[id] = row
    db["documents"][PROOF] = {
        "id": PROOF,
        "customer_id": OWNER,
        "title": "Подтверждение отмены фитнеса и связи возвратов",
        "body": PROOF_BODY,
        "created_at": "2026-08-25",
        "shared": False,
    }
    c = task["evaluation_criteria"]
    claim = "accept_claim_Bank 54"
    added = {"bank54_read_refunds", "bank54_read_proof", "bank54_read_kb111"}
    c["actions"] = [a for a in c["actions"] if a["action_id"] not in added]
    index = next(i for i, a in enumerate(c["actions"]) if a["action_id"] == claim)
    c["actions"][index:index] = [
        {
            "action_id": "bank54_read_kb111",
            "name": "file_read",
            "arguments": {"path": "/knowledge/kb_111.md"},
            "required_for_reward": False,
        },
        {
            "action_id": "bank54_read_proof",
            "name": "get_documents",
            "arguments": {"customer_id": OWNER},
            "required_for_reward": False,
        },
    ]
    for a in c["actions"]:
        if a["action_id"] == claim:
            a["arguments"]["amount"] = 2200.0
        if a["name"] == "get_transactions":
            a["arguments"].update(
                date_from="2026-06-01", date_to="2026-08-28", limit=50
            )
    for alt in c["db_alternative_action_replacements"]:
        alt[claim]["arguments"]["amount"] = 2200.0
    for rule in c["forbidden_argument_patterns"]:
        if rule["rule_id"] in {
            "completion:Bank 54:open_dispute:amount",
            "completion:Bank 54:create_case:amount",
        }:
            rule["pattern"] = r"\A(?!2200(?:\.0+)?\Z)[\s\S]*\Z"
    # Unknown IDs that fail may be corrected. Only known wrong records are
    # forbidden selectors; an arbitrary list differing from the reference is
    # not by itself an unauthorized attempt.
    known_wrong_ids = "|".join(re.escape(id) for id in sorted(tx) if id != TARGET)
    for rule in c["forbidden_argument_patterns"]:
        if rule["rule_id"] == "completion:Bank 54:claim_transactions":
            rule["pattern"] = r'"(?:' + known_wrong_ids + r')"'
    original_evidence_order = [e["evidence_id"] for e in c["result_evidence"]]
    c["forbidden_actions"] = [
        a
        for a in c["forbidden_actions"]
        if not a["action_id"].startswith(
            ("bank54_default_amount:", "bank54_wrong_target:")
        )
    ]
    for tool, selector, value in [
        ("open_dispute", "transaction_id", TARGET),
        ("create_case", "transaction_id", TARGET),
        ("create_case", "transaction_ids", [TARGET]),
    ]:
        c["forbidden_actions"].append(
            {
                "action_id": f"bank54_default_amount:{tool}:{selector}",
                "name": tool,
                "arguments": {selector: value},
                "compare_args": [selector],
                "argument_options": {"amount": [None]},
            }
        )
    for tool in ["open_dispute", "create_case"]:
        for wrong in ["txn_314104", "txn_314105", "txn_314106", "txn_314107"]:
            c["forbidden_actions"].append(
                {
                    "action_id": f"bank54_wrong_target:{tool}:{wrong}",
                    "name": tool,
                    "arguments": {"transaction_id": wrong},
                    "compare_args": ["transaction_id"],
                }
            )
    c["forbidden_argument_patterns"] = [
        r
        for r in c["forbidden_argument_patterns"]
        if r["rule_id"] != "bank54_wrong_target_list"
    ]
    c["forbidden_argument_patterns"].append(
        {
            "rule_id": "bank54_wrong_target_list",
            "tool_name": "create_case",
            "argument_name": "transaction_ids",
            "pattern": r'"txn_31410[4-7]"',
        }
    )
    # Existing record evidence is replaced by exact current subject fields.
    c["result_evidence"] = [
        e
        for e in c["result_evidence"]
        if not e["evidence_id"].startswith("bank54:")
        and e.get("record_id") not in {"sub_3141", TARGET}
    ]
    c["result_evidence"].append(
        _evidence(
            "sub_3141",
            {
                k: db["subscriptions"]["sub_3141"][k]
                for k in [
                    "customer_id",
                    "status",
                    "cancelled_at",
                    "paid_until",
                    "merchant_id",
                    "contract_reference",
                ]
            },
            ["get_subscriptions"],
        )
    )
    for id in [TARGET, "txn_314104", "txn_314105", "txn_314106"]:
        c["result_evidence"].append(
            _evidence(
                id,
                {
                    k: tx[id][k]
                    for k in [
                        "customer_id",
                        "card_id",
                        "date",
                        "amount",
                        "status",
                        "kind",
                        "order_id",
                        "original_transaction_id",
                    ]
                    if k in tx[id]
                },
                ["get_transactions", "get_transaction_details"],
            )
        )
    c["result_evidence"].append(
        _evidence(PROOF, {"customer_id": OWNER, "body": PROOF_BODY}, ["get_documents"])
    )
    receipts = {e["evidence_id"]: e for e in c["result_evidence"]}
    c["result_evidence"] = [
        receipts.pop(id) for id in original_evidence_order if id in receipts
    ] + list(receipts.values())
    c["env_assertions"] = [
        a for a in c["env_assertions"] if a["func_name"] != "assert_exactly_one_claim"
    ]
    c["env_assertions"].append(
        {
            "env_type": "assistant",
            "func_name": "assert_exactly_one_claim",
            "arguments": {
                "customer_id": OWNER,
                "transaction_id": TARGET,
                "expected_amount": 2200.0,
            },
            "assert_value": True,
        }
    )
    c["process_source_targets"]["/knowledge/kb_111.md"] = [claim]
    return tasks, db


def migrate(data_dir, check=False):
    """Apply only to the specified snapshot; --check writes no files."""
    paths = [data_dir / "tasks.json", data_dir / "db.json"]
    originals = [json.loads(p.read_text()) for p in paths]
    tasks, db = with_contract(*originals)
    task = next(t for t in tasks if t["id"] == "Bank 54")
    body = (data_dir / "corpus/knowledge/kb_111.md").read_text()
    ev = task["evaluation_criteria"]["result_evidence"]
    existing = next((e for e in ev if e["evidence_id"] == "bank54-source:kb_111"), None)
    receipt = {
        "evidence_id": "bank54-source:kb_111",
        "tool_names": ["file_read"],
        "record_id": "kb_111",
        "expected_fields": {
            "path": "/knowledge/kb_111.md",
            "complete": True,
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "content": body,
        },
        "before_action_id": "accept_claim_Bank 54",
    }
    if existing is None:
        ev.append(receipt)
    else:
        existing.update(receipt)
    for path, before, after in zip(paths, originals, (tasks, db)):
        if before != after:
            if check:
                raise SystemExit(f"Bank54 hardening stale: {path}")
            path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    migrate(args.data_dir, check=args.check)
    print("Bank54 hardening current; refresh shared receipts after apply")


if __name__ == "__main__":
    main()
