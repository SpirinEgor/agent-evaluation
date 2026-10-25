"""ID-scoped Bank41 migration. Parent integrates shared files sequentially."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

CUSTOMER = "egorova_n_3358"
TARGET = "bank41:investigation"
ITEMS = [
    {"transaction_id": "txn_077031", "amount": 70.0},
    {"transaction_id": "txn_077040", "amount": 108.0},
]


def with_db(db):
    db = deepcopy(db)
    template = deepcopy(db["transactions"]["txn_077031"])
    additions = {
        "txn_077040": dict(date="2026-08-22", amount=1800.0),
        "txn_077041": dict(date="2026-08-18", amount=1800.0, cashback_granted=True),
        "txn_077042": dict(
            date="2026-08-25",
            amount=400.0,
            kind="refund",
            original_transaction_id="txn_077031",
            merchant="Возврат покупки",
            card_id="card_3358",
        ),
        "txn_077043": dict(
            date="2026-09-02",
            amount=100.0,
            kind="refund",
            status="processing",
            original_transaction_id="txn_077031",
            merchant="Возврат ожидается",
            card_id="card_3358",
        ),
        "txn_077044": dict(
            date="2026-08-26",
            amount=300.0,
            kind="refund",
            original_transaction_id="txn_077039",
            merchant="Возврат другой покупки",
            card_id="card_3358",
        ),
        "txn_077045": dict(
            date="2026-09-01",
            amount=50.0,
            kind="cashback",
            original_transaction_id="txn_077031",
            merchant="Частичная выплата N40",
            card_id="card_3358",
        ),
        "txn_077046": dict(
            date="2026-09-01",
            amount=108.0,
            kind="cashback",
            original_transaction_id="txn_077041",
            merchant="Выплата N40",
            card_id="card_3358",
        ),
    }
    for key, fields in additions.items():
        row = dict(template, id=key, cashback_granted=False)
        row.update(fields)
        if row["kind"] != "purchase":
            row.update(mcc=None, channel="online")
        db["transactions"][key] = row
    # The hint shows that some payout occurred; actual linked amounts determine remainder.
    db["transactions"]["txn_077031"]["cashback_granted"] = True
    db["transactions"]["txn_077021"]["original_transaction_id"] = "txn_077020"
    db["transactions"]["txn_077021"]["card_id"] = "card_3358"
    return db


def source_evidence(data_dir, path, record, evidence_id):
    content = (data_dir / "corpus" / path.lstrip("/")).read_text()
    return dict(
        evidence_id=evidence_id,
        tool_names=["file_read"],
        record_id=record,
        expected_fields=dict(
            path=path,
            sha256=hashlib.sha256(content.encode()).hexdigest(),
            complete=True,
            content=content,
        ),
        before_action_id=TARGET,
    )


def with_tasks(tasks, db, data_dir):
    tasks = deepcopy(tasks)
    selected = [t for t in tasks if t["id"] == "Bank 41"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 41")
    task = selected[0]
    ticket = "Здравствуйте! Я Наталья Егорова, телефон +7 916 335-84-20. Сегодня 2 сентября 2026 года. В августе несколько раз платила в «Шоколаднице», но после плановой выплаты кешбэк по кафе пришёл не полностью. Есть похожие покупки, возвраты и ещё не проведённая операция. Проверьте все мои покупки, которые подходят под августовскую проверку N40, учтите возвраты и уже выплаченный кешбэк. Если недоплата подтвердится, зарегистрируйте одно расследование по всем таким покупкам с общей суммой и разбивкой по операциям. Самостоятельно начислять деньги не прошу; полностью компенсированные покупки повторно включать не нужно."
    task["ticket"] = ticket
    task["user_scenario"]["instructions"]["reason_for_call"] = ticket
    task["user_scenario"]["instructions"]["known_info"] = (
        ticket
        + " Кодовое слово — мускат; дата рождения — 1991-05-27. Только по запросу идентификации."
    )
    task["description"]["purpose"] = (
        "Проверяемое расследование N40 после плановой выплаты по всем недокомпенсированным покупкам."
    )
    task["description"]["relevant_policies"] = (
        "/policies/general.md; /policies/identity.md; /policies/fees_rewards.md; /knowledge/kb_320.md–kb_333.md либо полные сборники; /knowledge/kb_reward_reconciliation.md; /knowledge/processing_calendar.md необязателен"
    )
    task["description"]["notes"] = (
        "completion_only. Дата сценария 2026-09-02, выплата 2026-09-01. Получить авторизацию, полный набор покупок, связанные возвраты/выплаты, правила клиента и Classic, полные источники N40 и общий reconciliation. Создать одно расследование N40 178 ₽ с txn_077031=70 и txn_077040=108. Только posted linked returns/payouts; cap130 отдельно по покупке, затем выплаты. Независимый порядок чтений, детали и полный сборник допустимы; calculate не обязателен. Балансы, тариф и прочая БД сохраняются. До плановой даты маршрут просрочки запрещён. Ручное начисление не разрешено. Никакого ответа клиенту."
    )
    task["initial_state"] = {
        "initialization_data": {"agent_data": {"today": "2026-09-02"}}
    }
    c = task["evaluation_criteria"]
    c["actions"] = [
        a
        for a in c["actions"]
        if a["action_id"] not in [TARGET, "bank41:date", "bank41:reconciliation"]
    ]
    c["actions"].insert(
        0,
        dict(
            action_id="bank41:date",
            name="get_bank_date",
            arguments={},
            compare_args=[],
            required_for_reward=False,
        ),
    )
    for a in c["actions"]:
        if a["name"] == "get_transactions":
            a["arguments"].update(date_to="2026-09-02", limit=50)
    c["actions"].append(
        dict(
            action_id="bank41:reconciliation",
            name="file_read",
            arguments={"path": "/knowledge/kb_reward_reconciliation.md"},
            required_for_reward=False,
        )
    )
    c["actions"].append(
        dict(
            action_id=TARGET,
            name="create_cashback_investigation",
            arguments={
                "customer_id": CUSTOMER,
                "program": "N40",
                "amount": 178.0,
                "items": ITEMS,
            },
            compare_args=["customer_id", "program", "amount"],
            required_for_reward=True,
        )
    )
    c["env_assertions"] = [
        dict(
            env_type="assistant",
            func_name="assert_cashback_investigation",
            arguments={
                "customer_id": CUSTOMER,
                "program": "N40",
                "amount": 178.0,
                "items": ITEMS,
            },
        ),
        dict(
            env_type="assistant",
            func_name="assert_no_duplicate_cashback_investigation_attempts",
            arguments={"customer_id": CUSTOMER},
        ),
        dict(
            env_type="assistant",
            func_name="assert_cashback_investigation_attempts",
            arguments={
                "customer_id": CUSTOMER,
                "program": "N40",
                "amount": 178.0,
                "items": ITEMS,
            },
        ),
        dict(
            env_type="assistant",
            func_name="assert_reward_payout_due",
            arguments={"program": "N40"},
        ),
    ]
    evidence = []
    for e in c["result_evidence"]:
        if e["evidence_id"] == "bank41:clock-source" or e["evidence_id"].startswith(
            "bank41:extra:"
        ):
            continue
        if e.get("record_id") in db["transactions"]:
            txn = db["transactions"][e["record_id"]]
            e["expected_fields"] = {k: txn.get(k) for k in e["expected_fields"]}
        if e.get("before_action_id") is None:
            e["before_action_id"] = TARGET
        if e.get("record_id") == "bank-policy-fees_rewards":
            e.pop(
                "alternatives", None
            )  # Full N40 no longer replaces process authority for CRM.
        evidence.append(e)
    for txn_id in [
        "txn_077040",
        "txn_077041",
        "txn_077042",
        "txn_077043",
        "txn_077044",
        "txn_077045",
        "txn_077046",
        "txn_077039",
    ]:
        row = db["transactions"][txn_id]
        fields = {
            k: row.get(k)
            for k in [
                "customer_id",
                "date",
                "amount",
                "status",
                "kind",
                "original_transaction_id",
                "mcc",
                "channel",
                "country",
                "card_id",
                "account_id",
            ]
        }
        evidence.append(
            dict(
                evidence_id="bank41:extra:" + txn_id,
                tool_names=["get_transactions", "get_transaction_details"],
                record_id=txn_id,
                expected_fields=fields,
                before_action_id=TARGET,
            )
        )
    evidence.append(
        source_evidence(
            data_dir,
            "/knowledge/kb_reward_reconciliation.md",
            "kb_reward_reconciliation",
            "bank41:extra:reconciliation",
        )
    )
    c["result_evidence"] = evidence
    c["action_order"] = [o for o in c["action_order"] if o["after_action_id"] != TARGET]
    for action in [
        "verify_Bank 41",
        "get_cashback_rules_1_Bank 41",
        "get_tariff_2_Bank 41",
    ]:
        c["action_order"].append(
            dict(
                before_action_id=action,
                after_action_id=TARGET,
                require_result_before_target=True,
            )
        )
    # RO-02 must precede identification; the RO-01 framework precedes business execution.
    for evidence in task["evaluation_criteria"]["result_evidence"]:
        if evidence.get("record_id") == "bank-policy" and evidence.get(
            "before_action_id", ""
        ).startswith("verify"):
            evidence["before_action_id"] = "bank41:investigation"
    return tasks


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    db_path = a.data_dir / "db.json"
    tasks_path = a.data_dir / "tasks.json"
    olddb = json.loads(db_path.read_text())
    oldtasks = json.loads(tasks_path.read_text())
    db = with_db(olddb)
    tasks = with_tasks(oldtasks, db, a.data_dir)
    assert all(x == y for x, y in zip(oldtasks, tasks) if x["id"] != "Bank 41")
    if a.apply:
        db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n")
        tasks_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "task": "Bank 41",
                "changed": db != olddb or tasks != oldtasks,
                "other_tasks_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
