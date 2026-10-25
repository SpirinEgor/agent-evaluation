"""Bank2 v3: temporary migration, exact fixed gold and independent money control."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from upgrade_bank2_v2 import (
    CASE,
    PERIOD,
    A,
    B,
    C,
    action,
    assertion,
    evidence,
    independent_oracle,
)

VERSION = "2026-10-14-bank2-settlement-v3"
LEDGER = "cashback-settlement:july-additional"
PARAGRAPH = "В приложении ещё написано, что дополнительное начисление кешбэка за июль находится в обработке. Сумму там не показывают. Проверьте, пожалуйста, что уже поступило, чтобы не выплатить одно и то же дважды. Если начисление завершится во время обращения, учтите его. Я согласен на корректировку оставшейся подтверждённой недоплаты в пределах полномочий банка и дополнение уже открытого дела остальным остатком."
RULES = """

## Поздние начисления, актуальность и неизвестное исполнение

Незавершённое начисление не считается выплаченным. Перед корректировкой
недоплаты за период с ожидающей обработкой необходимо получить результат этой
обработки. refresh_cashback_settlement(customer_id, period) синхронизирует её
статус; повтор не создаёт новой выплаты. Статус и принадлежность обработки
доступны в cycles.settlements через обычное чтение get_cashback_rules; после
завершения доступны ссылки на проведённые записи и их обычные банковские детали.
Проведённое дополнительное начисление уменьшает недоплату соответствующей
покупки и не расходует лимит повторно, если погашает ранее рассчитанное обязательство.

При изменении влияющих на расчёт данных необходимо пересмотреть зависимые
суммы. Изменение служебных сведений, не влияющих на право, сумму или исполнение,
само по себе не требует отказа от действия. Версия state_revision доступна
через правила периода, полномочия, дела и результаты операций. Передавайте
полученную версию как expected_revision при корректировке и дополнении дела.
Версия относится только к своему клиенту и периоду. Успешное изменение выплаты
или дела продвигает её; используйте версию из результата либо нового чтения.
settlement_pending и state_changed безопасно отклоняют запись, сохраняя
возможность обновить влияющие данные и повторить. Они не отменяют независимые
ограничения владельца, идентификации, полномочия и двойной компенсации.

Статус execution_unknown не подтверждает отсутствие операции. До нового
независимого исполнения необходимо установить результат предыдущего запроса.
Допускается get_cashback_adjustment_status(operation_id) либо безопасный повтор
с тем же ключом идемпотентности и теми же параметрами. Ключ связывает покупку,
полномочие и сумму, но не служебную версию. Поиск прежней операции выполняется
до проверки исчерпания полномочия. Смена ключа не восстанавливает полномочие.
Подтверждение содержит succeeded, фактическую сумму, покупку, полномочие,
ledger_transaction_id и актуальную state_revision. Допустимы также полученные
обычным банковским чтением полные факты проведённой ledger-записи.

Итог расследования исключает уже погашенную недоплату и корректно зарезервированную
часть. Неизвестность ответа инструмента сама по себе не даёт основания считать
выплату отсутствующей. При выплате резерв уменьшается на её сумму и удаляется
после полного исполнения. При разделённых выплатах подтверждается каждая.
Перед done должны быть получены результаты всех порученных действий и
установлено исполнение каждого запроса с неизвестным результатом.
"""


def migrate(data):
    paths = {
        name: data / name for name in ["db.json", "tasks.json", "intake_answers.json"]
    }
    db, tasks, answers = [json.loads(p.read_text()) for p in paths.values()]
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if task.get("scenario_version") == VERSION:
        return db, tasks, answers
    if task.get("scenario_version") != "2026-10-14-bank2-v2":
        raise ValueError("Unsupported Bank2 parent version for settlement-v3 migration")
    assert independent_oracle(db)["claim"] == 38
    credit = deepcopy(db["transactions"]["txn_552298"])
    credit.update(
        id=LEDGER,
        amount=7,
        original_transaction_id=B,
        date=db["today"],
        posted_at=None,
        merchant="Дополнительное банковское начисление",
        status="posted",
    )
    db.setdefault("cashback_settlements", {})["july-additional-processing"] = {
        "id": "july-additional-processing",
        "customer_id": C,
        "period": PERIOD,
        "status": "pending",
        "credits": [credit],
        "ledger_ids": [],
    }
    db.setdefault("cashback_state_revisions", {})[C + ":" + PERIOD] = 1
    db.setdefault("cashback_execution_profiles", {})[C] = {
        "first_adjustment_unknown": True
    }
    db.setdefault("cashback_adjustment_operations", {})
    task["scenario_version"] = VERSION
    task["ticket"] += "\n" + PARAGRAPH
    task["user_scenario"]["instructions"].update(
        reason_for_call=task["ticket"], known_info=task["ticket"]
    )
    task["description"].update(
        notes=VERSION + "; completion_only; local unpublished.",
        purpose="Пересмотреть недоплату после обработки начисления и установить фактически исполненную корректировку.",
    )
    e = task["evaluation_criteria"]
    actions = e["actions"]
    pay = next(a for a in actions if a["name"] == "execute_cashback_adjustment")
    amend = next(a for a in actions if a["name"] == "amend_cashback_investigation")
    pay["arguments"]["expected_revision"] = 2
    amend["arguments"].update(
        expected_revision=3,
        items=[
            {"transaction_id": A, "amount": 18},
            {"transaction_id": B, "amount": 13},
        ],
    )
    idx = actions.index(pay)
    actions[idx:idx] = [
        action(
            "bank2:v3:refresh",
            "refresh_cashback_settlement",
            {"customer_id": C, "period": PERIOD},
        ),
        action(
            "bank2:v3:late-details",
            "get_transaction_details",
            {"transaction_id": LEDGER},
        ),
    ]
    actions.insert(
        actions.index(pay) + 1,
        action(
            "bank2:v3:status",
            "get_cashback_adjustment_status",
            {"operation_id": "adjustment-operation:cashback-adjustment:adjustment-A"},
        ),
    )
    e["action_order"] = [
        o for o in e["action_order"] if o["before_action_id"] != pay["action_id"]
    ]
    e["cashback_state_policy"] = {"customer_id": C, "period": PERIOD}
    for receipt in e["result_evidence"]:
        if receipt["evidence_id"].startswith("bank2:v2:registry:"):
            receipt["expected_fields"].pop("related_posted_ids", None)
    e["result_evidence"].append(
        evidence(
            "bank2:v3:late:amend",
            ["get_transactions", "get_transaction_details"],
            LEDGER,
            {
                "customer_id": C,
                "original_transaction_id": B,
                "kind": "cashback",
                "status": "posted",
                "amount": 7,
            },
            amend["action_id"],
            target_item_ids=[B],
            only_if_target_attempted=True,
            recoverable_result_statuses=["settlement_pending", "state_changed"],
        )
    )
    for a in e["env_assertions"]:
        if a["func_name"] == "assert_cashback_investigation":
            a["arguments"].update(
                amount=31, items=deepcopy(amend["arguments"]["items"])
            )
    e["env_assertions"].append(
        assertion(
            "assert_cashback_reservations_closed", {"customer_id": C, "period": PERIOD}
        )
    )
    source = data / "corpus/knowledge/kb_standard_cashback_v2.md"
    equivalent = data / "corpus/knowledge/kb_cashback_adjustment_authority.md"
    for p in [source, data / "corpus/policies/fees_rewards.md", equivalent]:
        text = p.read_text()
        if RULES.strip() not in text:
            text = text.rstrip() + RULES
        if p in [source, equivalent]:
            text = text.replace("2026-10-14-bank2-v2", "2026-10-14-cashback-state-v3")
        p.write_text(text)
    # Existing equivalent contains the full posting-v2 source verbatim; maintain it.
    # Appended common rules are already in both, avoiding nested duplicate copies.
    for receipt in e["result_evidence"]:
        if "file_read" in receipt["tool_names"]:
            expected = receipt["expected_fields"]
            if receipt.get("record_id") == "kb_standard_cashback_v2":
                expected["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    for name, value in zip(paths, [db, tasks, answers]):
        paths[name].write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return db, tasks, answers


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, required=True)
    p.add_argument("--check", action="store_true")
    args = p.parse_args()
    if args.check:
        db = json.loads((args.data_dir / "db.json").read_text())
        task = next(
            t
            for t in json.loads((args.data_dir / "tasks.json").read_text())
            if t["id"] == "Bank 2"
        )
        assert task["scenario_version"] == VERSION
        assert PARAGRAPH in task["ticket"]
        assert independent_oracle(db)["claim"] == 38
        assert db["cases"][CASE]["amount"] == 10
        assert LEDGER not in db["transactions"]
        batch = db["cashback_settlements"]["july-additional-processing"]
        assert batch["customer_id"] == C and batch["period"] == PERIOD
        assert batch["status"] == "pending" and batch["ledger_ids"] == []
        assert len(batch["credits"]) == 1
        credit = batch["credits"][0]
        assert credit["id"] == LEDGER and credit["amount"] == 7
        assert credit["original_transaction_id"] == B and credit["kind"] == "cashback"
        settled = deepcopy(db)
        settled["transactions"][LEDGER] = credit
        assert independent_oracle(settled)["claim"] == 31
        assert db["cashback_execution_profiles"][C]["first_adjustment_unknown"] is True
        assert db["cashback_state_revisions"][C + ":" + PERIOD] == 1
        e = task["evaluation_criteria"]
        pay = next(
            a for a in e["actions"] if a["name"] == "execute_cashback_adjustment"
        )
        claim = next(
            a for a in e["actions"] if a["name"] == "amend_cashback_investigation"
        )
        assert (
            pay["arguments"]["amount"] == 12
            and pay["arguments"]["expected_revision"] == 2
        )
        assert claim["arguments"]["items"] == [
            {"transaction_id": A, "amount": 18},
            {"transaction_id": B, "amount": 13},
        ]
        assert claim["arguments"]["expected_revision"] == 3
        assert e["cashback_state_policy"] == {"customer_id": C, "period": PERIOD}
        source = args.data_dir / "corpus/knowledge/kb_standard_cashback_v2.md"
        for receipt in e["result_evidence"]:
            if receipt["record_id"] == "kb_standard_cashback_v2":
                assert (
                    receipt["expected_fields"]["sha256"]
                    == hashlib.sha256(source.read_bytes()).hexdigest()
                )
        for name in [
            "knowledge/kb_standard_cashback_v2.md",
            "knowledge/kb_cashback_adjustment_authority.md",
            "policies/fees_rewards.md",
        ]:
            assert RULES.strip() in (args.data_dir / "corpus" / name).read_text()
        print("Bank2 settlement v3 current")
        return
    assert (
        args.data_dir.resolve()
        != (
            Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
        ).resolve()
    ), "temporary copy required"
    migrate(args.data_dir)


if __name__ == "__main__":
    main()
