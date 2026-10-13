"""Harden Bank60 with independent pending scopes and tariff revision dates.

Only Bank60 criteria and the declared scenario records change. Common tool and
source changes are maintained separately; refresh shared contract generators
after applying. First validate on a temporary --data-dir. No models are run.
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
OWNER = "fomin_r_9200"
TARGET = "card_9200b"
OTHER = "card_9200a"
ACTION = "set_limit_Bank 60"
CURRENT = {
    "revision_id": "premium-2026-08-28",
    "effective_from": "2026-08-28",
    "effective_to": "2026-08-31",
    "max_sbp_limit": 500000.0,
}
FUTURE = {
    "revision_id": "premium-2026-09-01",
    "effective_from": "2026-09-01",
    "effective_to": None,
    "max_sbp_limit": 300000.0,
}


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def build(data):
    """Build a scoped, deterministic migration without writing input files."""
    tasks = json.loads((data / "tasks.json").read_text())
    selected = [t for t in tasks if t["id"] == "Bank 60"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 60")
    task = selected[0]
    db = json.loads((data / "db.json").read_text())
    manifest = json.loads((data / "manifest.json").read_text())
    if db["customers"][OWNER]["tariff_id"] != "tariff_premium":
        raise ValueError("Bank60 tariff differs from the approved scenario")
    for card in [TARGET, OTHER]:
        if db["cards"][card]["customer_id"] != OWNER:
            raise ValueError("Bank60 declared card owner differs")
    db["tariffs"]["tariff_premium"].update(CURRENT)
    db["tariffs"]["tariff_premium"]["scheduled_revisions"] = [FUTURE]
    requests = {}
    for id, object_id, channel, period, status, amount in [
        ("lrq_9200_cash", TARGET, "cash", "daily", "pending", 120000.0),
        ("lrq_9200_other", OTHER, "sbp", "per_operation", "pending", 350000.0),
        ("lrq_9200_declined", TARGET, "sbp", "per_operation", "declined", 450000.0),
    ]:
        requests[id] = {
            "id": id,
            "customer_id": OWNER,
            "requested_limit": amount,
            "status": status,
            "decided_at": "2026-08-27" if status == "declined" else "",
            "public_reason": "Ранее запрошенное повышение отклонено"
            if status == "declined"
            else "",
            "request_type": "spending_limit",
            "object_type": "card",
            "object_id": object_id,
            "channel": channel,
            "period": period,
            "requires_decision": True,
        }
    db["limit_requests"].update(requests)
    task["description"]["notes"] = (
        "completion_only: на дату среды 28.08.2026 повысить только СБП card_9200b "
        "с 150000 до ровно 400000 RUB. Действующая редакция tariff_premium "
        "premium-2026-08-28 (28–31 августа) разрешает 500000; объявленная редакция "
        "с 1 сентября разрешает 300000 и досрочно не применяется. "
        "Получить карту, настройки, полный список своих заявок с их областью и "
        "действующий тариф до первой попытки изменения. Pending cash той же карты "
        "и pending СБП другой своей карты не блокируют целевую настройку; declined "
        "СБП целевой карты не pending. Совпадающая pending с requires_decision=true "
        "блокировала бы повторную попытку. Общие источники, авторизация и ответы "
        "клиента проверяются по полученным результатам. Все попытки set_limit должны "
        "соблюдать выбранную карту, канал и согласованную сумму; неизвестный ID "
        "с ошибкой можно исправить после идентификации. Последующее исправление "
        "не оправдывает раннюю или неверную попытку. Cash/internet и другая карта "
        "сохраняются, перевод не выполняется. Получить успешный результат и done; "
        "итог клиенту не отправляется. Среда одинакова для всех 61 задач."
    )
    criteria = task["evaluation_criteria"]
    for action in criteria["actions"]:
        if action["name"] == "get_limit_request":
            action["result_contains"] = []
        if action["action_id"] == ACTION:
            action["compare_args"] = ["card_id", "limit_type", "amount"]
    for rule in criteria["forbidden_argument_patterns"]:
        if rule["rule_id"] == "wrong_set_limit_card_id_Bank 60":
            known_wrong = sorted(set(db["cards"]) - {TARGET})
            rule["pattern"] = "^(?:" + "|".join(map(re.escape, known_wrong)) + ")$"
    evidence = criteria["result_evidence"]
    for receipt in evidence:
        if receipt["record_id"] == "tariff_premium":
            receipt["expected_fields"].update(CURRENT)
            receipt["expected_fields"]["scheduled_revisions"] = [FUTURE]
    for id, record in requests.items():
        expected = {
            "evidence_id": f"bank60:request:{id}",
            "tool_names": ["get_limit_request"],
            "record_id": id,
            "expected_fields": record,
            "before_action_id": ACTION,
            "after_action_id": "verify_Bank 60",
        }
        existing = next(
            (e for e in evidence if e["evidence_id"] == expected["evidence_id"]), None
        )
        if existing is None:
            evidence.append(expected)
        else:
            existing.clear()
            existing.update(expected)
    assertions = criteria["env_assertions"]
    assertions[:] = [
        a
        for a in assertions
        if a["func_name"] != "assert_no_pending_limit_change_attempts"
    ]
    assertions.append(
        {
            "env_type": "assistant",
            "func_name": "assert_no_pending_limit_change_attempts",
            "arguments": {"card_id": TARGET, "limit_type": "sbp"},
            "assert_value": True,
        }
    )
    raw_db = _bytes(db)
    manifest["legacy_db_sha256"] = hashlib.sha256(raw_db).hexdigest()
    return {
        data / "tasks.json": _bytes(tasks),
        data / "db.json": raw_db,
        data / "manifest.json": _bytes(manifest),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    inputs = [
        args.data_dir / name for name in ["tasks.json", "db.json", "manifest.json"]
    ]
    before = {path: path.read_bytes() for path in inputs}
    expected = build(args.data_dir)
    if any(path.read_bytes() != raw for path, raw in before.items()):
        raise RuntimeError("Concurrent data change; refusing overwrite")
    stale = [str(path) for path, raw in expected.items() if before[path] != raw]
    if stale and args.check:
        raise SystemExit("Bank60 hardening stale: " + ", ".join(stale))
    if args.apply:
        for path, raw in expected.items():
            if before[path] == raw:
                continue
            temporary = path.with_name(path.name + f".{os.getpid()}.bank60.tmp")
            temporary.write_bytes(raw)
            os.replace(temporary, path)
    print(
        "Bank60 contract current; refresh shared-source/access generators after apply"
    )


if __name__ == "__main__":
    main()
