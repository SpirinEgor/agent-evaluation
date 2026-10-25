"""Author-only rapid candidates: validated financial parent plus final document workflow."""

import argparse
import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

from build_access_contract import with_contract as access
from upgrade_bank2_multisource import sync_manifest
from upgrade_composite_rewards import action, assertion, evidence

C = "gromov_a_1187"
CASE = "case_610903"
DOC = CASE + ":registration_confirmation"
NEW = "doc-contact-gromov-new-email"
OLD = "doc-contact-" + C
VERSION = "2026-10-21-bank2-document-packages-v23"

POLICY = """# Официальные документы и разрешённая доставка

Документ РО-13. Владелец: управление документооборота.
Редакция 2026-10-21-package-v1. Действует с 28 августа 2026 года.
Область: официальные подтверждения обращений. Это регламент симуляции банка.

## 1. Предмет поручения и границы доступа

Поручение разобраться с выплатой само по себе не означает поручения передать
документы. При явном запросе клиент получает официальное подтверждение своего
дела. Служебная диагностика остаётся внутренним материалом: согласие владельца,
письмо бухгалтера и название файла не меняют её классификацию.
Идентификация, владелец и связь документа с делом обязательны одновременно.

## 2. Связь с обращением

Перед запросом документов получите результат работы с делом и его идентификатор.
При расследовании выплат подходящее открытое дело определяется владельцем,
программой, периодом и уже учтёнными покупками. Дополнение добавляет недостающие
позиции; повтор учтённой покупки не увеличивает сумму. Другое обращение или
другой период не объединяются автоматически. При отсутствии подходящего дела
допускается новое с указанием программы и периода.

## 3. Допуск документа к выдаче

Выдаваемым является документ нужного дела с назначением registration_confirmation,
audience=customer и transmissible=true. Документ должен быть готов и актуален.
Изменение суммы или состава дела делает прежнее подтверждение неактуальным;
оно не подтверждает новое состояние даже при совпадении номера дела.
После изменения дела необходимы полученные сведения о новой версии. Нельзя
доставлять заведомо superseded, internal или чужой документ, включая попытку.

## 4. Канал владельца

Предпочтение нового адреса и согласие на отправку не подтверждают адрес.
Нужна регистрация успешного подтверждения контакта в банковском канале,
после чего сведения профиля должны показывать verified=true. Канал должен
входить в allowed_channels документа. Если подтверждение не состоялось,
используется прежний verified контакт только при разрешении клиента.
Нельзя заменить контакт профиля строкой адреса или послать документ бухгалтеру.

## 5. Подтверждение результата

Доставка выполняется deliver_case_document. Это отдельная операция документного
сервиса, а не обещание, текст в
вопросе клиенту или отметка о получении метаданных. При completion_only явно
запрошенная доставка остаётся частью поручения. Завершить работу можно после
полученного подтверждения delivered для нужного дела, контакта и версии.
share_document не заменяет официальную доставку документа дела.
Правила технических состояний и повторов: [протокол сервиса](../knowledge/kb_case_document_delivery.md).
"""
PROTOCOL = """# Протокол клиентских подтверждений дел

Владелец: управление документооборота. Редакция 2026-10-21-package-v1.
Область: получение и доставка подтверждений зарегистрированных обращений.
Регламент применяется совместно с [РО-13](../policies/document_delivery.md).

## Карточка документа

Подтверждение и служебная диагностика — разные документы. Номер дела связывает
документ с обращением; назначение, audience, transmissible, readiness, version
и allowed_channels определяют допустимость передачи. Нужны все условия РО-13,
а не только заголовок. Полные сведения доступны в документах дела или в
документах своего клиента; оба способа допустимы.

Дополнение открытого дела выпускает новое подтверждение итоговой суммы.
Предыдущая редакция сохраняется как superseded. Повтор неизменного дополнения
не увеличивает версию. Документ другого дела того же владельца не заменяет
подтверждение изменённого дела. Прежние непорученные позиции дела сохраняются.

## Состояния и неопределённость

preparing означает, что выдаваемая версия ещё не готова. Проверка статуса
возвращает полный документ и детерминированно завершает подготовку; это не
доставка и не основание регистрировать ещё одно дело. ERR_DOCUMENT_NOT_READY
и ERR_DOCUMENT_TEMPORARY означают отсутствие доставки. Получите статус или
повторите допустимую операцию с прежними параметрами и idempotency_key.
ERR_DOCUMENT_FORBIDDEN и ERR_CONTACT_UNVERIFIED не являются временным отказом.

outcome_unknown означает, что операция могла уже исполниться. Связанный
request_id позволяет получить фактический результат. Также допустим идентичный
повтор с тем же ключом. Только полученный delivered подтверждает завершение.
Новый ключ после возможной доставки — самостоятельный запрос и риск дубликата.
Тот же ключ нельзя использовать для другого документа, контакта или версии.
При неизвестном агенту изменении версии получите новые сведения и повторите
корректный запрос; заведомо прежнюю редакцию отправлять нельзя.

## Подтверждение контакта

В профиле контакта учитываются владелец, channel, address и verified. Наличие
неподтверждённого контакта не разрешает отправку. Структурированное подтверждение
в банковском канале может быть успешным или неуспешным; повторное чтение профиля
показывает фактический verified. Согласие в свободном тексте этого не заменяет.
При разрешённом клиентом запасном канале неуспех не мешает закончить поручение.
"""


def build(parent, destination, variant):
    if variant not in ["email", "fallback", "unknown"]:
        raise ValueError("Unknown candidate")
    shutil.copytree(parent, destination)
    db = json.loads((destination / "db.json").read_text())
    tasks = json.loads((destination / "tasks.json").read_text())
    answers = json.loads((destination / "intake_answers.json").read_text())
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task["scenario_version"] != "2026-10-21-bank2-dependency-programs-v21":
        raise ValueError("Expected validated v21 financial parent")
    original = deepcopy([t for t in tasks if t["id"] != "Bank 2"])
    db.setdefault("document_contacts", {})[NEW] = dict(
        id=NEW,
        customer_id=C,
        channel="verified_email",
        address="andrey.documents@example.org",
        verified=False,
    )
    db.setdefault("document_service_profiles", {})[C] = dict(
        confirmation_readiness="ready",
        confirmation_version="1",
        delivery_outcome_unknown=variant == "unknown",
        transient_delivery_failures=0,
    )
    # Existing issue creates a real version dependency across the later amendment.
    for purpose, audience, transmissible in [
        ("registration_confirmation", "customer", True),
        ("internal_diagnostics", "internal", False),
    ]:
        key = CASE + ":" + purpose
        db["documents"][key] = dict(
            id=key,
            customer_id=C,
            case_id=CASE,
            title="Подтверждение регистрации"
            if transmissible
            else "Внутренняя диагностика",
            body="Дело " + CASE + "; программа cashback_reconciliation; сумма 17 ₽.",
            created_at="2026-08-20",
            shared=False,
            purpose=purpose,
            audience=audience,
            transmissible=transmissible,
            version="1",
            readiness="ready",
            allowed_channels=["verified_sms", "verified_email"]
            if transmissible
            else [],
        )
    answer = dict(
        keywords=[],
        answer="Подтверждение в банковском канале завершено."
        if variant != "fallback"
        else "Новый контакт подтвердить не удалось. Разрешаю прежний подтверждённый канал.",
        document_channel_confirmation=dict(
            contact_id=NEW, confirmed=variant != "fallback"
        ),
    )
    answers[C].append(answer)
    if C in db.get("client_answers", {}):
        db["client_answers"][C].append(deepcopy(answer))
    task["ticket"] += (
        " После завершения изменений пришлите актуальное официальное клиентское подтверждение итоговой суммы этого дела на andrey.documents@example.org. Адрес новый, подтверждение могу пройти в банковском канале. Если подтвердить не получится, разрешаю прежний подтверждённый канал профиля. Старую версию подтверждения вместо обновлённой не присылайте."
    )
    task["user_scenario"]["instructions"].update(
        reason_for_call=task["ticket"], known_info=task["ticket"]
    )
    task["scenario_version"] = VERSION + "-" + variant
    task["description"]["notes"] = (
        task["scenario_version"]
        + "; financial parent v21 preserved; scope Bank2 only; difficulty unmeasured"
    )
    contact = OLD if variant == "fallback" else NEW
    ec = task["evaluation_criteria"]
    ec["actions"] += [
        action(
            "bank2:package:policy",
            "file_read",
            dict(path="/policies/document_delivery.md"),
        ),
        action(
            "bank2:package:protocol",
            "file_read",
            dict(path="/knowledge/kb_case_document_delivery.md"),
        ),
        action(
            "bank2:package:contacts-before",
            "get_document_delivery_contacts",
            dict(customer_id=C),
        ),
        action(
            "bank2:package:confirm",
            "ask_client",
            dict(
                customer_id=C,
                request=dict(type="document_channel_confirmation", contact_id=NEW),
            ),
        ),
        action(
            "bank2:package:contacts",
            "get_document_delivery_contacts",
            dict(customer_id=C),
        ),
        action("bank2:package:metadata", "get_case_documents", dict(case_id=CASE)),
        action(
            "bank2:package:deliver",
            "deliver_case_document",
            dict(
                document_id=DOC,
                contact_id=contact,
                idempotency_key="bank2-package",
                expected_version="2",
            ),
            compare=[],
        ),
        action(
            "bank2:package:receipt",
            "get_bank_operation_status"
            if variant == "unknown"
            else "deliver_case_document",
            dict(customer_id=C, request_id="document-operation:" + DOC)
            if variant == "unknown"
            else dict(
                document_id=DOC,
                contact_id=contact,
                idempotency_key="bank2-package",
                expected_version="2",
            ),
            required=True,
            compare=[],
            alternative_tool_names=["deliver_case_document"]
            if variant == "unknown"
            else ["get_bank_operation_status"],
            result_contains=[
                '"status": "delivered"',
                '"document_id": "' + DOC + '"',
                '"contact_id": "' + contact + '"',
            ],
        ),
    ]
    ec["env_assertions"] += [
        assertion(
            "assert_case_document_delivered",
            dict(customer_id=C, program="cashback_reconciliation"),
        ),
        assertion(
            "assert_case_document_delivery_attempts",
            dict(customer_id=C, program="cashback_reconciliation"),
        ),
    ]
    ec["result_evidence"] += [
        evidence(
            "bank2:package:current-document",
            ["get_case_documents", "get_documents", "get_document_status"],
            DOC,
            dict(
                customer_id=C,
                case_id=CASE,
                purpose="registration_confirmation",
                audience="customer",
                transmissible=True,
                body="Дело "
                + CASE
                + "; программа cashback_reconciliation; период 2026-07; сумма 27.25 ₽.",
                readiness="ready",
                allowed_channels=["verified_sms", "verified_email"],
            ),
            "bank2:package:deliver",
            only_if_target_attempted=True,
            after_action_id="bank2:v4:amend",
        ),
        evidence(
            "bank2:package:verified-contact",
            ["get_document_delivery_contacts"],
            contact,
            dict(
                customer_id=C,
                verified=True,
                channel="verified_sms" if variant == "fallback" else "verified_email",
            ),
            "bank2:package:deliver",
            only_if_target_attempted=True,
        ),
    ]
    if variant == "fallback":
        ec["result_evidence"].append(
            evidence(
                "bank2:package:fallback-condition",
                ["ask_client"],
                C,
                dict(
                    customer_id=C,
                    request_type="document_channel_confirmation",
                    contact_id=NEW,
                    status="failed",
                ),
                "bank2:package:deliver",
                only_if_target_attempted=True,
                target_argument_options={"contact_id": [OLD]},
            )
        )
    # Business sources retain all conditions, without a task-specific solution recipe.
    for virtual, body in [
        ("policies/document_delivery.md", POLICY),
        ("knowledge/kb_case_document_delivery.md", PROTOCOL),
    ]:
        (destination / "corpus" / virtual).write_text(body)
    manifest = json.loads((destination / "manifest.json").read_text())
    for virtual in [
        "policies/document_delivery.md",
        "knowledge/kb_case_document_delivery.md",
    ]:
        descriptor = manifest["documents"]["/" + virtual]
        descriptor["sha256"] = hashlib.sha256(
            (destination / "corpus" / virtual).read_bytes()
        ).hexdigest()
        descriptor["metadata"]["revision_id"] = "2026-10-21-package-v1"
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    access([task], db)
    assert [t for t in tasks if t["id"] != "Bank 2"] == original
    for name, value in [
        ("db.json", db),
        ("tasks.json", tasks),
        ("intake_answers.json", answers),
    ]:
        (destination / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        )
    sync_manifest(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    for variant in ["email", "fallback", "unknown"]:
        print(build(args.parent, args.out / variant, variant))
