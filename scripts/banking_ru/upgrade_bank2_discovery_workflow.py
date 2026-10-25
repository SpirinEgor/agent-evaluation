"""Author-only v25: discover actions, conditional client application, bank outcome."""

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

from build_access_contract import with_contract
from upgrade_bank2_multisource import sync_manifest
from upgrade_composite_rewards import action, assertion, evidence

C = "gromov_a_1187"
CASE = "case_610903"
B = "txn_804227"
VERSION = "2026-10-21-bank2-discoverable-statements-v25"
DOCS = {
    "reward_statement_applicability": (
        "Заявления о разногласиях по вознаграждению",
        "При reward_discrepancy_type=partner_activation_review требуется заявление самого клиента о конкретных покупках своего открытого дела cashback_investigation того же расчётного периода; программа дела standard_cashback или cashback_reconciliation, период по проводке в Europe/Moscow. До запроса получите договор, покупку и дело своего клиента. Для ordinary_recalculation заявление не требуется и запрашивать его вместо банковского пересчёта нельзя. Тип расхождения указан в записи покупки. Заявление не заменяет идентификацию, согласие на выплату или банковские данные. submitted означает только отправку клиентом, registered — регистрацию, accepted — принятие банком. Исполнение выплаты по позиции, требующей заявления, допустимо только после полученного банковского accepted.",
    ),
    "reward_statement_channel": (
        "Канал клиентских заявлений",
        "Клиентское действие submit_reward_statement открывается через give_discoverable_user_tool(name). Клиент исполняет его в детерминированном канале ask_client: request.type=client_action, request.tool_name=submit_reward_statement, request.arguments содержит case_id и transaction_ids. Результат содержит id отправленного заявления, владельца, дело, покупки и status=submitted. Обычный ответ «согласен», свободный вопрос и reward_resolution_confirmation не создают заявления. Не отправляйте заявление за другого владельца или по покупкам, для которых процедура не нужна.",
    ),
    "reward_statement_processing": (
        "События регистрации и принятия заявления",
        "Банковская операция register_reward_statement принимает customer_id, submission_id полученного заявления и case_id; возвращает свой регистрационный id и registered. При update_available=true get_reward_statement_status(customer_id,statement_id) получает объявленное событие принятия и возвращает accepted; повтор возвращает тот же результат. Принятие не проводит деньги. Обработка ранее ожидавшего начисления после принятия синхронизируется через refresh_cashback_settlement. Она меняет остаток и ревизию. Согласие на выплату связывает актуальные суммы, остатки и ревизию; прежнее согласие после изменения не разрешает исполнение.",
    ),
}


def build(parent, destination):
    shutil.copytree(parent, destination)
    tasks = json.loads((destination / "tasks.json").read_text())
    db = json.loads((destination / "db.json").read_text())
    manifest = json.loads((destination / "manifest.json").read_text())
    task = next(x for x in tasks if x["id"] == "Bank 2")
    assert task["scenario_version"] == "2026-10-21-bank2-legacy-knowledge-v24r"
    manifest["discoverable_workflows"] = True
    db["transactions"]["txn_804219"]["reward_discrepancy_type"] = (
        "ordinary_recalculation"
    )
    db["transactions"][B]["reward_discrepancy_type"] = "partner_activation_review"
    for name in ["reward_client_submissions", "reward_statement_registrations"]:
        db[name] = {}
    paths = {d["metadata"]["id"]: p for p, d in manifest["documents"].items()}
    for i, (ident, (title, text)) in enumerate(DOCS.items(), 150):
        assert len(text) <= 800
        p = f"/knowledge/service_notes/doc_{i:04}.md"
        paths[ident] = p
        body = (
            "# "
            + title
            + "\n"
            + text
            + "\nРедакция customer-statements-v1. Применяется с 2026-06-01.\n"
        )
        (destination / "corpus" / p.lstrip("/")).write_text(body)
        manifest["documents"][p] = dict(
            sha256=hashlib.sha256(body.encode()).hexdigest(),
            metadata=dict(
                id=ident,
                title=title,
                revision_id="customer-statements-v1",
                effective_from="2026-06-01",
            ),
        )
    # Local API hints belong to the corresponding procedure, not to startup.
    additions = {
        "reward_resolution_amounts": "Состав и банковские записи меню доступны через get_reward_resolution_offers и get_reward_resolution_components; специальные операции открываются по имени из документации.",
        "reward_resolution_execution": "Техническая операция исполнения — execute_reward_resolution; обновление остатка существующего дела — amend_cashback_investigation. Дополнительное условие отдельных расхождений описано в «Заявления о разногласиях по вознаграждению».",
        "reward_payment_reconciliation": "Техническая синхронизация — refresh_cashback_settlement. Для позиции, требующей клиентского заявления, сперва требуется банковское принятие по правилам «События регистрации и принятия заявления».",
        "kb_case_document_delivery": "Сведения дела доступны через get_case_documents, фактический результат неизвестного исполнения — get_bank_operation_status. Это специальные операции, открываемые по документированному имени.",
    }
    for ident, text in additions.items():
        f = destination / "corpus" / paths[ident].lstrip("/")
        f.write_text(f.read_text() + "\n" + text + "\n")
        manifest["documents"][paths[ident]]["sha256"] = hashlib.sha256(
            f.read_bytes()
        ).hexdigest()
    startup = destination / "corpus/AGENTS.md"
    startup.write_text(
        startup.read_text()
        + "\nСпециальные операции и клиентские действия обнаруживаются в документах. Откройте их схемы инструментами discovery. Для чтения виртуального корпуса также доступен corpus_command; доступа к файловой системе компьютера он не даёт.\n"
    )
    manifest["documents"]["/AGENTS.md"]["sha256"] = hashlib.sha256(
        startup.read_bytes()
    ).hexdigest()
    receipts = {
        d["metadata"]["id"]: dict(
            id=d["metadata"]["id"],
            title=d["metadata"].get("title", d["metadata"]["id"]),
            content=(destination / "corpus" / p.lstrip("/")).read_text(),
        )
        for p, d in manifest["documents"].items()
    }
    for t in tasks:
        for row in t["evaluation_criteria"].get("result_evidence", []):
            for source in [row] + row.get("alternatives", []):
                if "file_read" in source.get("tool_names", []):
                    source["expected_fields"] = receipts[source["record_id"]]
                    if "corpus_command" not in source["tool_names"]:
                        source["tool_names"].append("corpus_command")
                elif (
                    "file_search" in source.get("tool_names", [])
                    and "corpus_command" not in source["tool_names"]
                ):
                    source["tool_names"].append("corpus_command")
    ec = task["evaluation_criteria"]
    sid = (
        "client-statement:"
        + hashlib.sha256(json.dumps([C, CASE, [B]]).encode()).hexdigest()[:16]
    )
    bankid = "bank-statement:" + sid
    new = [
        action(
            "bank2:client:give",
            "give_discoverable_user_tool",
            dict(name="submit_reward_statement"),
        ),
        action(
            "bank2:client:submit",
            "ask_client",
            dict(
                customer_id=C,
                request=dict(
                    type="client_action",
                    tool_name="submit_reward_statement",
                    arguments=dict(case_id=CASE, transaction_ids=[B]),
                ),
            ),
        ),
        action(
            "bank2:client:register",
            "register_reward_statement",
            dict(customer_id=C, submission_id=sid, case_id=CASE),
        ),
        action(
            "bank2:client:accepted",
            "get_reward_statement_status",
            dict(customer_id=C, statement_id=bankid),
        ),
    ]
    reads = [
        action("bank2:client:doc:" + ident, "file_read", dict(path=paths[ident]))
        for ident in DOCS
    ]
    i = next(
        i
        for i, a in enumerate(ec["actions"])
        if a["name"] == "refresh_cashback_settlement"
    )
    ec["actions"][i:i] = reads + new
    ec["env_assertions"].append(
        assertion("assert_reward_statements", dict(customer_id=C))
    )
    received_contract = deepcopy(
        next(
            r
            for r in ec["result_evidence"]
            if r["evidence_id"] == "bank2:v4:contract:bank2:v4:amend"
        )
    )
    received_contract.pop("target_item_ids", None)
    received_contract.pop("recoverable_result_statuses", None)
    received_contract.update(
        evidence_id="bank2:client:received-contract",
        before_action_id="bank2:client:submit",
    )
    ec["result_evidence"].append(received_contract)
    ec["result_evidence"].append(
        evidence(
            "bank2:client:received-category",
            ["get_transactions", "get_transaction_details"],
            B,
            dict(
                customer_id=C,
                account_id="acc_1187",
                card_id="card_804217",
                reward_discrepancy_type="partner_activation_review",
            ),
            "bank2:client:submit",
            only_if_target_attempted=True,
        )
    )
    ec["result_evidence"].append(
        evidence(
            "bank2:client:received-case",
            ["get_cashback_investigations"],
            CASE,
            dict(
                customer_id=C,
                category="cashback_investigation",
                reward_period="2026-07",
                status="open",
                reward_program="cashback_reconciliation",
            ),
            "bank2:client:submit",
            only_if_target_attempted=True,
        )
    )
    for ident in ["reward_statement_applicability"]:
        row = evidence(
            "bank2:client:source:" + ident,
            ["file_read", "corpus_command"],
            ident,
            receipts[ident],
            "bank2:client:submit",
            only_if_target_attempted=True,
        )
        row["alternatives"] = [
            dict(
                tool_names=["file_search", "corpus_command"],
                record_id=ident,
                expected_fields=dict(
                    id=ident,
                    path=paths[ident],
                    line=2,
                    text=DOCS[ident][1],
                    line_truncated=False,
                ),
            )
        ]
        ec["result_evidence"].append(row)
    ec["result_evidence"] += [
        evidence(
            "bank2:client:received-submission",
            ["ask_client"],
            sid,
            dict(
                customer_id=C,
                case_id=CASE,
                transaction_ids=[B],
                status="submitted",
                actor="customer",
            ),
            "bank2:client:register",
            only_if_target_attempted=True,
        ),
        evidence(
            "bank2:client:received-registration",
            ["register_reward_statement"],
            bankid,
            dict(customer_id=C, submission_id=sid, case_id=CASE, status="registered"),
            "bank2:client:accepted",
            only_if_target_attempted=True,
        ),
    ]
    for target in ["bank2:v4:refresh", "bank2:v5:pay"]:
        ec["result_evidence"].append(
            evidence(
                "bank2:client:received-accepted:" + target,
                ["get_reward_statement_status"],
                bankid,
                dict(
                    customer_id=C, case_id=CASE, transaction_ids=[B], status="accepted"
                ),
                target,
                only_if_target_attempted=True,
            )
        )
    ec["result_argument_bindings"] += [
        dict(
            binding_id="bank2:client:submission-id",
            source_action_id="bank2:client:submit",
            target_action_id="bank2:client:register",
            argument_name="submission_id",
            result_path=["id"],
            only_if_target_attempted=True,
        ),
        dict(
            binding_id="bank2:client:bank-id",
            source_action_id="bank2:client:register",
            target_action_id="bank2:client:accepted",
            argument_name="statement_id",
            result_path=["id"],
            only_if_target_attempted=True,
        ),
    ]
    task["scenario_version"] = VERSION
    task["description"]["notes"] = (
        VERSION
        + "; parent v24r financial objective unchanged; shared discovery and deterministic customer statement"
    )
    with_contract(tasks, db)
    for name, obj in [
        ("db.json", db),
        ("tasks.json", tasks),
        ("manifest.json", manifest),
    ]:
        (destination / name).write_text(
            json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
        )
    sync_manifest(destination)
    (destination / "author-version.json").write_text(
        json.dumps(
            dict(
                version=VERSION,
                parent=str(parent),
                statement_id=bankid,
                submission_id=sid,
                paths=paths,
                other60_behavioral_validation="not run",
                canonical_data_promoted=False,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return destination


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parent", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    print(build(a.parent, a.out))
