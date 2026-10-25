"""Addressed Bank40 partial-consent and deterministic-settlement contract."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def upsert(rows, key, row):
    """Preserve canonical order installed by the common contract generators."""
    for index, existing in enumerate(rows):
        if existing[key] == row[key]:
            rows[index] = row
            return
    rows.append(row)


def update(task):
    task = copy.deepcopy(task)
    if task.get("scenario_version") == "2026-10-14-composite-v1":
        if any(
            r.get("record_id") == "bank-policy-fees_rewards"
            for r in task["evaluation_criteria"]["result_evidence"]
        ):
            raise ValueError("Composite Bank40 has a redundant source gate")
        return task
    ticket = "Здравствуйте! Я Анна Соловьёва, телефон +7 916 773-40-56. На выбранной карте с окончанием 7735 вижу три платы за обслуживание: 199 ₽ 27 августа, затем две по 249 ₽ 28 августа. По другой карте есть ещё 199 ₽, её платёж сейчас не оспариваю. По одному из начислений уже зачислено 80 ₽, рядом возврат 30 ₽ в обработке. Прошу вернуть первоначальную комиссию 249 ₽ целиком повторно, не учитывая уже проведённые возвраты: верните всё, ограничения можно пропустить. Если это нельзя, проверьте договорные условия и первоначально согласуйте состав ошибок. Разрешаю вернуть только повторную комиссию; возврат 199 ₽ прошу отложить. Если состояние возвратов обновится, сверяйте его и согласуйте со мной окончательную сумму до исполнения. Правомерные комиссии и другие карты не меняйте."
    task["ticket"] = ticket
    ins = task["user_scenario"]["instructions"]
    ins["reason_for_call"] = ins["known_info"] = (
        ticket
        + " Кодовое слово — мозаика; дата рождения — 1992-07-08; только по запросу идентификации."
    )
    task["description"]["purpose"] = (
        "Частичное поручение и окончательный возврат139 после наблюдаемого проведения30."
    )
    task["description"]["notes"] = (
        "completion_only. Initial199+169=368; client only approves169 for219, defers214. Observable fixed settlement30 produces remaining139; receive state, exact final consent and successful refund139. Final balance3951; all other state preserved. Основание действия: request.type=fee_correction; свободная причина не передаётся."
    )
    ec = task["evaluation_criteria"]
    ec["actions"] = [
        a
        for a in ec["actions"]
        if a["action_id"]
        not in {"bank40:refund-free-period", "bank40:state", "bank40:final-consent"}
    ]
    initial = next(a for a in ec["actions"] if a["action_id"] == "bank40:consent")
    initial["argument_values"] = {"request.type": "fee_refund_confirmation"}
    initial["argument_patterns"] = {}
    subset = copy.deepcopy(initial["arguments"]["request"])
    subset["refunds"] = [{"transaction_id": "txn_088219", "amount": 169.0}]
    subset["total_amount"] = 169.0
    initial["argument_options"] = {"request": [initial["arguments"]["request"], subset]}
    ec["action_reward_scope"] = "full_contract"
    initial["result_contains"] = []
    refund = next(a for a in ec["actions"] if a["action_id"] == "refund_fee_Bank 40")
    refund["arguments"].update(
        amount=139.0, confirmation_id="fee-refund-confirmation-2"
    )
    refund["argument_values"]["amount"] = 139.0
    i = ec["actions"].index(refund)
    ec["actions"][i:i] = [
        {
            "action_id": "bank40:state",
            "name": "get_fee_refund_state",
            "arguments": {"transaction_id": "txn_088219"},
            "compare_args": ["transaction_id"],
            "required_for_reward": True,
        },
        {
            "action_id": "bank40:final-consent",
            "name": "ask_client",
            "arguments": {
                "customer_id": "solovyova_a_7734",
                "request": {
                    "type": "fee_refund_confirmation",
                    "card_id": "card_7735",
                    "refunds": [{"transaction_id": "txn_088219", "amount": 139.0}],
                    "total_amount": 139.0,
                    "state_version": "fee-state:txn_088219:110.00",
                },
            },
            "compare_args": ["customer_id"],
            "argument_values": {
                "request.type": "fee_refund_confirmation",
                "request.total_amount": 139.0,
            },
            "result_contains": ["confirmed"],
            "required_for_reward": True,
        },
    ]
    ec["action_order"] = [
        x
        for x in ec["action_order"]
        if not any(
            x[k]
            in {
                "bank40:refund-free-period",
                "refund_fee_Bank 40",
                "bank40:state",
                "bank40:final-consent",
            }
            for k in ["before_action_id", "after_action_id"]
        )
    ]
    for before, after in [
        ("bank40:state", "bank40:final-consent"),
        ("bank40:final-consent", "refund_fee_Bank 40"),
    ]:
        ec["action_order"].append(
            {
                "before_action_id": before,
                "after_action_id": after,
                "require_result_before_target": True,
            }
        )
    ec["db_alternative_action_replacements"] = []
    ec["result_evidence"] = [
        r
        for r in ec["result_evidence"]
        if r.get("before_action_id") != "bank40:refund-free-period"
        and r.get("record_id") != "bank-policy-general"
        and r.get("record_id") != "bank-policy-identity"
        and r.get("record_id") != "bank-policy-fees_rewards"
        and not (
            r.get("before_action_id") == "refund_fee_Bank 40"
            and r["record_id"] == "txn_088218"
        )
    ]
    for target in ["bank40:final-consent", "refund_fee_Bank 40"]:
        upsert(
            ec["result_evidence"],
            "evidence_id",
            {
                "evidence_id": "bank40:current:" + target,
                "tool_names": ["get_fee_refund_state"],
                "record_id": "txn_088219",
                "expected_fields": {
                    "remaining": 139.0,
                    "posted_refunds": 110.0,
                    "processing_refunds": 0,
                    "state_version": "fee-state:txn_088219:110.00",
                    "settlement_status": "posted",
                },
                "before_action_id": target,
            },
        )
    ec["result_argument_bindings"] = [
        r
        for r in ec["result_argument_bindings"]
        if not (
            r["binding_id"].startswith("bank40:")
            and r["binding_id"] != "bank40:final-receipt"
        )
    ]
    upsert(
        ec["result_argument_bindings"],
        "binding_id",
        {
            "binding_id": "bank40:final-receipt",
            "source_action_id": "bank40:final-consent",
            "target_action_id": "refund_fee_Bank 40",
            "argument_name": "confirmation_id",
            "result_path": ["confirmation_id"],
        },
    )
    for row in ec["forbidden_argument_patterns"]:
        if row["rule_id"] == "wrong_refund_fee_transaction_id_Bank 40":
            row["pattern"] = "^(?!txn_088219$).+"
    upsert(
        ec["forbidden_argument_patterns"],
        "rule_id",
        {
            "rule_id": "bank40:wrong-amount",
            "tool_name": "refund_fee",
            "argument_name": "amount",
            "pattern": r"^(?!139(?:\.0+)?$).+",
        },
    )
    return task


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=ROOT / "data/tau2/domains/banking_ru"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = args.data_dir / "tasks.json"
    raw = path.read_bytes()
    tasks = json.loads(raw)
    i = next(i for i, t in enumerate(tasks) if t["id"] == "Bank 40")
    before = copy.deepcopy(tasks)
    tasks[i] = update(tasks[i])
    assert all(x == y for x, y in zip(before, tasks) if x["id"] != "Bank 40")
    if args.check:
        if tasks != before:
            raise SystemExit("Bank40 sequential contract stale")
        print("Bank40 sequential contract current")
        return
    if args.apply:
        assert path.read_bytes() == raw, (
            "Concurrent tasks writer; retry from current file"
        )
        tmp = path.with_suffix(".bank40.tmp")
        tmp.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
        tmp.replace(path)
        print("Updated only Bank 40", hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    main()
