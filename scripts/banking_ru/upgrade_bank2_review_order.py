"""Author-only prototype: operational settlement versus formally registered review."""

import hashlib
import json
import shutil

from build_access_contract import with_contract
from upgrade_bank2_multisource import sync_manifest
from upgrade_composite_rewards import action, assertion, evidence

VERSION = "2026-10-21-bank2-review-order-v26"
C = "gromov_a_1187"
CASE = "case_610903"
IDENT = "reward_formal_review"
BODY = """# Передача дела о вознаграждении на рассмотрение
Открытое дело cashback_investigation хранит редактируемый состав требований. review_status=draft означает подготовку, registered — передачу в подразделение рассмотрения. submit_cashback_review(case_id,expected_version) фиксирует текущую сумму и покупки, увеличивает версию клиентского подтверждения. Пока дело рассматривается, оперативные выплаты по его покупкам и изменение состава недоступны: требования зарезервированы в отдельном контуре, чтобы не возместить их дважды. Отмена через поддержку не предусмотрена; рассмотрение завершается подразделением позднее. Это правило продукта, а не установленный законом запрет.
FAQ. Клиентское заявление submit_reward_statement о расхождении партнёрской активации — входящий материал для проверки начисления. Его банковское accepted не является передачей дела на рассмотрение и не резервирует требования. Передача целого дела — самостоятельное поручение клиента. expected_version берётся из результата чтения или изменения дела; version_conflict ничего не регистрирует. Повтор submit_cashback_review с версией исходного состава возвращает ту же регистрацию.
Редакция review-intake-v1. Действует с 2026-06-01.
"""


def build(parent, destination, distributed=False):
    shutil.copytree(parent, destination)
    tasks = json.loads((destination / "tasks.json").read_text())
    db = json.loads((destination / "db.json").read_text())
    m = json.loads((destination / "manifest.json").read_text())
    t = next(t for t in tasks if t["id"] == "Bank 2")
    assert t["scenario_version"] == "2026-10-21-bank2-discoverable-statements-v25"
    body = BODY
    if distributed:
        body = """# Передача дела о вознаграждении на рассмотрение
Регистрация обращения в CRM и направление материалов в профильное подразделение — разные этапы. Подготовленные требования передаются операцией submit_cashback_review(case_id,expected_version). При направлении review_status становится registered, ответственный контур — rewards_review; фиксируются покупки и сумма. Состав принятого материала в поддержке не редактируется. Приём увеличивает версию клиентского подтверждения. Отмена передачи через поддержку не предусмотрена: дальнейшие решения относятся к получившему материалы подразделению.
Клиентское заявление submit_reward_statement по партнёрской активации — входящее подтверждение фактов, а не направление материалов дела. Его accepted относится к этому подтверждению; ответственный контур дела не меняется. Версию для передачи получите из CRM. Повтор с версией направленного состава возвращает ту же регистрацию; version_conflict не создаёт передачи.
Редакция review-intake-v2. Действует с 2026-06-01.
"""
    p = "/knowledge/service_notes/doc_0153.md"
    f = destination / "corpus" / p.lstrip("/")
    f.write_text(body)
    m["documents"][p] = dict(
        sha256=hashlib.sha256(f.read_bytes()).hexdigest(),
        metadata=dict(
            id=IDENT,
            title="Передача дела о вознаграждении на рассмотрение",
            revision_id="review-intake-v1",
            effective_from="2026-06-01",
        ),
    )
    paths = {d["metadata"]["id"]: path for path, d in m["documents"].items()}
    additions = {
        "reward_case_amendment": "Редактирование состава относится к подготовке дела. Состояния передачи перечислены в «Передача дела о вознаграждении на рассмотрение».",
        "reward_resolution_execution": "Оперативная выплата несовместима с зарегистрированным рассмотрением по тем же покупкам; см. «Передача дела о вознаграждении на рассмотрение».",
        "kb_case_document_delivery": "Передача дела на рассмотрение обновляет версию официального подтверждения; актуальные метаданные получает get_case_documents.",
    }
    if distributed:
        additions["reward_case_amendment"] = (
            "Подготовка и регистрация обращения в CRM не означают передачу материалов профильному подразделению. Операция направления описана в регламенте «Передача дела о вознаграждении на рассмотрение»."
        )
        additions["reward_resolution_execution"] = (
            "Применимость полномочий определяется разделом о правах по соглашению, состав требований — данными CRM."
        )
        additions["reward_resolution_rights"] = (
            "Распределение полномочий. Оперативное денежное урегулирование в поддержке применяется к требованиям контура rewards_operations. Контур rewards_review осуществляет выплаты по собственному решению после профильного рассмотрения; отдельное оперативное соглашение поддержки к находящимся там требованиям не применяется. Передача материалов и регистрация обращения в CRM — разные события. Это внутреннее условие продукта, не законодательный запрет."
        )
    for ident, text in additions.items():
        path = paths[ident]
        f = destination / "corpus" / path.lstrip("/")
        f.write_text(f.read_text() + "\n" + text + "\n")
        m["documents"][path]["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
    receipts = {
        d["metadata"]["id"]: dict(
            id=d["metadata"]["id"],
            title=d["metadata"].get("title", d["metadata"]["id"]),
            content=(destination / "corpus" / path.lstrip("/")).read_text(),
        )
        for path, d in m["documents"].items()
    }
    for task in tasks:
        for row in task["evaluation_criteria"].get("result_evidence", []):
            for source in [row] + row.get("alternatives", []):
                if "file_read" in source.get("tool_names", []):
                    source["expected_fields"] = receipts[source["record_id"]]
    t["ticket"] = (
        t["ticket"]
        .replace(
            "Проверьте кешбэк",
            "Хочу передать существующее дело о кешбэке на формальное рассмотрение в профильное подразделение. Проверьте кешбэк",
        )
        .replace(
            "После завершения изменений пришлите",
            "В дело должны попасть именно оставшиеся после выплаты требования. Порядок действий выбирайте по правилам банка. После передачи дела на рассмотрение пришлите",
        )
    )
    # Draft/registered is a common field on all cases, never an ID-based environment rule.
    for case in db["cases"].values():
        case.update(
            review_status="draft",
            review_source_version=None,
            review_amount=None,
            review_items=[],
        )
    ec = t["evaluation_criteria"]
    a = ec["actions"]
    a.insert(3, action("bank2:review:doc", "file_read", dict(path=p)))
    i = next(i for i, x in enumerate(a) if x["action_id"] == "bank2:v4:amend")
    a.insert(
        i + 1,
        action(
            "bank2:review:submit",
            "submit_cashback_review",
            dict(case_id=CASE, expected_version=2),
            required=True,
            compare=["case_id"],
        ),
    )
    ec["env_assertions"].append(assertion("assert_cashback_review", dict(case_id=CASE)))
    ec["result_evidence"].append(
        evidence(
            "bank2:review:received-policy",
            ["file_read", "corpus_command"],
            IDENT,
            receipts[IDENT],
            "bank2:review:submit",
            only_if_target_attempted=True,
        )
    )
    ec["result_evidence"][-1]["alternatives"] = [
        dict(
            tool_names=["file_search", "corpus_command"],
            record_id=IDENT,
            expected_fields=dict(
                id=IDENT,
                path=p,
                line=2,
                text=body.splitlines()[1],
                line_truncated=False,
            ),
        )
    ]
    ec["result_evidence"].append(
        evidence(
            "bank2:review:current-composition",
            ["amend_cashback_investigation", "get_cashback_investigations"],
            CASE,
            dict(customer_id=C, amount=27.25, review_status="draft"),
            "bank2:review:submit",
            only_if_target_attempted=True,
        )
    )
    next(x for x in a if x["name"] == "deliver_case_document")["arguments"][
        "expected_version"
    ] = "3"
    # Metadata must be current after registration; independent channel confirmation may precede it.
    row = next(
        r
        for r in ec["result_evidence"]
        if r["evidence_id"] == "bank2:package:current-document"
    )
    row["after_action_id"] = "bank2:review:submit"
    t["scenario_version"] = VERSION + ("-distributed-v27" if distributed else "")
    t["description"]["notes"] = (
        VERSION
        + "; frozen v25 financial objective retained; formal review locks same-purchase settlement"
    )
    with_contract(tasks, db)
    for name, obj in [("tasks.json", tasks), ("db.json", db), ("manifest.json", m)]:
        (destination / name).write_text(
            json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
        )
    sync_manifest(destination)
    (destination / "author-version.json").write_text(
        json.dumps(
            dict(
                version=t["scenario_version"],
                parent=str(parent),
                distributed_rules=distributed,
                canonical_data_promoted=False,
                other60_behavioral_validation="not run",
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return destination
