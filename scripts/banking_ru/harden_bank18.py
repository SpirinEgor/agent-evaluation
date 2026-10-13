"""Addressed Bank18 recipient/route/refund migration; apply to copied data first."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OWNER = "egorova_t_2266"
SOURCES = ["txn_844217", "txn_844219"]
TICKET = "Здравствуйте! Я Татьяна Егорова, телефон +7 926 226-64-05. 27 августа через сохранённый контакт отправила по СБП 50 000 ₽ и ещё 8 000 ₽: оба перевода предназначались Смирновой Елене, но ушли одному Смирнову Александру в банк «Восток». Вижу возврат 12 000 ₽, ещё 3 000 ₽ в обработке и похожие входящие 8 000 ₽ от однофамильца; не уверена, что уже вернулось именно по моим ошибочным переводам. Рядом есть перевод 8 000 ₽ другому Смирнову и покупка 8 000 ₽ по торговому QR — их признаю, в заявление не включайте. Проверьте операции и зарегистрируйте одно обращение на оставшуюся сумму двух ошибочных переводов; исполненные переводы напрямую не отменяйте."
PARAGRAPH = "Для ошибочных переводов физлицу используйте фактические recipient_id и payment_route каждой операции: похожее имя не подтверждает одного получателя, а торговый QR относится к оплате товара, а не к переводу физлицу. В одно misdirected_transfer включайте только исходные ошибочные переводы одному recipient_id по одному механизму. В transaction_ids сохраняйте все исходные переводы группы; возвраты в этот перечень не добавляйте. Сумма требования равна сумме исходных переводов за вычетом только posted-возвратов с явной связью original_transaction_id на них. Возвраты processing и поступления по иным исходным операциям не вычитайте. Список истории и детали операций равноценны при наличии этих полей. Якорь transaction_id может быть любой исходной операцией группы или отсутствовать при полном transaction_ids. Исполненные переводы нельзя отменять снятием холда или прямым возвратом из первой линии; регистрация Просьбы СБП не означает возврат денег."


def with_contract(tasks, db):
    """Change exactly Bank18 and its transaction rows, preserving other content."""
    tasks, db = deepcopy(tasks), deepcopy(db)
    selected = [t for t in tasks if t["id"] == "Bank 18"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 18")
    task = selected[0]
    task["ticket"] = TICKET
    task["description"]["purpose"] = (
        "Одна Просьба СБП по двум ошибочным переводам одному получателю, остаток 46000 RUB."
    )
    task["description"]["notes"] = (
        "completion_only: после полученной идентификации, РО-05 и kb_180 получить список/детали исходных операций и возвратов. Группа txn_844217+txn_844219 имеет одинаковые recipient_id и payment_route=sbp; posted txn_844218 связан с первой, processing txn_844220 со второй. Несвязанный txn_844223, однофамилец txn_844221 и признанный merchant_qr txn_844222 исключаются. Создать ровно одно открытое misdirected_transfer на 46000 RUB с обоими исходными ID в любом порядке; якорь любой из них или отсутствует. Ошибочная сумма/группа запрещена во всех попытках, исправление не отменяет нарушение; исправимый отказ API в необязательных metadata допустим. Получить результат и done; остальные записи/балансы неизменны. calculate необязателен, финальный ответ не оценивается."
    )
    ins = task["user_scenario"]["instructions"]
    ins["reason_for_call"] = TICKET
    ins["known_info"] = (
        TICKET
        + " Кодовое слово — клевер; дата рождения — 1982-06-11. Называйте только по запросу идентификации."
    )
    rows = db["transactions"]
    base = deepcopy(rows["txn_844217"])
    if base["customer_id"] != OWNER:
        raise ValueError("Unexpected Bank18 owner")
    rows["txn_844217"].update(
        recipient_id="recipient_smirnov_alexander_vostok", payment_route="sbp"
    )
    rows["txn_844218"].update(original_transaction_id="txn_844217", payment_route="sbp")
    rows["txn_844100"].update(
        recipient_id="recipient_smirnova_elena_vostok", payment_route="sbp"
    )
    specs = [
        (
            "txn_844219",
            8000,
            "Перевод по СБП, Смирнов А. А., банк «Восток»",
            "posted",
            "transfer",
            "sbp",
            "recipient_smirnov_alexander_vostok",
            None,
        ),
        (
            "txn_844220",
            3000,
            "Входящий возврат СБП в обработке по txn_844219",
            "processing",
            "transfer",
            "sbp",
            None,
            "txn_844219",
        ),
        (
            "txn_844221",
            8000,
            "Перевод по СБП, Смирнов А. А., банк «Восток»",
            "posted",
            "transfer",
            "sbp",
            "recipient_smirnov_andrey_vostok",
            None,
        ),
        (
            "txn_844222",
            8000,
            "Торговый QR, Смирнов А. А.",
            "posted",
            "purchase",
            "merchant_qr",
            "merchant_smirnov_shop",
            None,
        ),
        (
            "txn_844223",
            8000,
            "Входящий возврат СБП, Смирнов А. А.",
            "posted",
            "transfer",
            "sbp",
            None,
            "txn_844221",
        ),
    ]
    for id, amount, merchant, status, kind, route, recipient, original in specs:
        if id in rows and rows[id]["customer_id"] != OWNER:
            raise ValueError(f"Collision: {id}")
        row = deepcopy(base)
        row.update(
            id=id,
            amount=float(amount),
            merchant=merchant,
            status=status,
            kind=kind,
            payment_route=route,
            recipient_id=recipient,
            original_transaction_id=original,
        )
        rows[id] = row
    c = task["evaluation_criteria"]
    for action in c["actions"]:
        if action["name"] == "create_case":
            action["arguments"].update(
                amount=46000.0, transaction_id=SOURCES[0], transaction_ids=SOURCES
            )
        if action["name"] == "calculate":
            action["arguments"]["expression"] = "50000 + 8000 - 12000"
    c["db_alternative_action_overrides"] = [
        {"create_case_Bank 18": {"transaction_id": id}} for id in (None, SOURCES[1])
    ]
    for assertion in c["env_assertions"]:
        if assertion["func_name"] == "assert_case_exists":
            assertion["arguments"].pop("transaction_id", None)
            assertion["arguments"].update(
                expected_amount=46000.0, transaction_ids=SOURCES
            )
    c["result_evidence"] = [
        e
        for e in c["result_evidence"]
        if not (
            e.get("record_id", "").startswith("txn_844")
            and not e["evidence_id"].startswith("bank18_group:")
        )
    ]
    for id in ("txn_844217", "txn_844218", "txn_844219", "txn_844220"):
        row = rows[id]
        fields = {
            k: row[k]
            for k in (
                "customer_id",
                "amount",
                "date",
                "merchant",
                "status",
                "kind",
                "payment_route",
            )
        }
        if row.get("recipient_id"):
            fields["recipient_id"] = row["recipient_id"]
        if row.get("original_transaction_id"):
            fields["original_transaction_id"] = row["original_transaction_id"]
        replacement = {
            "evidence_id": f"bank18_group:{id}",
            "tool_names": ["get_transactions", "get_transaction_details"],
            "record_id": id,
            "expected_fields": fields,
            "before_action_id": "create_case_Bank 18",
            "after_action_id": "verify_Bank 18",
        }
        existing_index = next(
            (
                i
                for i, e in enumerate(c["result_evidence"])
                if e["evidence_id"] == replacement["evidence_id"]
            ),
            None,
        )
        if existing_index is None:
            c["result_evidence"].append(replacement)
        else:
            c["result_evidence"][existing_index] = replacement
    for evidence in c["result_evidence"]:
        if evidence["evidence_id"] == "completion:Bank 18:received_case":
            evidence["expected_fields"].pop("transaction_id", None)
            evidence["expected_fields"].update(amount=46000.0, transaction_ids=SOURCES)
    for field in ("transaction_ids", "amount"):
        rule_id = f"bank18_missing_claim_{field}"
        rule = {
            "action_id": rule_id,
            "name": "create_case",
            "arguments": {},
            "compare_args": [],
            "argument_options": {field: [None]},
        }
        existing = next(
            (
                i
                for i, item in enumerate(c["forbidden_actions"])
                if item["action_id"] == rule_id
            ),
            None,
        )
        if existing is None:
            c["forbidden_actions"].append(rule)
        else:
            c["forbidden_actions"][existing] = rule
    for rule in c["forbidden_argument_patterns"]:
        if rule["rule_id"] == "wrong_create_case_amount_Bank 18":
            rule["pattern"] = r"^(?!46000(?:\.0+)?$).+"
        if rule["rule_id"] == "wrong_create_case_transaction_id_Bank 18":
            wrong = sorted(
                id
                for id, row in rows.items()
                if row["customer_id"] == OWNER and id not in SOURCES
            )
            rule["pattern"] = r"\A(?:" + "|".join(wrong) + r")\Z"
        if rule["rule_id"] == "completion:Bank 18:only_requested_list":
            rule["pattern"] = (
                r'\A(?!\["txn_844217", "txn_844219"\]\Z|\["txn_844219", "txn_844217"\]\Z)[\s\S]*\Z'
            )
    return tasks, db


def with_documents(documents):
    """Explain shared grouping semantics in applicable sources without task IDs."""
    documents = deepcopy(documents)
    for path in ("/knowledge/kb_180.md", "/policies/transfers_fraud.md"):
        if PARAGRAPH not in documents[path]:
            documents[path] += "\n\n" + PARAGRAPH + "\n"
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.apply == args.check:
        parser.error("Choose --apply or --check")
    paths = {"tasks": args.data_dir / "tasks.json", "db": args.data_dir / "db.json"}
    before = {k: json.loads(p.read_text()) for k, p in paths.items()}
    tasks, db = with_contract(before["tasks"], before["db"])
    after = {"tasks": tasks, "db": db}
    doc_paths = {
        p: args.data_dir / "corpus" / p.lstrip("/")
        for p in ("/knowledge/kb_180.md", "/policies/transfers_fraud.md")
    }
    doc_before = {k: p.read_text() for k, p in doc_paths.items()}
    doc_after = with_documents(doc_before)
    stale = [k for k in before if before[k] != after[k]] + [
        k for k in doc_before if doc_before[k] != doc_after[k]
    ]
    if args.check and stale:
        raise SystemExit("Bank18 hardening stale: " + ", ".join(stale))
    if args.apply:
        for k, p in paths.items():
            if before[k] != after[k]:
                p.write_text(json.dumps(after[k], ensure_ascii=False, indent=2) + "\n")
        for k, p in doc_paths.items():
            if doc_before[k] != doc_after[k]:
                p.write_text(doc_after[k])
    print("Bank18 hardening current; synchronize shared source receipts after --apply")


if __name__ == "__main__":
    main()
