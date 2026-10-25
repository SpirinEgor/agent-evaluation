"""Id-scoped Bank2 migration. Shared tools and source receipts integrated by root."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

CUSTOMER = "gromov_a_1187"
TARGET = "txn_552290"
CREATE = "cashback_investigation_Bank 2"
ITEMS = [{"transaction_id": TARGET, "amount": 30.0}]
TICKET = "Сегодня 28 августа 2026 года. Здравствуйте! Я Андрей Громов, телефон +7 926 118-77-30. В июле дважды оплатил в «Кофемании» по 1 200 ₽. По одной из покупок кешбэк пришёл не полностью, срок августовской выплаты уже прошёл. Не помню сейчас дату, но чек могу уточнить. Проверьте именно нужную покупку и зарегистрируйте обращение на проверку недоначисления, если оно подтвердится. В приложении сейчас другая ставка категории. Самостоятельно выплачивать деньги или менять тариф не прошу."


def with_contract(tasks, db):
    tasks, db = deepcopy(tasks), deepcopy(db)
    selected = [t for t in tasks if t["id"] == "Bank 2"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 2")
    task = selected[0]
    if task.get("scenario_version") not in {
        None,
        "2026-10-14-composite-v1",
        "2026-10-14-bank2-v2",
    }:
        raise ValueError("Unsupported Bank2 parent version for legacy migration")
    if task.get("scenario_version") == "2026-10-14-bank2-v2":
        return tasks, db
    task["ticket"] = TICKET
    ins = task["user_scenario"]["instructions"]
    ins["reason_for_call"] = TICKET
    ins["known_info"] = (
        TICKET
        + " Кодовое слово — меркурий; дата рождения — 1999-11-02. Называйте только по запросу идентификации. Нужная покупка — 25 июля, чек CF-2507-1187. Дату или чек сообщайте только при уточнении покупки."
    )
    task["description"]["purpose"] = (
        "После срока выплаты выбрать покупку по уточнению клиента, рассчитать недоначисление и зарегистрировать одно обращение на 30 ₽."
    )
    task["description"]["relevant_policies"] = (
        "/policies/general.md, /policies/identity.md, /policies/fees_rewards.md; /knowledge/kb_160.md, /knowledge/kb_cashback_history.md, /knowledge/kb_reward_reconciliation.md"
    )
    task["description"]["notes"] = (
        "completion_only. Июльская ставка5%, posted refund200, processing600 не вычитается, чужой возврат900 не вычитается. Исходный cap35 не уменьшается повторно на paid5. min(1000*.05,35)-5=30. Один CRM кейс standard_cashback по txn_552290, без денежного начисления. Не фиксировать порядок независимых чтений; details и расчет без calculate допустимы."
    )
    tx = db["transactions"]
    tx[TARGET]["date"] = "2026-07-25"
    tx["txn_552291"] = deepcopy(tx[TARGET])
    tx["txn_552291"].update(id="txn_552291", date="2026-07-26")
    for ident, amount, status, kind, original, date in [
        ("txn_552292", 200.0, "posted", "refund", TARGET, "2026-07-27"),
        ("txn_552293", 600.0, "processing", "refund", TARGET, "2026-08-27"),
        ("txn_552294", 900.0, "posted", "refund", "txn_552291", "2026-07-28"),
        ("txn_552296", 5.0, "posted", "cashback", TARGET, "2026-08-05"),
    ]:
        tx[ident] = deepcopy(tx[TARGET])
        tx[ident].update(
            id=ident,
            amount=amount,
            status=status,
            kind=kind,
            date=date,
            original_transaction_id=original,
            merchant="Возврат покупки" if kind == "refund" else "Выплата кешбэка",
        )
    rules = db["cashback_rules"][CUSTOMER]
    july = deepcopy(rules["categories"])
    july[0]["rate"] = 0.05
    rules["categories"][0]["rate"] = 0.01
    rules.update(
        categories_history=[
            {
                "effective_from": "2026-07-01",
                "effective_to": "2026-07-31",
                "categories": july,
            },
            {
                "effective_from": "2026-08-01",
                "effective_to": None,
                "categories": deepcopy(rules["categories"]),
            },
        ],
        monthly_cap=1000.0,
        cap_remaining_before_transaction={TARGET: 35.0, "txn_552291": 0.0},
    )
    answers = db["client_answers"][CUSTOMER]
    answers[:] = [a for a in answers if not a.get("transaction_selection")]
    answers.append(
        {
            "keywords": ["дата покупки", "чек", "какая покупка"],
            "answer": "Покупка 25 июля 2026 года, чек CF-2507-1187.",
            "transaction_selection": {
                "id": TARGET,
                "date": "2026-07-25",
                "receipt": "CF-2507-1187",
                "merchant": "Кофемания",
                "amount": 1200.0,
            },
        }
    )
    e = task["evaluation_criteria"]
    e["actions"] = [
        a
        for a in e["actions"]
        if a["name"]
        not in {
            "calculate",
            "get_transactions",
            "get_transaction_details",
            "get_cashback_rules",
            "create_cashback_investigation",
            "get_bank_date",
        }
        and a["action_id"] != "select_purchase_Bank 2"
        and a["action_id"] not in {"read_history_Bank 2", "read_reconciliation_Bank 2"}
    ]
    e["actions"] += [
        {
            "action_id": "read_history_Bank 2",
            "name": "file_read",
            "arguments": {"path": "/knowledge/kb_cashback_history.md"},
            "compare_args": ["path"],
        },
        {
            "action_id": "read_reconciliation_Bank 2",
            "name": "file_read",
            "arguments": {"path": "/knowledge/kb_reward_reconciliation.md"},
            "compare_args": ["path"],
        },
        {
            "action_id": "date_Bank 2",
            "name": "get_bank_date",
            "arguments": {},
            "required_for_reward": False,
        },
        {
            "action_id": "select_purchase_Bank 2",
            "name": "ask_client",
            "arguments": {
                "customer_id": CUSTOMER,
                "request": {
                    "type": "transaction_selection",
                    "merchant": "Кофемания",
                    "amount": 1200.0,
                },
            },
            "compare_args": ["customer_id"],
            "argument_values": {"request.type": "transaction_selection"},
        },
        {
            "action_id": "get_transactions_Bank 2",
            "name": "get_transactions",
            "arguments": {
                "customer_id": CUSTOMER,
                "date_from": "2026-07-25",
                "date_to": "2026-08-28",
                "limit": 50,
            },
            "compare_args": ["customer_id"],
            "required_for_reward": False,
        },
        {
            "action_id": "get_rules_Bank 2",
            "name": "get_cashback_rules",
            "arguments": {"customer_id": CUSTOMER},
            "compare_args": ["customer_id"],
            "required_for_reward": False,
        },
        {
            "action_id": CREATE,
            "name": "create_cashback_investigation",
            "arguments": {
                "customer_id": CUSTOMER,
                "program": "standard_cashback",
                "amount": 30.0,
                "items": deepcopy(ITEMS),
            },
            "compare_args": ["customer_id", "program", "amount", "items"],
        },
    ]
    e["env_assertions"] = [
        a
        for a in e["env_assertions"]
        if a["func_name"]
        not in {
            "assert_cashback_investigation",
            "assert_cashback_investigation_attempts",
            "assert_no_duplicate_cashback_investigation_attempts",
        }
    ]
    e["env_assertions"] += [
        {
            "env_type": "assistant",
            "func_name": "assert_cashback_investigation",
            "arguments": {
                "customer_id": CUSTOMER,
                "program": "standard_cashback",
                "amount": 30.0,
                "items": deepcopy(ITEMS),
            },
            "assert_value": True,
        },
        {
            "env_type": "assistant",
            "func_name": "assert_cashback_investigation_attempts",
            "arguments": {
                "customer_id": CUSTOMER,
                "program": "standard_cashback",
                "amount": 30.0,
                "items": deepcopy(ITEMS),
            },
            "assert_value": True,
        },
        {
            "env_type": "assistant",
            "func_name": "assert_no_duplicate_cashback_investigation_attempts",
            "arguments": {"customer_id": CUSTOMER},
            "assert_value": True,
        },
    ]
    e["action_order"] = [
        o
        for o in e["action_order"]
        if o["after_action_id"] not in {CREATE, "select_purchase_Bank 2"}
    ]
    for before, after in [
        ("verify_identity_1", "select_purchase_Bank 2"),
        ("select_purchase_Bank 2", CREATE),
    ]:
        e["action_order"].append(
            {
                "before_action_id": before,
                "after_action_id": after,
                "require_result_before_target": True,
            }
        )
    evidence = e["result_evidence"]
    evidence[:] = [r for r in evidence if not r["evidence_id"].startswith("bank2:")]
    for r in evidence:
        if r["record_id"] == TARGET:
            r["expected_fields"].update(date=tx[TARGET]["date"])
        if r["evidence_id"] == "personal_cashback_rules_Bank 2":
            r["expected_fields"] = deepcopy(rules)
        if r.get("before_action_id") not in {
            "verify_identity_1",
            "ask_identity_Bank 2",
        }:
            r["before_action_id"] = CREATE
    for ident in ["txn_552292", "txn_552293", "txn_552296"]:
        evidence.append(
            {
                "evidence_id": "bank2:transaction:" + ident,
                "tool_names": ["get_transactions", "get_transaction_details"],
                "record_id": ident,
                "expected_fields": {
                    k: tx[ident][k]
                    for k in ["customer_id", "date", "amount", "status", "kind"]
                    + (["original_transaction_id"] if ident != "txn_552291" else [])
                },
                "after_action_id": "verify_identity_1",
                "before_action_id": CREATE,
            }
        )
    evidence.append(
        {
            "evidence_id": "bank2:client-selection",
            "tool_names": ["ask_client"],
            "record_id": TARGET,
            "expected_fields": {"date": "2026-07-25", "receipt": "CF-2507-1187"},
            "before_action_id": CREATE,
        }
    )
    e["communicate_info"] = []
    e["nl_assertions"] = []
    # RO-02 must precede identification; the RO-01 framework precedes business execution.
    for evidence in task["evaluation_criteria"]["result_evidence"]:
        if evidence.get("record_id") == "bank-policy" and evidence.get(
            "before_action_id", ""
        ).startswith("verify"):
            evidence["before_action_id"] = "cashback_investigation_Bank 2"
    return tasks, db


def with_intake(intake):
    """Update only the current customer's fixed clarification answer."""
    intake = deepcopy(intake)
    rows = intake[CUSTOMER]
    rows[:] = [row for row in rows if not row.get("transaction_selection")]
    rows.append(
        {
            "keywords": [],
            "answer": "Покупка 25 июля 2026 года, чек CF-2507-1187.",
            "transaction_selection": {
                "id": TARGET,
                "date": "2026-07-25",
                "receipt": "CF-2507-1187",
                "merchant": "Кофемания",
                "amount": 1200.0,
            },
        }
    )
    return intake


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    paths = [args.data_dir / "tasks.json", args.data_dir / "db.json"]
    updated = with_contract(*(json.loads(p.read_text()) for p in paths))
    for path, data in zip(paths, updated):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    intake_path = args.data_dir / "intake_answers.json"
    intake_path.write_text(
        json.dumps(
            with_intake(json.loads(intake_path.read_text())),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
