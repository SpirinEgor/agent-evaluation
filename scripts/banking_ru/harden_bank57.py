"""Address Bank57's one-contract, two-mechanism cancellation scenario.

Only Bank57 subject criteria change. Source receipt generators must subsequently
refresh all declared shared-source pins; this migration never rewrites other tasks.
Use a temporary --data-dir first. No model executions are performed.
"""

import argparse
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OWNER = "orlova_m_4400"
TARGET_SUB = "sub_4401"
TARGET_AP = "ap_4401"
OTHER_SUB = "sub_4403"
CONTRACT = "ivi-personal-4400"
PROFILE = "Основной личный профиль"
POLICY_PATH = "/policies/subscriptions.md"
POLICY_PARAGRAPH = (
    "Поручение прекратить все будущие платежи по одному договору/профилю сервиса "
    "охватывает связанные с ним карточные разрешения и банковские автоплатежи, "
    "включая резервный механизм. Связь устанавливается по merchant_id и "
    "contract_reference; одно название продавца и одинаковая сумма не определяют "
    "договор. Другой профиль того же продавца сохраняется, если клиент его не "
    "включил в поручение. Если профиль однозначно указан в обращении и подтверждён "
    "записями, повторное согласие не требуется. При неоднозначности используйте "
    "структурированный запрос subscription_selection для данного merchant_id "
    "и получите выбор клиента до первой попытки отключения. Разрешение и "
    "автоплатёж выбранного договора можно отключить в любом порядке после "
    "получения применимого регламента, авторизации и связанных записей."
)
TICKET = (
    "Здравствуйте! Я Марина Орлова, телефон +7 902 440-07-19. Хочу прекратить "
    "все будущие платежи за «Иви» только по моему основному личному профилю, "
    "которым больше не пользуюсь, в том числе резервные способы оплаты этого "
    "профиля. Другой, семейный профиль «Иви» оставляю. «Кинопоиск» и "
    "автопополнение телефона тоже нужны. Уже прошедшие покупки возвращать "
    "не прошу. Договор с сервисом я прекращу сама."
)


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _action(action_id, name, arguments):
    return {
        "action_id": action_id,
        "name": name,
        "arguments": arguments,
        "compare_args": list(arguments),
        "required_for_reward": True,
    }


def _evidence(evidence_id, record_id, fields, target):
    return {
        "evidence_id": evidence_id,
        "tool_names": ["get_subscriptions"],
        "record_id": record_id,
        "expected_fields": fields,
        "before_action_id": target,
        "after_action_id": "verify_Bank 57",
    }


def build(data):
    """Build immutable scoped output before applying any filesystem mutation."""
    tasks = json.loads((data / "tasks.json").read_text())
    selected = [t for t in tasks if t["id"] == "Bank 57"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 57")
    task = selected[0]
    db = json.loads((data / "db.json").read_text())
    intake = json.loads((data / "intake_answers.json").read_text())
    manifest = json.loads((data / "manifest.json").read_text())
    target = db["subscriptions"][TARGET_SUB]
    if target["customer_id"] != OWNER:
        raise ValueError("Bank57 subscription owner differs from declared scenario")
    target.update(
        merchant_id="merchant_ivi", contract_reference=CONTRACT, profile_label=PROFILE
    )
    other = deepcopy(target)
    other.update(
        id=OTHER_SUB,
        contract_reference="ivi-family-4400",
        profile_label="Семейный профиль",
        next_charge_date="2026-09-14",
    )
    db["subscriptions"][OTHER_SUB] = other
    db["autopayments"][TARGET_AP] = {
        "id": TARGET_AP,
        "customer_id": OWNER,
        "merchant": "Иви — резервная оплата основного личного профиля",
        "amount": 399.0,
        "status": "active",
        "cancelled_at": None,
        "merchant_id": "merchant_ivi",
        "contract_reference": CONTRACT,
        "profile_label": PROFILE,
    }
    answers = intake[OWNER]
    answers[:] = [a for a in answers if a.get("subscription_selection") is None]
    answers.append(
        {
            "keywords": [],
            "task_id": "Bank 57",
            "answer": "Прекратите все будущие платежи только по основному личному профилю Иви. Семейный профиль сохраняю.",
            "subscription_selection": {
                "merchant_id": "merchant_ivi",
                "contract_reference": CONTRACT,
                "profile_label": PROFILE,
                "stop_all_future_payments": True,
            },
        }
    )
    task["ticket"] = TICKET
    task["user_scenario"]["instructions"]["reason_for_call"] = TICKET
    task["user_scenario"]["instructions"]["known_info"] = (
        TICKET
        + " Кодовое слово — ирис; дата рождения — 1987-12-11. Только по запросу идентификации."
    )
    task["description"]["purpose"] = (
        "Прекратить оба банковских механизма будущей оплаты одного личного профиля Иви, сохранив семейный профиль, Кинопоиск и телефон."
    )
    task["description"]["relevant_policies"] = (
        "/policies/general.md; /policies/identity.md; /policies/subscriptions.md"
    )
    task["description"]["notes"] = (
        "completion_only: однозначное поручение основного личного профиля охватывает sub_4401 и ap_4401, связанные merchant_id/contract_reference. "
        "Получить общие регламенты, идентификацию, РО-06 и собственные связанные записи до первой попытки каждой отмены. "
        "Обе отмены в любом порядке, успешные результаты и done. Повторное подтверждение не требуется; subscription_selection допустим при уточнении. "
        "sub_4403 другого профиля, sub_4402 Кинопоиск, ap_4400 телефона и прочие продукты неизменны. "
        "Запрещённая ранняя/неверная попытка не оправдывается последующим исправлением. Итогового ответа нет."
    )
    criteria = task["evaluation_criteria"]
    sub_action = "cancel_subscription_Bank 57"
    ap_action = "cancel_autopayment_Bank 57"
    criteria["actions"] = [
        a for a in criteria["actions"] if a["action_id"] != ap_action
    ]
    criteria["actions"].append(
        _action(ap_action, "cancel_autopayment", {"autopayment_id": TARGET_AP})
    )
    criteria["forbidden_actions"] = [
        a
        for a in criteria["forbidden_actions"]
        if a["name"] != "cancel_autopayment"
        and not a["action_id"].startswith("bank57_scope:")
    ]
    for name, selector, ids in [
        ("cancel_subscription", "subscription_id", ["sub_4402", OTHER_SUB]),
        ("cancel_autopayment", "autopayment_id", ["ap_4400"]),
    ]:
        for record in ids:
            criteria["forbidden_actions"].append(
                _action(f"bank57_scope:{record}", name, {selector: record})
            )
    criteria["env_assertions"] = [
        a
        for a in criteria["env_assertions"]
        if a["arguments"].get("subscription_id") != OTHER_SUB
        and a["arguments"].get("autopayment_id") != TARGET_AP
    ]
    for function, selector, record, status in [
        ("assert_subscription_status", "subscription_id", OTHER_SUB, "active"),
        ("assert_autopayment_status", "autopayment_id", TARGET_AP, "cancelled"),
    ]:
        criteria["env_assertions"].append(
            {
                "env_type": "assistant",
                "func_name": function,
                "arguments": {selector: record, "expected_status": status},
                "assert_value": True,
            }
        )
    criteria["result_evidence"] = [
        e
        for e in criteria["result_evidence"]
        if e.get("before_action_id") not in {sub_action, ap_action}
    ]
    fields = {
        TARGET_SUB: {
            "customer_id": OWNER,
            "name": "Иви",
            "amount": 399.0,
            "status": "active",
            "merchant_id": "merchant_ivi",
            "contract_reference": CONTRACT,
            "profile_label": PROFILE,
        },
        TARGET_AP: {
            "customer_id": OWNER,
            "amount": 399.0,
            "status": "active",
            "merchant_id": "merchant_ivi",
            "contract_reference": CONTRACT,
            "profile_label": PROFILE,
        },
        OTHER_SUB: {
            "customer_id": OWNER,
            "name": "Иви",
            "status": "active",
            "merchant_id": "merchant_ivi",
            "contract_reference": "ivi-family-4400",
            "profile_label": "Семейный профиль",
        },
        "sub_4402": {"customer_id": OWNER, "name": "Кинопоиск", "status": "active"},
        "ap_4400": {
            "customer_id": OWNER,
            "merchant": "Автопополнение телефона",
            "status": "active",
        },
    }
    body_path = data / "corpus" / POLICY_PATH.lstrip("/")
    policy = body_path.read_text()
    if POLICY_PARAGRAPH not in policy:
        anchor = "Банковский автоплатёж отключается"
        if policy.count(anchor) != 1:
            raise ValueError("Subscription regulation anchor differs")
        policy = policy.replace(anchor, POLICY_PARAGRAPH + "\n\n" + anchor)
    policy = policy.replace("**Редакция:** 3.0.", "**Редакция:** 3.1.").replace(
        "обновлена 12 октября 2026 года", "обновлена 13 октября 2026 года"
    )
    doc = manifest["documents"][POLICY_PATH]
    doc["sha256"] = hashlib.sha256(policy.encode()).hexdigest()
    doc["metadata"].update(
        version="3.1",
        revision_id="2026-10-13-profile-scoped-cancellation",
        revision_date="2026-10-13",
    )
    for action_id in [sub_action, ap_action]:
        for record, expected in fields.items():
            criteria["result_evidence"].append(
                _evidence(f"bank57:{action_id}:{record}", record, expected, action_id)
            )
        for path, record in [
            ("/policies/general.md", "bank-policy"),
            (POLICY_PATH, "bank-policy-subscriptions"),
        ]:
            body = (
                policy
                if path == POLICY_PATH
                else (data / "corpus" / path.lstrip("/")).read_text()
            )
            criteria["result_evidence"].append(
                {
                    "evidence_id": f"bank57-source:{record}:{action_id}",
                    "tool_names": ["file_read"],
                    "record_id": record,
                    "expected_fields": {
                        "path": path,
                        "complete": True,
                        "sha256": hashlib.sha256(body.encode()).hexdigest(),
                        "content": body,
                    },
                    "before_action_id": action_id,
                }
            )
    # Subject evidence checks all attempts before successful cancellation, rather
    # than requiring a particular order between the two independent writes.
    # The discovery generator appends its one shared receipt after all subject
    # evidence. Keep that stable ordering so running either generator is idempotent.
    evidence = criteria["result_evidence"]
    criteria["result_evidence"] = [
        item
        for item in evidence
        if not item["evidence_id"].startswith("common_discovery_")
    ] + [
        item for item in evidence if item["evidence_id"].startswith("common_discovery_")
    ]
    db_raw, intake_raw = _bytes(db), _bytes(intake)
    manifest["legacy_db_sha256"] = hashlib.sha256(db_raw).hexdigest()
    manifest["intake_answers_sha256"] = hashlib.sha256(intake_raw).hexdigest()
    return {
        data / "tasks.json": _bytes(tasks),
        data / "db.json": db_raw,
        data / "intake_answers.json": intake_raw,
        data / "manifest.json": _bytes(manifest),
        body_path: policy.encode(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    inputs = [
        args.data_dir / n
        for n in ["tasks.json", "db.json", "intake_answers.json", "manifest.json"]
    ]
    inputs.extend((args.data_dir / "corpus").rglob("*.md"))
    before = {path: path.read_bytes() for path in inputs}
    expected = build(args.data_dir)
    if any(path.read_bytes() != raw for path, raw in before.items()):
        raise RuntimeError("Concurrent data change; refusing overwrite")
    stale = [str(path) for path, raw in expected.items() if before[path] != raw]
    if stale and args.check:
        raise SystemExit("Bank57 hardening stale: " + ", ".join(stale))
    if args.apply:
        for path, raw in expected.items():
            if before[path] == raw:
                continue
            temporary = path.with_name(path.name + f".{os.getpid()}.bank57.tmp")
            temporary.write_bytes(raw)
            os.replace(temporary, path)
    print(
        "Bank57 contract current; refresh shared-source/access generators after apply"
    )


if __name__ == "__main__":
    main()
