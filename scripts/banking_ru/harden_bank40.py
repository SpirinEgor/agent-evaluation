"""Addressed Bank40 billing-period migration; common receipts rebuilt separately."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
CUSTOMER = "solovyova_a_7734"
DUPLICATE = "txn_088219"
HISTORY = [
    {
        "product": "card_billing_cycle",
        "card_id": "card_7735",
        "effective_from": "2026-07-28",
        "effective_to": "2026-08-27",
        "revision_id": "standard-cycle-2026-07-28",
        "billing_anchor_day": 28,
        "billing_period": {"start": "2026-07-28", "end": "2026-08-27"},
        "qualifying_period": {"start": "2026-06-28", "end": "2026-07-27"},
        "avg_monthly_balance": 4200.0,
        "monthly_turnover": 8000.0,
        "monthly_fee": 199.0,
        "free_min_balance": 10000.0,
        "free_min_turnover": 30000.0,
    },
    {
        "product": "card_billing_cycle",
        "card_id": "card_7735",
        "effective_from": "2026-08-28",
        "effective_to": None,
        "revision_id": "standard-cycle-2026-08-28",
        "billing_anchor_day": 28,
        "billing_period": {"start": "2026-08-28", "end": "2026-09-27"},
        "qualifying_period": {"start": "2026-07-28", "end": "2026-08-27"},
        "avg_monthly_balance": 4200.0,
        "monthly_turnover": 8000.0,
        "monthly_fee": 249.0,
        "free_min_balance": 10000.0,
        "free_min_turnover": 30000.0,
    },
]
TICKET = "Здравствуйте! Я Анна Соловьёва, телефон +7 916 773-40-56. На одной карте вижу три платы за обслуживание: 199 ₽ 27 августа, затем две по 249 ₽ 28 августа. По другой карте есть ещё 199 ₽, её платёж сейчас не оспариваю. По одному из начислений уже зачислено 80 ₽, рядом возврат 30 ₽ в обработке. Проверьте договорные условия и периоды каждой комиссии и верните только ещё не возмещённую ошибочную часть, если ошибка подтверждается. Правомерные комиссии сохраните."
KB_RULE = """## Обслуживание с договорным расчётным периодом

Для продукта card_billing_cycle расчётный месяц определяется началом и концом
периода в операции, а не календарным месяцем или датой удержания. История договора
в профиле клиента содержит карту, даты действия редакций, день начала периода,
плату и пороги бесплатности. Применяется редакция, закреплённая на начало периода:
standard-cycle-2026-07-28 — 199 ₽, standard-cycle-2026-08-28 — 249 ₽. Новая цена
не применяется к предыдущему периоду. Пороги бесплатности обеих редакций:
средний остаток 10 000 ₽ либо оборот 30 000 ₽. Это отдельный сегмент «Стандарт»;
для остальных продуктов сохраняются общие условия выше. Плата начисляется
заранее на начало обслуживаемого периода; дата проведения удержания может быть
позже даты начисления. Бесплатность определяется по предыдущему завершённому
договорному периоду qualifying_period из истории договора, где сохранены
avg_monthly_balance и monthly_turnover именно этого периода. Незавершённый
обслуживаемый период не используется для оценки бесплатности. Недатированные
показатели профиля не заменяют эти договорные записи.

Две платы одной карты в календарном месяце могут обслуживать разные договорные
периоды и быть правомерными. Для каждого периода сохраните первое правомерное
начисление; повторная плата за тот же период и карту — ошибка. Сверяйте полный
период billing_period и редакцию tariff_revision операции с историей договора.
Вычитайте только проведённые связанные возвраты по этой ошибочной операции;
processing не уменьшает невозмещённый остаток, даже при совпадающей ссылке.
Верните остаток через refund_fee с request.type=fee_correction и явным amount.
"""
POLICY_RULE = """Для продукта card_billing_cycle расчётный период задаётся договором и полями
billing_period операции. Применяется тарифная редакция tariff_revision,
закреплённая на начало периода; историю дат, платы и порогов получите из профиля
клиента. Для этого продукта плата начисляется заранее на начало периода,
а удержание может быть проведено позднее. Условия бесплатности проверяются по
предыдущему завершённому qualifying_period; его средний остаток и оборот
сохранены в соответствующей договорной записи. Будущие показатели и недатированные
показатели профиля не заменяют эти данные. Календарный месяц и сегодняшняя цена
не заменяют договорный период и редакцию. Сохраните
первое правомерное начисление каждой карты за каждый договорный период и верните
только невозмещённую часть повторного начисления этого периода. Для остальных
продуктов правило расчётного месяца выше сохраняется.
"""


def with_contract(tasks, db):
    """Change exactly Bank40 and its declared scenario records."""
    tasks, db = deepcopy(tasks), deepcopy(db)
    selected = [t for t in tasks if t["id"] == "Bank 40"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 40")
    task = selected[0]
    task["ticket"] = TICKET
    instructions = task["user_scenario"]["instructions"]
    instructions["reason_for_call"] = TICKET
    instructions["known_info"] = (
        TICKET
        + " Кодовое слово — мозаика; дата рождения — 1992-07-08. Называйте только по запросу идентификации."
    )
    task["description"]["purpose"] = (
        "Определить периоды трёх комиссий и вернуть остаток только ошибочного повторного начисления."
    )
    task["description"]["notes"] = (
        "completion_only: получить применимые регламенты, показатели и историю договора своего клиента, периоды/редакции трёх комиссий и связи возвратов до первой попытки. txn_088214: 199 ₽ за 28.07–27.08; txn_088215: 249 ₽ за 28.08–27.09; обе правомерны. txn_088219: повторные 249 ₽ за 28.08–27.09. Связанный posted80 txn_088217 вычитается; processing30 txn_088218 не вычитается. Вернуть169 только по txn_088219; acc_7734=3951 ₽. Другая карта неизменна. Детали вместо списка, прямое чтение документов, отсутствие calculate и дополнительное чтение созданного возврата допустимы. Пороги бесплатности10000/30000, показатели4200/8000. Итоговый ответ не оценивается и не отправляется. Новые условия синтетические, общая среда одинакова для61. Основание действия: request.type=fee_correction; свободная причина не передаётся."
    )
    db["customers"][CUSTOMER]["servicing_contract_history"] = deepcopy(HISTORY)
    tx = db["transactions"]
    tx["txn_088214"].update(
        billing_period={"start": "2026-07-28", "end": "2026-08-27"},
        tariff_revision="standard-cycle-2026-07-28",
    )
    tx["txn_088215"].update(
        amount=249.0,
        billing_period={"start": "2026-08-28", "end": "2026-09-27"},
        tariff_revision="standard-cycle-2026-08-28",
    )
    tx[DUPLICATE] = deepcopy(tx["txn_088215"])
    tx[DUPLICATE].update(id=DUPLICATE, fee_refunded=False)
    tx["txn_088217"].update(
        original_transaction_id=DUPLICATE,
        merchant=f"Зачисленный частичный возврат комиссии по операции {DUPLICATE}",
    )
    tx["txn_088218"].update(
        original_transaction_id=DUPLICATE,
        card_id="card_7735",
        merchant=f"Ожидаемый возврат комиссии по операции {DUPLICATE}",
    )
    criteria = task["evaluation_criteria"]
    for action in criteria["actions"]:
        if action["name"] == "refund_fee":
            action["arguments"].update(transaction_id=DUPLICATE, amount=169.0)
    for assertion in criteria["env_assertions"]:
        if assertion["func_name"] == "assert_no_excess_fee_refund_attempts":
            assertion["arguments"]["transaction_id"] = DUPLICATE
    for rule in criteria["forbidden_argument_patterns"]:
        if rule["rule_id"] == "wrong_refund_fee_transaction_id_Bank 40":
            rule["pattern"] = f"^(?!{DUPLICATE}$).+"
        if rule["rule_id"] == "wrong_refund_fee_amount_Bank 40":
            rule["pattern"] = r"^(?!169(?:\.0+)?$).+"
    # Contract history already supplies prices and free thresholds. Neither the
    # generic tariff catalogue nor an undisputed other-card fee is necessary.
    evidence = criteria["result_evidence"]
    evidence[:] = [
        e for e in evidence if e["record_id"] not in {"tariff_standard", "txn_088216"}
    ]
    for receipt in evidence:
        record = receipt.get("record_id")
        if record == CUSTOMER and receipt["tool_names"] == ["get_customer_profile"]:
            receipt["expected_fields"] = {
                "servicing_contract_history": deepcopy(HISTORY)
            }
        if record in tx and "get_transactions" in receipt["tool_names"]:
            receipt["expected_fields"] = {
                k: deepcopy(tx[record].get(k))
                for k in [
                    "customer_id",
                    "account_id",
                    "card_id",
                    "date",
                    "amount",
                    "status",
                    "kind",
                    "original_transaction_id",
                    "billing_period",
                    "tariff_revision",
                ]
                if k in tx[record]
            }
    evidence[:] = [e for e in evidence if e["record_id"] != DUPLICATE]
    discovery_index = next(
        (
            i
            for i, e in enumerate(evidence)
            if e["evidence_id"] == "common_discovery_Bank 40"
        ),
        len(evidence),
    )
    evidence.insert(
        discovery_index,
        {
            "evidence_id": "bank40:duplicate-period",
            "tool_names": ["get_transactions", "get_transaction_details"],
            "record_id": DUPLICATE,
            "expected_fields": {
                k: deepcopy(tx[DUPLICATE][k])
                for k in [
                    "customer_id",
                    "account_id",
                    "card_id",
                    "date",
                    "amount",
                    "status",
                    "kind",
                    "billing_period",
                    "tariff_revision",
                ]
            },
            "before_action_id": "refund_fee_Bank 40",
            "after_action_id": "verify_Bank 40",
        },
    )
    return tasks, db


def migrate(data_dir, check=False):
    """Apply addressed changes to the supplied snapshot, never another directory."""
    paths = [data_dir / "tasks.json", data_dir / "db.json"]
    originals = [json.loads(p.read_text()) for p in paths]
    updated = with_contract(*originals)
    for path, before, after in zip(paths, originals, updated):
        if before != after:
            if check:
                raise SystemExit(f"Bank40 hardening stale: {path}")
            path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n")
    docs = {"knowledge/kb_121.md": KB_RULE, "policies/fees_rewards.md": POLICY_RULE}
    for relative, rule in docs.items():
        path = data_dir / "corpus" / relative
        before = path.read_text()
        old_phrase = "Назовите клиенту оба условия, его собственные показатели за месяц и разницу до каждого порога."
        new_phrase = "Сопоставьте оба условия, собственные показатели клиента и разницу до каждого порога."
        if relative.startswith("knowledge") and old_phrase in before:
            if check:
                raise SystemExit(f"Bank40 completion source stale: {path}")
            before = before.replace(old_phrase, new_phrase)
            path.write_text(before)
        if rule.strip() not in before:
            if check:
                raise SystemExit(f"Bank40 hardening source stale: {path}")
            if relative.startswith("knowledge"):
                heading = "## Обслуживание с договорным расчётным периодом"
                if heading in before:
                    prefix, old = before.split(heading, 1)
                    suffix = (
                        "\n## " + old.split("\n## ", 1)[1] if "\n## " in old else ""
                    )
                    after = prefix + rule + suffix
                else:
                    after = before.rstrip() + "\n\n" + rule
            else:
                start = "Для продукта card_billing_cycle"
                if start in before:
                    prefix, old = before.split(start, 1)
                    suffix = "## Смена тарифа" + old.split("## Смена тарифа", 1)[1]
                    after = prefix + rule + "\n" + suffix
                else:
                    after = before.replace(
                        "## Смена тарифа", rule + "\n## Смена тарифа"
                    )
            path.write_text(after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    migrate(args.data_dir, args.check)
    print(
        "Bank40 billing-period hardening current; rebuild common source/access receipts after applying"
    )


if __name__ == "__main__":
    main()
