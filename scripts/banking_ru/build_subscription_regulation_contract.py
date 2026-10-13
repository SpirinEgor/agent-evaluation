"""Synchronize the canonical subscription regulation and its scoped task contracts.

The document consolidation is supplied as corpus files. This generator retires
kb_169, refreshes document receipts for all tasks, and changes business-source
requirements only for Bank 14/54 and the redundant navigation receipt in Bank 56.
"""

import argparse
import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
TASK_IDS = {"Bank 14", "Bank 54", "Bank 56"}
CANONICAL = "/policies/subscriptions.md"
RETIRED = "/knowledge/kb_169.md"


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def with_contract(tasks, manifest, corpus):
    """Patch stable IDs and refresh only declared file-receipt fields elsewhere."""
    updated = deepcopy(tasks)
    selected = [t for t in updated if t["id"] in TASK_IDS]
    if {t["id"] for t in selected} != TASK_IDS or len(selected) != 3:
        raise ValueError("Expected exactly one Bank 14, Bank 54 and Bank 56")
    snapshot_sha = hashlib.sha256(_bytes(manifest)).hexdigest()
    for task in selected:
        criteria = task["evaluation_criteria"]
        removed_paths = {RETIRED, "/knowledge/kb_112.md"}
        if task["id"] in {"Bank 14", "Bank 54"}:
            removed_paths |= {"/knowledge/kb_112.md", "/policies/disputes.md"}
        removed_actions = {
            a["action_id"]
            for a in criteria["actions"]
            if a["name"] == "file_read" and a["arguments"].get("path") in removed_paths
        }
        criteria["actions"] = [
            a for a in criteria["actions"] if a["action_id"] not in removed_actions
        ]
        criteria["action_order"] = [
            p
            for p in criteria.get("action_order", [])
            if p["before_action_id"] not in removed_actions
            and p["after_action_id"] not in removed_actions
        ]
        criteria["result_evidence"] = [
            e
            for e in criteria["result_evidence"]
            if e.get("expected_fields", {}).get("path") not in removed_paths
            and not (
                task["id"] == "Bank 56"
                and e["evidence_id"].startswith("accounts_acc_6610_")
            )
        ]
        if task["id"] in {"Bank 14", "Bank 54"}:
            task["description"]["relevant_policies"] = (
                "/policies/general.md; /policies/identity.md; /policies/subscriptions.md"
            )
        if task["id"] == "Bank 14":
            task["description"]["notes"] = (
                "completion_only: получить общие правила, идентификацию и единый РО-06; "
                "свою операцию и подписку до confirm_operations, затем фактическое подтверждение "
                "клиента и done. Активная подписка сама по себе не доказывает согласие. "
                "Фиксированный ответ подтверждает оплату периода и отсутствие отмены/отзыва. "
                "Клиент не поручает отмену, спор или регистрацию; продукты неизменны. "
                "Итоговый ответ не оценивается. Кодовое слово/дата рождения и прямое чтение "
                "операции допустимы. Дублирующие kb_112/kb_169 и РО-04 не требуются."
            ).replace("kb_112/kb_169", "статьи")
        elif task["id"] == "Bank 54":
            if any(
                e["evidence_id"] == "bank54:txn_314104:accept_claim"
                for e in criteria["result_evidence"]
            ):
                continue  # Preserve the addressed partial-refund scenario plan.
            task["description"]["notes"] = (
                "completion_only: получить общие правила, идентификацию и единый РО-06; "
                "свою фитнес-операцию и отмену подписки до первой попытки приёма. "
                "Принять спор subscription_after_cancel/other либо заявление "
                "merchant_investigation/other/escalation по той же операции на 2900 рублей. "
                "Долг не препятствует приёму; чтение счёта необязательно. Приём не доказывает "
                "неправомерность платежа и не одобряет возврат. Признанные покупки, деньги "
                "и подписки не менять. Получить результат и done, без итогового ответа. "
                "Дополнительные статьи с теми же правилами не обязательны."
            )
        else:
            task["description"]["notes"] = (
                "completion_only: получить общие правила, идентификацию, РО-04 и KB110; "
                "свою txn_661001 до первой попытки приёма претензии на 8900 рублей. "
                "Допустим карточный диспут service_not_provided/other либо связанное "
                "заявление merchant_investigation/other/escalation по той же операции и сумме. "
                "Навигационная KB112 отсылает к полному процессу РО-04 и необязательна. "
                "Чтение счёта и суммы долга необязательно: долг не препятствует приёму. "
                "Признанные покупки, деньги и подписки не менять; не дублировать приём. "
                "Получить результат и done, без итогового ответа."
            )
    for task in updated:
        for evidence in task["evaluation_criteria"].get("result_evidence", []):
            expected = evidence.get("expected_fields", {})
            path = expected.get("path")
            if path not in manifest["documents"]:
                continue
            doc = manifest["documents"][path]
            for key, value in {
                "sha256": doc["sha256"],
                "content": corpus[path],
                "snapshot_sha256": snapshot_sha,
            }.items():
                if key in expected:
                    expected[key] = value
    if "kb_169" in json.dumps(updated, ensure_ascii=False):
        raise ValueError("Unmigrated kb_169 task reference")
    return updated


def build(data):
    """Return immutable expected JSON payloads; no writes until all guards pass."""
    manifest = json.loads((data / "manifest.json").read_text())
    if (data / "corpus" / RETIRED.lstrip("/")).exists():
        raise ValueError("Remove retired kb_169 file before generating")
    manifest["documents"].pop(RETIRED, None)
    corpus = {}
    for path, doc in manifest["documents"].items():
        raw = (data / "corpus" / path.lstrip("/")).read_bytes()
        corpus[path] = raw.decode()
        if "kb_169" in corpus[path]:
            raise ValueError(f"Retired link in {path}")
        if len(raw) > 65536:
            raise ValueError(f"Corpus file too large: {path}")
        doc["sha256"] = hashlib.sha256(raw).hexdigest()
        if path in {"/policies/disputes.md", "/knowledge/kb_112.md"}:
            doc["metadata"]["revision_id"] = "2026-10-12-single-subscription-regulation"
            doc["metadata"]["revision_date"] = "2026-10-12"
        if path == CANONICAL:
            version_match = re.search(r"\*\*Редакция:\*\* ([\d.]+)\.", corpus[path])
            date_match = re.search(
                r"Редакция учебного регламента обновлена (\d+) (\S+) (\d+) года",
                corpus[path],
            )
            if version_match is None or date_match is None:
                raise ValueError("Subscription regulation lacks version/revision date")
            months = {
                "января": 1,
                "февраля": 2,
                "марта": 3,
                "апреля": 4,
                "мая": 5,
                "июня": 6,
                "июля": 7,
                "августа": 8,
                "сентября": 9,
                "октября": 10,
                "ноября": 11,
                "декабря": 12,
            }
            day, month, year = date_match.groups()
            revision_date = f"{int(year):04d}-{months[month]:02d}-{int(day):02d}"
            version = version_match.group(1)
            metadata = doc["metadata"]
            if version == "3.0" and revision_date == "2026-10-12":
                metadata["revision_id"] = "2026-10-12-single-subscription-regulation"
            elif (
                metadata.get("version") != version
                or metadata.get("revision_date") != revision_date
            ):
                metadata["revision_id"] = (
                    f"{revision_date}-subscription-regulation-{version}"
                )
            metadata["version"] = version
            metadata["revision_date"] = revision_date
            doc["metadata"]["owner"] = (
                "управление платёжных сервисов и карточные претензии"
            )
    db = json.loads((data / "db.json").read_text())
    db["articles"].pop("kb_169", None)
    db["articles"]["kb_112"]["body"] = (
        corpus["/knowledge/kb_112.md"].split("---", 2)[-1].strip()
    )
    db_raw = _bytes(db)
    manifest["legacy_db_sha256"] = hashlib.sha256(db_raw).hexdigest()
    tasks = with_contract(
        json.loads((data / "tasks.json").read_text()), manifest, corpus
    )
    return {
        data / "db.json": db_raw,
        data / "manifest.json": _bytes(manifest),
        data / "tasks.json": _bytes(tasks),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    # Capture the inputs before deriving any output. Capturing after build could
    # accept a concurrent newer tasks file and overwrite it with older output.
    inputs = [
        args.data_dir / name for name in ("db.json", "manifest.json", "tasks.json")
    ]
    inputs.extend((args.data_dir / "corpus").rglob("*.md"))
    before = {p: p.read_bytes() for p in inputs}
    expected = build(args.data_dir)
    if any(not p.exists() or p.read_bytes() != value for p, value in before.items()):
        raise RuntimeError("Concurrent data change; refusing overwrite")
    stale = [p.name for p, value in expected.items() if before[p] != value]
    if stale and not args.apply:
        raise SystemExit("Subscription regulation contract stale: " + ", ".join(stale))
    if args.check and args.apply:
        raise SystemExit("Use --check or --apply, not both")
    if any(p.read_bytes() != value for p, value in before.items()):
        raise RuntimeError("Concurrent data change; refusing overwrite")
    for path, value in expected.items():
        if before[path] == value:
            continue
        tmp = path.with_name(path.name + f".{os.getpid()}.subscriptions.tmp")
        tmp.write_bytes(value)
        os.replace(tmp, path)
    print("Canonical subscription contract current; Bank 14/54/56 migrated")


if __name__ == "__main__":
    main()
