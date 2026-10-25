"""Versioned v4 -> v5 author migration. Apply to a temporary copy before publishing."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from bank2_multisource_oracle import CASE, CUSTOMER, PERIOD, A, B
from bank2_resolution_oracle import calculate
from build_access_contract import with_contract as access
from build_discovery_contract import with_contract as discovery
from build_identity_contract import with_contract as identity
from upgrade_bank2_multisource import sync_manifest
from upgrade_composite_rewards import action, assertion, evidence

VERSION = "2026-10-15-bank2-resolution-v5"
PARENT = "2026-10-14-bank2-multisource-v4"
DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
TICKET = "Сегодня 28 августа 2026 года. Здравствуйте! Я Андрей Громов, телефон +7 926 118-77-30. Проверьте кешбэк за покупки в «Кофемании» 25 июля по чеку CF-2507-1187 и 26 июля на 1400 ₽: в этот день было два чека, нужный могу уточнить. Карту в июле перевыпускали, были возвраты и прежние выплаты. В приложении вижу разные предложения урегулирования. Хочу получить максимально возможную дополнительную сумму деньгами сейчас. Уточните мои ограничения и согласуйте итоговый вариант перед исполнением. Невозмещённую часть этих покупок добавьте в существующее дело, остальные его позиции сохраните. Новые продукты оформлять не прошу."
DOCS = {
    "reward_resolution_amounts": (
        "Суммы предложений урегулирования",
        """Для предложения cumulative договорная amount — совокупная сумма погашения по transaction_ids. Из неё вычитаются связанные проведённые cashback и compensates_cashback=true, включая поздние начисления, по обеим покупкам. Покупки, товарные возвраты и выплаты других покупок не входят в зачёт. Для incremental amount — только новая дополнительная выплата. Незавершённую обработку периода нужно завершить до исполнения; сравнение вариантов проводится на актуальных фактах. При цели максимизации сравнивают новые гарантированные рубли всех разрешённых вариантов, включая ручное полномочие. Суммы в клиентском сообщении не заменяют банковские записи. Распределение нового платежа: на allocation_first_transaction_id направить allocation_first_amount; остаток — второй покупке scope. Не превышать непогашенное обязательство за вычетом уже заявленного. [Права](reward_resolution_rights.md), [исполнение](reward_resolution_execution.md), [сверка](reward_payment_reconciliation.md).""",
    ),
    "reward_resolution_rights": (
        "Права и альтернативные способы урегулирования",
        """rights=preserve сохраняет право на оставшуюся недоплату и другие позиции дела; full_settlement предполагает окончательное урегулирование всего дела с отказом от оставшихся требований. Ограничения клиента нужно уточнить через case_clarification/resolution_preferences. Клиент, сохраняющий права, не разрешает full_settlement, даже если сумма выше. Новые продукты без поручения запрещены. Выплата по предложению и ручная корректировка — взаимоисключающие способы для пересекающегося scope покупок одного периода: нельзя получить оба, меняя ключ, порядок или предложение. Ранее назначенная обработка периода не является выбором способа. Предложение не расширяет ручное полномочие. Остатки по согласованным покупкам дополняют существующее дело; непорученные позиции сохраняются. [Суммы](reward_resolution_amounts.md), [дело](reward_case_amendment.md).""",
    ),
    "reward_resolution_execution": (
        "Согласие и исполнение предложения",
        """Допускается только status=approved банка, действующая version и срок предложения. under_review не разрешает выплату; слова клиента о согласовании этого не меняют. Согласие клиента — отдельное условие. ask_client reward_resolution_confirmation предъявляет offer_id/version, payouts, total_amount, case_id, resulting_claims (полный ожидаемый состав дела, включая сохранённые позиции) и state_revision. Клиент соглашается с предъявленным пакетом, не проверяет расчёт и не утверждает предложение от имени банка. Получите confirmation_id до execute_reward_resolution; подтверждение связано с точным пакетом и ревизией. При settlement_pending/state_changed/consent_stale обновите факты и при необходимости согласие: это восстанавливаемые состояния. Идемпотентный повтор с тем же ключом и пакетом возвращает прежние проводки; новый ключ не даёт права на вторую выплату. Исполнение возвращает ledger и новую ревизию, но само не меняет CRM. Получите результат до зависимого дополнения дела. Правильные альтернативы с равной максимальной дополнительной суммой допустимы. [Суммы](reward_resolution_amounts.md), [права](reward_resolution_rights.md).""",
    ),
}


def documents(data):
    path = data / "manifest.json"
    manifest = json.loads(path.read_text())
    for ident, (title, body) in DOCS.items():
        metadata = dict(
            id=ident,
            section="Акции и кешбэк",
            title=title,
            keywords=["кешбэк", "урегулирование", "предложения", "resolution"],
            revision_id="resolution-v1",
            effective_from="2026-06-01",
            effective_to=None,
            document_type="bank-knowledge",
            trust="bank-policy",
        )
        content = (
            "---\n"
            + "".join(
                k + ": " + json.dumps(v, ensure_ascii=False) + "\n"
                for k, v in metadata.items()
            )
            + "---\n\n# "
            + title
            + "\n\n"
            + body
            + "\n"
        )
        target = "/knowledge/" + ident + ".md"
        (data / "corpus" / target.lstrip("/")).write_text(content)
        manifest["documents"][target] = dict(
            sha256=hashlib.sha256(content.encode()).hexdigest(), metadata=metadata
        )
    for name in ("reward_product_scope", "reward_case_amendment"):
        target = "/knowledge/" + name + ".md"
        p = data / "corpus" / target.lstrip("/")
        text = p.read_text()
        link = "\n\nПри наличии банковских предложений доступны альтернативные способы: [суммы урегулирования](reward_resolution_amounts.md), [сохранение прав](reward_resolution_rights.md), [исполнение](reward_resolution_execution.md).\n"
        if link.strip() not in text:
            p.write_text(text.rstrip() + link)
        manifest["documents"][target]["sha256"] = hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def configure(task, db, data):
    calc = calculate(db)
    e = task["evaluation_criteria"]
    task.update(scenario_version=VERSION, ticket=TICKET)
    task["user_scenario"]["instructions"].update(
        reason_for_call=TICKET, known_info=TICKET
    )
    task["description"].update(
        purpose="Выбрать максимальную дополнительную разрешённую выплату, получить точное согласие, сохранить права и дополнить дело.",
        notes=VERSION + "; completion_only; model difficulty unconfirmed",
    )
    oldpay = "bank2:v4:pay"
    amend = "bank2:v4:amend"
    payid = "bank2:v5:pay"
    consentid = "bank2:v5:consent"
    actions = [
        a
        for a in e["actions"]
        if a["name"]
        not in (
            "execute_cashback_adjustment",
            "get_cashback_adjustment_status",
            "amend_cashback_investigation",
            "done",
        )
    ]
    actions += [
        action(
            "bank2:v5:offers",
            "get_reward_resolution_offers",
            dict(customer_id=CUSTOMER, case_id=CASE),
        ),
        action(
            "bank2:v5:preferences",
            "ask_client",
            dict(
                customer_id=CUSTOMER,
                request=dict(type="case_clarification", topic="resolution_preferences"),
            ),
        ),
    ]
    actions += [
        action(
            "bank2:v5:doc:" + name, "file_read", dict(path="/knowledge/" + name + ".md")
        )
        for name in DOCS
    ]
    payouts = [
        dict(transaction_id=k, amount=v / 100) for k, v in calc["payouts"].items()
    ]
    claims = [dict(transaction_id=k, amount=v / 100) for k, v in calc["claims"].items()]
    packet = dict(
        type="reward_resolution_confirmation",
        offer_id=calc["winners"][0],
        offer_version=db["reward_resolution_offers"][calc["winners"][0]]["version"],
        payouts=payouts,
        total_amount=calc["maximum"] / 100,
        case_id=CASE,
        resulting_claims=claims,
        state_revision=2,
    )
    if calc.get("component_ids"):
        packet["component_ids"] = calc["component_ids"]
    actions.append(
        action(
            consentid,
            "ask_client",
            dict(customer_id=CUSTOMER, request=packet),
            False,
            ["customer_id"],
            argument_values={"request.type": "reward_resolution_confirmation"},
        )
    )
    actions.append(
        action(
            payid,
            "execute_reward_resolution",
            dict(
                offer_id=calc["winners"][0],
                offer_version=db["reward_resolution_offers"][calc["winners"][0]][
                    "version"
                ],
                payouts=payouts,
                total_amount=calc["maximum"] / 100,
                case_id=CASE,
                confirmation_id="resolution-confirmation-1",
                expected_revision=2,
                idempotency_key="bank2-resolution",
            ),
            True,
            ["case_id"],
        )
    )
    actions.append(
        action(
            amend,
            "amend_cashback_investigation",
            dict(
                case_id=CASE,
                items=[x for x in claims if x["transaction_id"] in (A, B)],
                expected_revision=3,
            ),
            True,
            ["case_id"],
        )
    )
    if calc.get("component_ids"):
        next(a for a in actions if a["action_id"] == payid)["arguments"][
            "component_ids"
        ] = calc["component_ids"]
    e["actions"] = actions
    next(a for a in actions if a["action_id"] == payid)["result_patterns"] = [
        r'"total_amount"\s*:\s*'
        + str(calc["maximum"] / 100).replace(".", r"\.")
        + r"\s*[,}]"
    ]
    retained = []
    for item in e["result_evidence"]:
        if item.get("before_action_id") == oldpay:
            continue
        if "authority" in item["evidence_id"] and not item["evidence_id"].startswith(
            "common_"
        ):
            continue
        retained.append(item)
        if item.get("before_action_id") == amend:
            # Same factual basis for choosing amount; no irrelevant manual-authority gate.
            copy = deepcopy(item)
            copy["evidence_id"] += ":resolution"
            copy["before_action_id"] = payid
            copy.pop("target_item_ids", None)
            copy["only_if_target_attempted"] = True
            copy["recoverable_result_statuses"] = [
                "settlement_pending",
                "state_changed",
                "consent_stale",
            ]
            retained.append(copy)
    e["result_evidence"] = retained
    for item in e["result_evidence"]:
        path = item["expected_fields"].get("path")
        if path:
            item["expected_fields"]["sha256"] = hashlib.sha256(
                (data / "corpus" / path.lstrip("/")).read_bytes()
            ).hexdigest()
    for name in DOCS:
        e["result_evidence"].append(
            evidence(
                "bank2:v5:doc:" + name,
                ["file_read"],
                name,
                dict(
                    path="/knowledge/" + name + ".md",
                    complete=True,
                    sha256=hashlib.sha256(
                        (data / "corpus/knowledge" / (name + ".md")).read_bytes()
                    ).hexdigest(),
                ),
                payid,
                recoverable_result_statuses=[
                    "settlement_pending",
                    "state_changed",
                    "consent_stale",
                ],
            )
        )
    pref = db["reward_resolution_preferences"][CUSTOMER]
    e["result_evidence"].append(
        evidence(
            "bank2:v5:preferences",
            ["ask_client"],
            pref["id"],
            {k: v for k, v in pref.items() if k != "text"},
            payid,
        )
    )
    for oid, offer in db["reward_resolution_offers"].items():
        if offer["customer_id"] == CUSTOMER:
            e["result_evidence"].append(
                evidence(
                    "bank2:v5:offer:" + oid,
                    ["get_reward_resolution_offers"],
                    oid,
                    offer,
                    payid,
                )
            )
    e["result_argument_bindings"] = [
        x
        for x in e.get("result_argument_bindings", [])
        if x["target_action_id"] != oldpay
    ]
    e["result_argument_bindings"].append(
        dict(
            binding_id="bank2:v5:received-consent",
            source_action_id=consentid,
            target_action_id=payid,
            argument_name="confirmation_id",
            result_path=["confirmation_id"],
            only_if_target_attempted=True,
        )
    )
    # The received payment is required before the final claim, regardless of chosen offer ID.
    e["result_evidence"].append(
        evidence(
            "bank2:v5:received-payment",
            ["execute_reward_resolution"],
            "resolution:" + CASE,
            dict(customer_id=CUSTOMER, case_id=CASE, status="succeeded"),
            amend,
            only_if_target_attempted=True,
        )
    )
    e["env_assertions"] = [
        x
        for x in e["env_assertions"]
        if x["func_name"] != "assert_cashback_investigation"
    ]
    e["env_assertions"].append(
        assertion(
            "assert_reward_resolution_attempts",
            dict(customer_id=CUSTOMER, case_id=CASE),
        )
    )
    for key in (
        "db_alternative_action_replacements",
        "db_alternative_action_overrides",
        "db_alternative_action_additions",
    ):
        e[key] = []
    identity([task])
    discovery([task], db)
    return calc


def migrate(data):
    paths = [data / name for name in ("db.json", "tasks.json", "intake_answers.json")]
    db, tasks, answers = [json.loads(p.read_text()) for p in paths]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != PARENT:
        raise ValueError("Unsupported Bank2 parent")
    if db.get("reward_resolution_offers"):
        raise ValueError("Offer collision: migrate explicitly")
    db["reward_resolution_offers"] = {}
    for oid, amount, basis, status, rights in [
        ("offer_610711", 100, "cumulative", "approved", "full_settlement"),
        ("offer_610719", 95, "cumulative", "approved", "preserve"),
        ("offer_610723", 24, "incremental", "approved", "preserve"),
        ("offer_610729", 26, "incremental", "under_review", "preserve"),
    ]:
        db["reward_resolution_offers"][oid] = dict(
            id=oid,
            customer_id=CUSTOMER,
            case_id=CASE,
            period=PERIOD,
            transaction_ids=[A, B],
            version=1,
            amount=amount,
            amount_basis=basis,
            status=status,
            rights=rights,
            terms_id="reward_resolution_rights",
            allocation_terms_id="reward_resolution_amounts",
            allocation_first_transaction_id=A,
            allocation_first_amount=11,
            valid_from="2026-08-01",
            valid_to="2026-08-31",
        )
    db["reward_resolution_preferences"] = {
        CUSTOMER: dict(
            id="resolution-preferences-1187",
            customer_id=CUSTOMER,
            objective="maximize_additional_guaranteed_cash",
            currency="RUB",
            preserve_unpaid_claims=True,
            preserve_other_case_items=True,
            allow_new_products=False,
            customer_reported_offer_id="offer_610729",
            customer_reported_status="approved",
            text="Хочу максимум дополнительных гарантированных рублей сейчас, прежние выплаты второй раз не считайте. Сохраняю право на всю невозмещённую часть обеих покупок и остальные позиции дела. Новых продуктов не хочу. Мне кажется, предложение на 26 рублей уже одобрено; проверьте статус банка. Итоговый вариант согласуйте со мной.",
        )
    }
    # Remove superseded client instructions to pay the ordinary permission unconditionally.
    for a in answers[CUSTOMER]:
        if a.get("clarification_topic") == "existing_case":
            a["answer"] = (
                "Продолжайте существующее дело. Непорученные позиции сохраняйте; ограничения и итоговый вариант урегулирования уточните отдельно."
            )
    documents(data)
    configure(task, db, data)
    access(tasks, db)
    for p, value in zip(paths, (db, tasks, answers)):
        p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
    return db, tasks, answers


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir.resolve() == DATA.resolve():
        raise ValueError("Use a temporary copy first")
    migrate(args.data_dir)
