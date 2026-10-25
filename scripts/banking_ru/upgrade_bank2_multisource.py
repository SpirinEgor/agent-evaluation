"""Idempotent Bank2 v3 -> v4 migration. Apply to a temporary copy before publishing."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from bank2_multisource_oracle import (
    CAPABILITY,
    CASE,
    COMPENSATION,
    CUSTOMER,
    LATE,
    PERIOD,
    PROMOTION,
    A,
    B,
    C,
    D,
    E,
    calculate,
)
from upgrade_composite_rewards import action, assertion, evidence

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/tau2/domains/banking_ru"
VERSION = "2026-10-14-bank2-multisource-v4"
PARENT = "2026-10-14-bank2-settlement-v3"
CONTRACT = "rc_610482"
PRODUCT = "cafe_rewards"
DOCS = {
    "reward_product_scope": (
        "Договор и программы вознаграждений",
        """Область расчёта определяется действующим договором вознаграждений: владелец, счёт, product_code, связанные card_ids, срок и rules_revision. Для продукта cafe_rewards в редакции multi-program-v1 доступны обычная и партнёрская программы. Параметры периода и лимита доступны в правилах клиента; условия программы определяются применимой редакцией документов. Ставки выбираются по [истории ставок](reward_rate_history.md), а совмещение — по [приоритету программ](reward_program_priority.md). Если карта заменена в течение периода, область общего лимита определяется договором; см. [перевыпуск](reward_card_reissue.md).""",
    ),
    "reward_rate_history": (
        "История ставок обычного кешбэка",
        """Для продукта cafe_rewards, редакция multi-program-v1, историческую ставку выбирают по дате покупки, включая обе границы действия. Для покупок июня 2026 ставка категории кафе — 10%, для покупок июля 2026 — 5%. Текущая ставка не подменяет историческую. Категория определяется по MCC; см. [категории](reward_merchant_categories.md). Дата покупки для ставки и дата проводки для [периода](reward_posting_period.md) могут относиться к разным месяцам. Применимость продукта проверяется по [договору](reward_product_scope.md).""",
    ),
    "reward_posting_period": (
        "Расчётный период по проводке",
        """В редакции multi-program-v1 период определяется местной датой posted_at проведённой покупки, а не датой её совершения. Границы posting_from и posting_to включительны. Время хранится с часовым смещением; банковская зона Europe/Moscow. Покупка июня, проведённая в июле, относится к июльскому периоду, сохраняя ставку июня. В расчёт входят проведённые покупки всех связанных карт договора; незавершённые операции не включаются. Полнота определяется подходящей выборкой операций, включая страницы, а не только покупками из обращения. См. [общий лимит](reward_standard_limits.md) и [историю ставок](reward_rate_history.md).""",
    ),
    "reward_standard_limits": (
        "Общий лимит обычного кешбэка",
        """Редакция multi-program-v1: обычный лимит задаётся на договор и расчётный период, общий для всех его карт. Обычные обязательства распределяются по времени проводки, при равенстве — лексикографически по стабильному ID. Для каждой покупки после возвратов рассчитывают ставку; округление выполняется до копеек по правилу половины вверх. Обязательство ограничивается остатком общего лимита; остаток уменьшается на обязательство, а не на фактически выплаченную сумму. Партнёрские покупки, заменяющие обычную программу, лимит не расходуют; см. [приоритет](reward_program_priority.md). См. [возвраты](reward_refund_adjustments.md) и [перевыпуск](reward_card_reissue.md).""",
    ),
    "reward_merchant_categories": (
        "MCC и продавец",
        """Для продукта cafe_rewards в редакции multi-program-v1 MCC 5812 относится к категории кафе. MCC определяет обычную категорию; название продавца само по себе не доказывает участие в партнёрском предложении. Для партнёрской программы необходимы условия предложения и фактическая активация клиента; см. [партнёрское предложение](reward_partner_offer.md). Чек и время покупки помогают различать покупки одного продавца одинаковой суммы.""",
    ),
    "reward_partner_offer": (
        "Партнёрское предложение Кофемании",
        """Предложение promo_730415 продукта cafe_rewards действует для покупок у продавца Кофемания с MCC 5812 с 1 по 31 июля 2026 включительно. Ставка — 10% суммы после проведённых связанных возвратов; максимум 100 рублей на покупку, округление до копеек половины вверх. Активация должна состояться не позднее точного момента покупки; отмена активации до покупки исключает участие. Данные активации берутся из банковского журнала, воспоминание клиента не заменяет точное время. Сравнивайте моменты с учётом часового смещения. Подходящая программа заменяет обычную; см. [приоритет](reward_program_priority.md) и [возвраты](reward_refund_adjustments.md).""",
    ),
    "reward_program_priority": (
        "Приоритет программ кешбэка",
        """В редакции multi-program-v1 подходящее партнёрское предложение заменяет обычный кешбэк этой покупки. Ставки двух программ не складываются. Партнёрское обязательство не расходует обычный лимит договора. При отсутствии применимого предложения покупка рассчитывается по обычной программе с её ставкой и лимитом. Проверка применимости основана на фактах предложения и активации; см. [условия предложения](reward_partner_offer.md), [историю ставок](reward_rate_history.md) и [лимит](reward_standard_limits.md).""",
    ),
    "reward_refund_adjustments": (
        "Возвраты и база вознаграждения",
        """Редакция multi-program-v1: проведённый возврат с original_transaction_id уменьшает базу исходной покупки до нуля, включая проведённые после периода возвраты не позднее settlement_as_of. Связь требует совпадения владельца, счёта и карты. Компенсация кешбэка с compensates_cashback=true не является возвратом товара, не уменьшает базу покупки и погашает соответствующее обязательство. Незавершённый возврат не уменьшает базу. См. [сверку выплат](reward_payment_reconciliation.md).""",
    ),
    "reward_card_reissue": (
        "Перевыпуск и договор вознаграждений",
        """Редакция multi-program-v1: связанные карты одного договора имеют общий лимит. Замена карты определяется полями replaced_card_id, replacement_card_id и replacement_effective_from договора. Перевыпуск не начинает новый период и не создаёт отдельный лимит. Проведённые покупки обеих карт учитываются в договоре согласно [периоду](reward_posting_period.md); счёт и владелец должны совпадать с договором. См. [область продукта](reward_product_scope.md).""",
    ),
    "reward_payment_reconciliation": (
        "Сверка выплат вознаграждения",
        """Редакция multi-program-v1: связанные проведённые обычные или партнёрские выплаты, компенсации обязательства и поздние начисления погашают обязательство соответствующей покупки. Все связи и принадлежность сверяются по исходной покупке; одна выплата не учитывается дважды. При ожидающей обработке периода актуальный результат обработки должен быть получен до зависимой корректировки. Повторная синхронизация не создаёт повторного начисления. Сумму и покупку нового начисления подтверждают обычные факты проведённой записи. Недоплата не бывает отрицательной; переплату автоматически не списывают. См. [полномочия](reward_adjustment_authority.md) и [дополнение дела](reward_case_amendment.md).""",
    ),
    "reward_adjustment_authority": (
        "Полномочия и неизвестное исполнение",
        """Редакция multi-program-v1: ручная выплата требует подтверждённой личности, совпадения владельца, покупки, программы и периода полномочия, статуса approved, действующего срока и доступного остатка. Выплата не превышает подтверждённую недоплату за вычетом уже заявленного или зарезервированного. Полученное собственное дело проверяют по [правилам дополнения](reward_case_amendment.md). Состояние обработки и state_revision должны быть актуальны; передавайте ожидаемую ревизию, обновляя её после изменений. Отказ state_changed или settlement_pending допускает обновление фактов и корректный повтор. execution_unknown не означает отсутствие исполнения. До независимого повторного исполнения и завершения подтвердите результат status, повтором того же ключа с теми же параметрами либо полными фактами проведённой банковской записи. Разделённые выплаты допустимы в пределах общего остатка, каждое исполнение подтверждается. См. [сверку выплат](reward_payment_reconciliation.md).""",
    ),
    "reward_case_amendment": (
        "Дополнение существующего дела",
        """Редакция multi-program-v1: дело cashback_reconciliation содержит покупки разных программ одного клиента и периода. items задаёт итог каждой переданной позиции, а не её прирост; непереданные позиции сохраняются. Независимые позиции клиент не разрешает удалять. Получите собственное открытое дело и актуальную версию до изменения. Суммы исключают проведённые выплаты и корректные резервы; допускается резерв согласованной ручной корректировки до выплаты. После исполнения резерв уменьшается, полностью исполненный закрывается. Раздельные дополнения допустимы при актуальных версиях. Не создавайте дубли; не включайте покупки вне поручения только потому, что у них вычислена недоплата. Перед завершением все порученные результаты получены, неизвестные исполнения установлены, открытых резервов нет. См. [полномочия](reward_adjustment_authority.md) и [сверку выплат](reward_payment_reconciliation.md).""",
    ),
}


def write_documents(data):
    manifest = json.loads((data / "manifest.json").read_text())
    for ident, (title, body) in DOCS.items():
        path = "/knowledge/" + ident + ".md"
        metadata = dict(
            id=ident,
            section="Акции и кешбэк",
            title=title,
            keywords=[
                "кешбэк",
                "вознаграждения",
                "multi-program-v1",
                *title.lower().split(),
            ],
            revision_id="multi-program-v1",
            effective_from="2026-06-01",
            effective_to=None,
            document_type="bank-knowledge",
            trust="bank-policy",
        )
        front = (
            "---\n"
            + "".join(
                k + ": " + json.dumps(v, ensure_ascii=False) + "\n"
                for k, v in metadata.items()
            )
            + "---\n\n"
        )
        content = front + "# " + title + "\n\n" + body + "\n"
        (data / "corpus" / path.lstrip("/")).write_text(content)
        manifest["documents"][path] = dict(
            sha256=hashlib.sha256(content.encode()).hexdigest(), metadata=metadata
        )
    notice = "\n\n## Область редакции\n\nПолный встроенный регламент обычного кешбэка относится только к posting-v2. Для multi-program-v1 применяются отдельные правила, начиная с [области продукта](reward_product_scope.md); встроенный текст не заменяет их.\n"
    for name in ("kb_standard_cashback_v2", "kb_cashback_adjustment_authority"):
        path = "/knowledge/" + name + ".md"
        target = data / "corpus" / path.lstrip("/")
        content = target.read_text()
        if notice.strip() not in content:
            content = content.rstrip() + notice
            target.write_text(content)
        descriptor = manifest["documents"][path]
        descriptor["sha256"] = hashlib.sha256(content.encode()).hexdigest()
        # New sources stay independent; do not extend legacy equivalence/inclusion.
    (data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )


def initialize(db, answers):
    requested = {
        "card_804217",
        CONTRACT,
        PROMOTION,
        "pa_730416",
        CAPABILITY,
        "cap_620197",
        CASE,
        C,
        E,
        A,
        B,
        D,
        LATE,
        COMPENSATION,
        *["txn_804" + str(i) for i in (241, 245, 249, 253, 257, 261, 269)],
    }
    existing = {
        key for value in db.values() if isinstance(value, dict) for key in value
    }
    if requested & existing:
        raise ValueError("New ID collision: " + ", ".join(sorted(requested & existing)))
    db["today"] = "2026-08-28"
    txs = db["transactions"]
    base = deepcopy(txs["txn_552290"])
    # Retire only data introduced for the replaced v2/v3 scenario; preserve other-period records.
    retired = {
        "txn_552290",
        "txn_552291",
        "txn_552298",
        "txn_552299",
        "txn_b2_similar",
        "txn_b2_background",
        "txn_b2_background_paid",
        "txn_b2_other_program",
        "txn_b2_processing",
    }
    retired.update(
        t["id"] for t in txs.values() if t.get("original_transaction_id") in retired
    )
    for ident in retired:
        txs.pop(ident, None)
    old = deepcopy(db["cards"]["card_1187"])
    db["cards"]["card_804217"] = {
        **old,
        "id": "card_804217",
        "last4": "4217",
        "reissue_status": None,
    }
    db.setdefault("reward_contracts", {})[CONTRACT] = dict(
        id=CONTRACT,
        customer_id=CUSTOMER,
        account_id="acc_1187",
        product_code=PRODUCT,
        card_ids=["card_1187", "card_804217"],
        effective_from="2026-06-01",
        effective_to=None,
        rules_revision="multi-program-v1",
        replaced_card_id="card_1187",
        replacement_card_id="card_804217",
        replacement_effective_from="2026-07-20",
    )
    for ident, card, date, time, posted, amount, merchant, receipt in [
        (C, "card_1187", "2026-06-30", "18:00", "2026-07-01", 300, "Кафе", None),
        (E, "card_1187", "2026-07-02", "12:00", "2026-07-02", 200, "Кафе", None),
        (
            A,
            "card_804217",
            "2026-07-25",
            "12:00",
            "2026-07-25",
            1200,
            "Кофемания",
            "CF-2507-1187",
        ),
        (
            B,
            "card_804217",
            "2026-07-26",
            "12:00",
            "2026-07-26",
            1400,
            "Кофемания",
            "CF-2607-1188",
        ),
        (
            D,
            "card_804217",
            "2026-07-26",
            "10:00",
            "2026-07-27",
            1400,
            "Кофемания",
            "CF-2607-1199",
        ),
    ]:
        txs[ident] = {
            **base,
            "id": ident,
            "card_id": card,
            "date": date,
            "purchased_at": date + "T" + time + ":00+03:00",
            "posted_at": posted + "T12:00:00+03:00",
            "amount": amount,
            "merchant": merchant,
            "order_id": receipt,
            "receipt_id": receipt,
            "mcc": "5812",
            "kind": "purchase",
            "status": "posted",
            "reward_program": None,
            "original_transaction_id": None,
            "cashback_granted": False,
        }
    for ident, purchase, kind, amount, compensation in [
        ("txn_804241", C, "cashback", 30, False),
        ("txn_804245", E, "cashback", 3, False),
        ("txn_804249", A, "refund", 200, False),
        ("txn_804253", A, "cashback", 20, False),
        ("txn_804257", B, "refund", 200, False),
        ("txn_804261", B, "cashback", 40, False),
        (COMPENSATION, A, "cashback", 5, True),
        ("txn_804269", D, "cashback", 5, False),
    ]:
        txs[ident] = {
            **txs[purchase],
            "id": ident,
            "date": "2026-08-05",
            "purchased_at": None,
            "posted_at": "2026-08-05T12:00:00+03:00",
            "kind": kind,
            "amount": amount,
            "order_id": None,
            "receipt_id": None,
            "original_transaction_id": purchase,
            "reward_program": "partner_cashback"
            if purchase == B and kind == "cashback"
            else "standard_cashback",
            "compensates_cashback": compensation,
            "merchant": "Компенсация кешбэка"
            if compensation
            else ("Возврат покупки" if kind == "refund" else "Начисление кешбэка"),
        }
    rules = db["cashback_rules"][CUSTOMER]
    rules["monthly_cap"] = 110
    rules["cycles"] = [
        dict(
            id="cycle_610482",
            rules_revision="multi-program-v1",
            contract_id=CONTRACT,
            period=PERIOD,
            program="standard_cashback",
            limit=110,
            card_ids=["card_1187", "card_804217"],
            posting_from="2026-07-01",
            posting_to="2026-07-31",
            settlement_as_of=db["today"],
        )
    ]
    for h in rules["categories_history"]:
        h["product_code"] = PRODUCT
    db["promotions"][PROMOTION] = dict(
        id=PROMOTION,
        name="Кофемания: партнёрский кешбэк",
        description="Предложение кафе",
        active=True,
        valid_from="2026-07-01",
        customer_id=CUSTOMER,
        code="CF_JULY_2026",
        reward=0,
        required_purchases=1,
        min_purchase_amount=0,
        valid_until="2026-07-31",
        product_code=PRODUCT,
        program="partner_cashback",
        merchant="Кофемания",
        mcc_codes=["5812"],
        rate=0.10,
        per_purchase_cap=100,
        activation_required=True,
        replaces_standard=True,
    )
    db.setdefault("promotion_activations", {})["pa_730416"] = dict(
        id="pa_730416",
        customer_id=CUSTOMER,
        promotion_id=PROMOTION,
        activated_at="2026-07-26T11:00:00+03:00",
        revoked_at=None,
    )
    permissions = db["cashback_adjustment_permissions"]
    permissions.pop("adjustment-A", None)
    permissions.pop("adjustment-other-purchase", None)
    for ident, tx in [(CAPABILITY, A), ("cap_620197", D)]:
        permissions[ident] = dict(
            id=ident,
            customer_id=CUSTOMER,
            transaction_id=tx,
            program="standard_cashback",
            period=PERIOD,
            max_amount=12,
            remaining_amount=12,
            used_amount=0,
            status="approved",
            valid_from="2026-08-01",
            valid_to="2026-08-31",
            used=False,
        )
    db["cases"].pop("case-standard-july", None)
    db["cases"][CASE] = dict(
        id=CASE,
        customer_id=CUSTOMER,
        category="cashback_investigation",
        reward_program="cashback_reconciliation",
        reward_period=PERIOD,
        status="open",
        created_at="2026-08-20",
        responsible_unit="rewards_operations",
        amount=17,
        transaction_id=A,
        transaction_ids=[A, E],
        reward_items=[
            dict(transaction_id=A, amount=10),
            dict(transaction_id=E, amount=7),
        ],
        version=1,
    )
    credit = {
        **txs["txn_804261"],
        "id": LATE,
        "amount": 7,
        "date": db["today"],
        "posted_at": "2026-08-28T12:00:00+03:00",
        "merchant": "Дополнительное банковское начисление",
    }
    db["cashback_settlements"]["july-additional-processing"] = dict(
        id="july-additional-processing",
        customer_id=CUSTOMER,
        period=PERIOD,
        status="pending",
        credits=[credit],
        ledger_ids=[],
    )
    db.setdefault("cashback_state_revisions", {})[CUSTOMER + ":" + PERIOD] = 1
    db.setdefault("cashback_execution_profiles", {})[CUSTOMER] = {
        "first_adjustment_unknown": True
    }
    db.setdefault("cashback_adjustment_operations", {})
    answers[CUSTOMER] = [
        a for a in answers[CUSTOMER] if not a.get("transaction_selection")
    ] + [
        dict(
            keywords=[],
            answer="Нашёл чек CF-2607-1188. В обращении имею в виду эту покупку.",
            transaction_selection=dict(
                id="customer-purchase-selection",
                merchant="Кофемания",
                amount=1400,
                date="2026-07-26",
                receipt="CF-2607-1188",
            ),
        ),
        dict(
            keywords=[],
            clarification_topic="promotion_activation",
            answer="Не помню точное время. Активировал предложение в приложении 26 июля.",
        ),
        dict(
            keywords=[],
            clarification_topic="request_scope",
            answer="Проверьте покупку по чеку CF-2507-1187 и покупку по чеку CF-2607-1188. Другие покупки сейчас добавлять в обращение не прошу.",
        ),
    ]


def with_promotion_source_alternatives(task, db, data):
    """A complete applicable bank document may supply the same fixed offer terms.

    Customer binding still requires the independent contract/activation receipts.
    Pin this alternative to the exact authored edition and matching raw terms;
    a changed offer or changed article must not silently inherit this proof.
    """
    path = "/knowledge/reward_partner_offer.md"
    actual_sha = hashlib.sha256(
        (data / "corpus" / path.lstrip("/")).read_bytes()
    ).hexdigest()
    known_sha = "c9bf60871510ec7470035c611523b0647660f3919a852c1fc12df07d1fdb6a19"
    terms = dict(
        id=PROMOTION,
        customer_id=CUSTOMER,
        product_code="cafe_rewards",
        program="partner_cashback",
        merchant="Кофемания",
        mcc_codes=["5812"],
        valid_from="2026-07-01",
        valid_until="2026-07-31",
        rate=0.1,
        per_purchase_cap=100,
        activation_required=True,
        replaces_standard=True,
        active=True,
    )
    row = db["promotions"].get(PROMOTION, {})
    eligible = actual_sha == known_sha and all(
        row.get(k) == v for k, v in terms.items()
    )
    for item in task["evaluation_criteria"].get("result_evidence", []):
        if (
            item["evidence_id"].startswith("bank2:v4:offer:")
            and item["record_id"] == PROMOTION
        ):
            item.pop("alternatives", None)
            if eligible:
                item["alternatives"] = [
                    dict(
                        tool_names=["file_read"],
                        record_id="reward_partner_offer",
                        expected_fields=dict(
                            path=path, complete=True, sha256=known_sha
                        ),
                    )
                ]


def configure(task, db, answers, data):
    selected = (
        D
        if next(x for x in answers[CUSTOMER] if x.get("transaction_selection"))[
            "transaction_selection"
        ]["receipt"]
        == "CF-2607-1199"
        else B
    )
    calc = calculate(db, selected)
    phone = db["customers"][CUSTOMER]["phone"]
    ticket = f"""Здравствуйте. Я Андрей Громов, телефон: {phone}. За июль неправильно начислили кешбэк за две покупки в «Кофемании». Первая — 25 июля, чек CF-2507-1187. Вторая — 26 июля на 1400 ₽, чек могу найти. В тот день было две похожие покупки.

Карту в июле перевыпускали. В приложении было партнёрское предложение, но я не помню, когда его активировал. По покупкам были частичные возвраты. Раньше уже обращался, какую-то компенсацию зачислили.

Проверьте эти две покупки. Разрешаю исправить подтверждённую недоплату в пределах банковских полномочий и дополнить существующее дело остальным остатком. Другие позиции дела не меняйте. Новое дело не создавайте, тариф не меняйте."""
    task.update(scenario_version=VERSION, ticket=ticket)
    task["user_scenario"]["instructions"].update(
        reason_for_call=ticket, known_info=ticket
    )
    task["description"].update(
        purpose="Сверка двух программ на связанных картах, частичная выплата и дополнение существующего дела.",
        notes=VERSION + "; completion_only; local unpublished.",
        relevant_policies="multi-program-v1; /knowledge/reward_product_scope.md",
    )
    e = task["evaluation_criteria"]
    e["actions"] = [
        a
        for a in e["actions"]
        if a["name"] in ("find_customer", "verify_identity")
        or a["name"] == "ask_client"
        and a["arguments"].get("request", {}).get("type") == "identity"
    ]

    def read(ident, name, args):
        e["actions"].append(action("bank2:v4:" + ident, name, args))

    read("date", "get_bank_date", {})
    read("cards", "get_cards", {"customer_id": CUSTOMER})
    read("contract", "get_reward_contracts", {"customer_id": CUSTOMER})
    read("rules", "get_cashback_rules", {"customer_id": CUSTOMER})
    read("offers", "get_promotions", {"customer_id": CUSTOMER})
    read("activations", "get_promotion_activations", {"customer_id": CUSTOMER})
    read(
        "purchases",
        "get_transactions",
        {
            "customer_id": CUSTOMER,
            "posted_from": "2026-07-01",
            "posted_to": "2026-07-31",
            "limit": 50,
        },
    )
    # Two broad related-record pages are unnecessary: precise posted filters return all eight records.
    read(
        "related",
        "get_transactions",
        {
            "customer_id": CUSTOMER,
            "posted_from": "2026-08-01",
            "posted_to": db["today"],
            "limit": 50,
        },
    )
    read(
        "selection",
        "ask_client",
        {
            "customer_id": CUSTOMER,
            "request": {
                "type": "transaction_selection",
                "merchant": "Кофемания",
                "amount": 1400,
            },
        },
    )
    read("cases", "get_cashback_investigations", {"customer_id": CUSTOMER})
    read("authority", "get_cashback_adjustment_permissions", {"customer_id": CUSTOMER})
    read(
        "refresh",
        "refresh_cashback_settlement",
        {"customer_id": CUSTOMER, "period": PERIOD},
    )
    read("late", "get_transaction_details", {"transaction_id": LATE})
    for ident in DOCS:
        read(ident, "file_read", {"path": "/knowledge/" + ident + ".md"})
    pay = None
    if calc["manual_payment"]:
        pay = action(
            "bank2:v4:pay",
            "execute_cashback_adjustment",
            dict(
                permission_id=CAPABILITY,
                transaction_id=A,
                amount=calc["manual_payment"] / 100,
                idempotency_key="bank2-v4-adjust",
                expected_revision=2,
            ),
            True,
            ["permission_id", "transaction_id"],
        )
        e["actions"].append(pay)
        read(
            "status",
            "get_cashback_adjustment_status",
            {"operation_id": "adjustment-operation:cashback-adjustment:" + CAPABILITY},
        )
    claim = action(
        "bank2:v4:amend",
        "amend_cashback_investigation",
        dict(
            case_id=CASE,
            items=[
                dict(transaction_id=k, amount=v / 100)
                for k, v in calc["case_items"].items()
                if k in (A, selected)
            ],
            expected_revision=3 if pay else 2,
        ),
        True,
        ["case_id"],
    )
    e["actions"].append(claim)
    e["action_order"] = []
    e["result_evidence"] = [
        x
        for x in e.get("result_evidence", [])
        if x["evidence_id"].startswith("common_discovery_")
    ]
    targets = [claim] + ([pay] if pay else [])
    txs = {
        **db["transactions"],
        LATE: db["cashback_settlements"]["july-additional-processing"]["credits"][0],
    }
    for target in targets:
        ident = target["action_id"]
        ispay = target is pay
        outcomes = [A] if ispay else [A, selected]

        def receipt(label, tools, record, fields, affected=None):
            options = dict(
                recoverable_result_statuses=["settlement_pending", "state_changed"]
            )
            if not ispay:
                options.update(
                    target_item_ids=affected or outcomes, only_if_target_attempted=True
                )
            completion_only = (
                not ispay
                and affected == [selected]
                and calc["rows"][selected]["gap"] == 0
            )
            if completion_only:
                options.pop("target_item_ids", None)
                options.pop("only_if_target_attempted", None)
            e["result_evidence"].append(
                evidence(
                    "bank2:v4:" + label + ":" + ident,
                    tools,
                    record,
                    fields,
                    None if completion_only else ident,
                    **options,
                )
            )

        for doc in DOCS:
            path = "/knowledge/" + doc + ".md"
            raw = (data / "corpus" / path.lstrip("/")).read_bytes()
            receipt(
                "doc:" + doc,
                ["file_read"],
                doc,
                {
                    "path": path,
                    "complete": True,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
                ([A] + ([selected] if selected == D else []))
                if doc
                in {
                    "reward_rate_history",
                    "reward_standard_limits",
                    "reward_merchant_categories",
                    "reward_card_reissue",
                    "reward_adjustment_authority",
                }
                else None,
            )
        receipt(
            "contract",
            ["get_reward_contracts"],
            CONTRACT,
            db["reward_contracts"][CONTRACT],
        )
        cycle = db["cashback_rules"][CUSTOMER]["cycles"][0]
        receipt(
            "cycle",
            ["get_cashback_rules"],
            cycle["id"],
            {k: v for k, v in cycle.items() if k != "contract_id"},
        )
        receipt(
            "case",
            ["get_cashback_investigations", "get_documents"],
            CASE,
            {
                k: db["cases"][CASE][k]
                for k in (
                    "customer_id",
                    "reward_program",
                    "reward_period",
                    "status",
                    "amount",
                    "reward_items",
                )
            },
        )
        receipt(
            "offer",
            ["get_promotions"],
            PROMOTION,
            {
                k: db["promotions"][PROMOTION][k]
                for k in (
                    "customer_id",
                    "valid_from",
                    "valid_until",
                    "rate",
                    "per_purchase_cap",
                    "merchant",
                    "mcc_codes",
                    "activation_required",
                    "replaces_standard",
                )
            },
        )
        receipt(
            "activation",
            ["get_promotion_activations"],
            "pa_730416",
            db["promotion_activations"]["pa_730416"],
        )
        receipt(
            "authority",
            ["get_cashback_adjustment_permissions"],
            CAPABILITY,
            db["cashback_adjustment_permissions"][CAPABILITY],
            [A],
        )
        needed = (
            [C, E, A] if ispay else ([C, E, A, B, D] if selected == D else [C, E, A, B])
        )
        # Different orders remain possible: only facts affecting each target item are required.
        for purchase in needed:
            for row in [
                txs[purchase],
                *[
                    x
                    for x in txs.values()
                    if x.get("original_transaction_id") == purchase
                ],
            ]:
                if row["id"] == LATE and ispay:
                    continue
                if purchase in (C, E) and row["kind"] != "purchase":
                    continue
                if purchase == B and selected == D and row["kind"] != "purchase":
                    continue
                affected = [purchase]
                if purchase in (C, E):
                    affected = [A] + ([selected] if selected == D else [])
                elif purchase == A and row["kind"] in {"purchase", "refund"}:
                    affected = [A] + ([selected] if selected == D else [])
                elif purchase == B and selected == D:
                    affected = [selected]
                fields = {
                    k: row.get(k)
                    for k in (
                        "customer_id",
                        "account_id",
                        "card_id",
                        "date",
                        "purchased_at",
                        "posted_at",
                        "amount",
                        "kind",
                        "status",
                        "original_transaction_id",
                        "mcc",
                        "order_id",
                        "reward_program",
                        "compensates_cashback",
                    )
                    if k in row
                }
                receipt(
                    "fact:" + row["id"],
                    ["get_transactions", "get_transaction_details"],
                    row["id"],
                    fields,
                    affected,
                )
        if not ispay:
            selection = next(
                x["transaction_selection"]
                for x in answers[CUSTOMER]
                if x.get("transaction_selection")
            )
            receipt(
                "selection",
                ["ask_client", "get_documents", "get_document_status"],
                selection["id"],
                {k: selection[k] for k in ("merchant", "amount", "date", "receipt")},
                [selected],
            )
    e["cashback_state_policy"] = {"customer_id": CUSTOMER, "period": PERIOD}
    e["env_assertions"] = [
        assertion(
            "assert_standard_reward_attempts",
            dict(customer_id=CUSTOMER, case_id=CASE, transaction_ids=[A, selected]),
        ),
        assertion(
            "assert_cashback_investigation",
            dict(
                customer_id=CUSTOMER,
                program="cashback_reconciliation",
                period=PERIOD,
                amount=calc["case_total"] / 100,
                items=[
                    dict(transaction_id=k, amount=v / 100)
                    for k, v in calc["case_items"].items()
                ],
            ),
        ),
        assertion(
            "assert_no_excess_cashback_adjustment_attempts",
            {"permission_id": CAPABILITY},
        ),
        assertion(
            "assert_cashback_reservations_closed",
            dict(customer_id=CUSTOMER, period=PERIOD),
        ),
    ]
    for key in (
        "db_alternative_action_replacements",
        "db_alternative_action_overrides",
        "db_alternative_action_additions",
    ):
        e[key] = []
    e["forbidden_actions"] = [
        a
        for a in e["forbidden_actions"]
        if a["name"] not in ("ask_client", "execute_cashback_adjustment")
    ]
    if not pay:
        e["forbidden_actions"].append(
            dict(
                action_id="bank2:v4:no-payment",
                name="execute_cashback_adjustment",
                arguments={},
                compare_args=[],
            )
        )
    from build_discovery_contract import with_contract as discovery
    from build_identity_contract import with_contract as identity

    identity([task])
    discovery([task], db)
    with_promotion_source_alternatives(task, db, data)
    return calc


def migrate(data):
    paths = [data / name for name in ("db.json", "tasks.json", "intake_answers.json")]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError(
            "Unsupported Bank2 parent version: " + str(task.get("scenario_version"))
        )
    initialize(db, answers)
    write_documents(data)
    configure(task, db, answers, data)
    for path, value in zip(paths, (db, tasks, answers)):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
    return db, tasks, answers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        db = json.loads((args.data_dir / "db.json").read_text())
        task = next(
            t
            for t in json.loads((args.data_dir / "tasks.json").read_text())
            if t["id"] == "Bank 2"
        )
        if task.get("scenario_version") != VERSION:
            raise SystemExit("Bank2 v4 migration pending")
        result = calculate(db)
        assert (
            result["manual_payment"] == 1200
            and result["case_total"] == 7300
            and result["balance_delta"] == 1900
        )
        print("Bank2 multisource v4 current; independent oracle agrees")
    elif args.apply:
        if args.data_dir.resolve() == DATA.resolve():
            raise ValueError(
                "Apply on a temporary copy first; publish reviewed files separately"
            )
        migrate(args.data_dir)


def apply_variant(data, kind):
    """Apply an explicitly specified offline control variant to an already upgraded copy."""
    from bank2_multisource_oracle import variant

    aliases = {
        "late_activation": "late_activation",
        "no_compensation": "without_compensation",
        "no_authority": "without_authority",
        "select_D": "select_other_receipt",
    }
    if kind not in aliases:
        raise ValueError("Unsupported control variant: " + kind)
    db = json.loads((data / "db.json").read_text())
    tasks = json.loads((data / "tasks.json").read_text())
    answers = json.loads((data / "intake_answers.json").read_text())
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") != VERSION:
        raise ValueError("Control variants require v4 version")
    db, selected = variant(db, aliases[kind])
    if selected == D:
        answer = next(x for x in answers[CUSTOMER] if x.get("transaction_selection"))
        answer["transaction_selection"]["receipt"] = "CF-2607-1199"
        answer["answer"] = (
            "Нашёл чек CF-2607-1199. В обращении имею в виду эту покупку."
        )
    configure(task, db, answers, data)
    for name, value in [
        ("db.json", db),
        ("tasks.json", tasks),
        ("intake_answers.json", answers),
    ]:
        (data / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
    return db, tasks, answers


def sync_manifest(data):
    manifest = json.loads((data / "manifest.json").read_text())
    manifest["legacy_db_sha256"] = hashlib.sha256(
        (data / "db.json").read_bytes()
    ).hexdigest()
    for name in ("db.json", "tasks.json", "intake_answers.json"):
        key = name.replace(".json", "_sha256")
        if key in manifest:
            manifest[key] = hashlib.sha256((data / name).read_bytes()).hexdigest()
    (data / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
