"""Addressed Bank26 utility-template migration; run first on a copied data dir."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
WRONG = "40817810900001187"
CORRECT = "40817810900005521"
PAYEE = "Мосэнергосбыт"
TEMPLATE = "utility_electricity_2208"
HISTORY = [
    {"revision": "1", "effective_at": "2026-08-01", "payee": PAYEE, "account": WRONG},
    {"revision": "2", "effective_at": "2026-08-21", "payee": PAYEE, "account": CORRECT},
]
TICKET = (
    "Здравствуйте! Я Денис Соколов, телефон +7 903 220-84-51. В шаблоне «Электричество» "
    "был неправильный лицевой счёт, но я исправил его 21 августа. По этому шаблону "
    "ушли 4 780 ₽ 20 августа и 4 940 ₽ 22 августа. Не уверен, какой платёж уже был "
    "после исправления и куда именно ушли деньги. Вижу возврат 600 ₽ и ещё 300 ₽ "
    "в обработке, но не знаю, к каким платежам они относятся. Помогите оформить "
    "одно обращение на возврат оставшейся суммы именно ошибочных платежей. "
    "Реквизиты правильного и ошибочного лицевых счетов могу сообщить. Другой "
    "коммунальный платёж на 5 120 ₽ был правильным, его не включайте."
)
KB_PARAGRAPH = (
    "Для ЖКХ имя и текущая редакция шаблона не подтверждают реквизиты уже исполненного "
    "платежа. Сопоставьте неизменные execution_payee и execution_account каждой "
    "операции с получателем и верным/ошибочным лицевыми счетами, полученными от "
    "клиента. template_id помогает найти операции, а template_revision и "
    "template_effective_at показывают применённую редакцию. История template_history "
    "может помочь сверке; при достаточных деталях исполнения отдельное чтение "
    "истории не требуется. Включайте только платежи с фактически ошибочными "
    "реквизитами, даже если правильные платежи имеют тот же template_id. "
    "Изменение шаблона не меняет реквизиты старых операций."
)
POLICY_PARAGRAPH = (
    "Для коммунальных шаблонов сверяйте фактического получателя и лицевой счёт "
    "каждого исполненного платежа, а не нынешнюю редакцию или название шаблона. "
    "Используйте неизменные реквизиты исполнения и счета, полученные от клиента "
    "до регистрации. История редакций помогает установить изменение; полные "
    "детали исполнения достаточны и без отдельного чтения истории. Правильный "
    "платёж по тому же шаблону в требование не включается. Уменьшайте остаток "
    "только на posted-возврат с явной связью original_transaction_id; processing "
    "и возвраты по другим платежам не вычитайте."
)


def with_contract(tasks, db):
    """Preserve other tasks and rows, changing exactly Bank26 and its own payments."""
    tasks, db = deepcopy(tasks), deepcopy(db)
    selected = [t for t in tasks if t["id"] == "Bank 26"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 26")
    task = selected[0]
    task["ticket"] = TICKET
    ins = task["user_scenario"]["instructions"]
    ins["reason_for_call"] = TICKET
    ins["known_info"] = (
        TICKET
        + " Кодовое слово — невод; дата рождения — 1983-07-19. Называйте только по запросу идентификации."
    )
    task["description"]["notes"] = (
        "completion_only: идентификация и клиентские лицевые счета из полученных ответов; "
        "источники и неизменные реквизиты исполнения до первой попытки create_case. "
        "Одно обращение misdirected_utility_payment на 4180 RUB только по txn_889231. "
        "txn_889232 исполнен по исправленной редакции, txn_889240 также правильный. "
        "Вычесть только linked posted 600; linked processing 300 не вычитать. "
        "История шаблона необязательна при достаточных деталях операций. Получить "
        "регистрацию, затем done. Среда общая для всех задач."
    )
    for id, account, revision, effective in (
        ("txn_889231", WRONG, "1", "2026-08-01"),
        ("txn_889232", CORRECT, "2", "2026-08-21"),
        ("txn_889150", CORRECT, None, None),
        ("txn_889240", "40817810900007403", None, None),
    ):
        row = db["transactions"][id]
        if row["customer_id"] != "sokolov_d_2208":
            raise ValueError(f"Unexpected owner: {id}")
        row.update(
            execution_payee=PAYEE if id != "txn_889240" else "МосОблЕИРЦ",
            execution_account=account,
        )
        if revision:
            row.update(
                template_id=TEMPLATE,
                template_revision=revision,
                template_effective_at=effective,
                template_history=deepcopy(HISTORY),
            )
    for id, original in (
        ("txn_889233", "txn_889231"),
        ("txn_889234", "txn_889231"),
        ("txn_889241", "txn_889240"),
    ):
        row = db["transactions"][id]
        row["original_transaction_id"] = original
        if id == "txn_889234":
            row["merchant"] = (
                "Входящий возврат платежа ЖКХ в обработке по исходной операции txn_889231"
            )
    criteria = task["evaluation_criteria"]
    for action in criteria["actions"]:
        if action["name"] == "create_case":
            action["arguments"].update(
                amount=4180.0,
                transaction_id="txn_889231",
                transaction_ids=["txn_889231"],
            )
        if action["name"] == "calculate":
            action["arguments"]["expression"] = "4780 - 600"
    criteria["db_alternative_action_overrides"] = [
        {"create_case_Bank 26": {"transaction_id": None}}
    ]
    for evidence in criteria["result_evidence"]:
        id = evidence.get("record_id")
        if id in {"txn_889231", "txn_889232"}:
            row = db["transactions"][id]
            evidence["expected_fields"].update(
                {
                    k: row[k]
                    for k in (
                        "execution_payee",
                        "execution_account",
                        "template_id",
                        "template_revision",
                        "template_effective_at",
                    )
                }
            )
        if id in {"txn_889233", "txn_889234"}:
            row = db["transactions"][id]
            evidence["expected_fields"].update(
                original_transaction_id=row["original_transaction_id"],
                merchant=row["merchant"],
            )
        if evidence["evidence_id"] == "bank26_received_case":
            evidence["expected_fields"].update(
                amount=4180, transaction_ids=["txn_889231"]
            )
    for rule in criteria["forbidden_argument_patterns"]:
        if rule["rule_id"] == "wrong_create_case_transaction_id_Bank 26":
            # Retain known-target semantics; an absent singleton anchor is allowed.
            pattern = rule["pattern"]
            if "txn_889232" not in pattern:
                rule["pattern"] = pattern.replace("\\A(?:", "\\A(?:txn_889232|", 1)
        if rule["rule_id"] == "wrong_create_case_transaction_ids_Bank 26":
            rule["pattern"] = r'\A(?!\["txn_889231"\]\Z).+'
        if rule["rule_id"] == "wrong_create_case_amount_Bank 26":
            rule["pattern"] = r"^(?!4180(?:\.0+)?$).+"
    return tasks, db


def with_documents(documents):
    """Add the utility-specific rule without changing other transfer processes."""
    documents = deepcopy(documents)
    kb = "/knowledge/kb_180.md"
    policy = "/policies/transfers_fraud.md"
    if KB_PARAGRAPH not in documents[kb]:
        documents[kb] = documents[kb].replace(
            "## Исключения и передача", KB_PARAGRAPH + "\n\n## Исключения и передача"
        )
    if POLICY_PARAGRAPH not in documents[policy]:
        marker = "Банк не обещает сам исправить лицевой счёт получателя."
        if marker not in documents[policy]:
            raise ValueError("Utility policy anchor missing")
        documents[policy] = documents[policy].replace(
            marker, marker + "\n\n" + POLICY_PARAGRAPH
        )
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
        raise SystemExit("Bank26 hardening stale: " + ", ".join(stale))
    if args.apply:
        for k, p in paths.items():
            if before[k] != after[k]:
                p.write_text(json.dumps(after[k], ensure_ascii=False, indent=2) + "\n")
        for k, p in doc_paths.items():
            if doc_before[k] != doc_after[k]:
                p.write_text(doc_after[k])
    print("Bank26 hardening current; synchronize shared source receipts after --apply")


if __name__ == "__main__":
    main()
