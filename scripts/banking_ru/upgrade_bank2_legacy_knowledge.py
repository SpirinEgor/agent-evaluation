"""Author-only migration: shared, uneven helpdesk articles, unchanged business state."""

import hashlib
import json
import re
import shutil
from copy import deepcopy
from pathlib import Path

from upgrade_bank2_multisource import sync_manifest

VERSION = "2026-10-21-bank2-legacy-knowledge-v24r"

# These are solver suggestions, not rates, eligibility conditions or API semantics.
COACHING = [
    r"При цели максимизации сравните исполнимые подмножества меню и обычные\s+предложения; выбор только самой большой отдельной доплаты или последовательный\s+жадный выбор не гарантирует максимум\.",
    r"Нельзя считать верхнюю\s+границу предложения достижимой, не проверив допустимую комбинацию\.",
]

REWRITES = {
    "reward_rate_history": """# Почему ставка в выписке отличается от текущей?
Для cafe_rewards в редакции multi-program-v1 ставку определяет дата самой покупки.
| Когда совершена покупка | Обычная ставка в категории кафе |
| --- | --- |
| Июнь 2026 года, весь месяц | 10% |
| Июль 2026 года, весь месяц | 5% |
Обе границы каждого месяца включаются. Текущая ставка к прошлой покупке не переносится.
Категория устанавливается по MCC. Дата покупки и дата проводки могут попадать в разные месяцы: это разные основания для выбора ставки и расчётного периода. Продукт и редакция берутся из договора вознаграждений.
""",
    "reward_program_priority": """# Памятка по совмещению программ
Условия multi-program-v1 относятся к следующим ситуациям.
| Условия покупки | Начисление | Обычный лимит договора |
| --- | --- | --- |
| Подходит партнёрское предложение и есть применимая активация | Партнёрская программа заменяет обычную | Не расходуется |
| Применимого партнёрского предложения нет | Обычный кешбэк по его ставке и лимиту | Применяется |
Две ставки для одной покупки не складываются. Для определения применимости используются факты предложения и активации.
""",
    "reward_partner_offer": """# Кофемания: условия летнего предложения
10% за подходящие покупки. Предложение promo_730415, продукт cafe_rewards; продавец Кофемания, MCC 5812. Период покупок — с 1 по 31 июля 2026 года включительно.
Участие после активации: она должна состояться не позднее точного момента покупки. Если до покупки активация отменена, покупка в предложение не входит. Время активации учитывается по банковскому журналу с часовым смещением; воспоминание клиента не заменяет запись журнала.
Мелкий шрифт: база — сумма после проведённых связанных возвратов. Максимум 100 рублей на покупку; округление до копеек половины вверх. Подходящее предложение заменяет обычную программу, а не прибавляется к ней.
""",
    "reward_card_reissue": """# Новый пластик, прежний договор
В multi-program-v1 связанные карты одного договора используют один общий лимит. Перевыпуск не открывает новый расчётный период и не создаёт новый лимит.
В карточке договора связь замены задана полями replaced_card_id, replacement_card_id, replacement_effective_from. Проведённые покупки старой и новой карты учитываются в договоре по его правилам периода. Счёт и владелец покупки должны совпадать с договором.
""",
    "reward_refund_adjustments": """# Что меняется при возврате покупки?
Вопрос: какие возвраты учитываются в multi-program-v1?
Ответ: проведённые записи с original_transaction_id исходной покупки. Они уменьшают её базу, но не ниже нуля; более поздние проведённые возвраты тоже учитываются, если получены не позднее settlement_as_of. Владелец, счёт и карта должны совпадать.
Вопрос: незавершённый возврат уже уменьшает базу?
Ответ: нет.
Вопрос: запись с compensates_cashback=true — возврат товара?
Ответ: нет. Это компенсация кешбэка: базу покупки она не меняет, соответствующее обязательство погашает.
""",
}


def stripped(content):
    if content.startswith("---\n"):
        return content.split("---", 2)[-1].strip()
    return content.strip()


def build(parent: Path, destination: Path):
    shutil.copytree(parent, destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    tasks = json.loads((destination / "tasks.json").read_text())
    bank2 = next(t for t in tasks if t["id"] == "Bank 2")
    assert bank2["scenario_version"] == "2026-10-21-bank2-document-packages-v23-unknown"
    before_db = hashlib.sha256((destination / "db.json").read_bytes()).hexdigest()
    old_docs = deepcopy(manifest["documents"])
    bodies = {p: (destination / "corpus" / p.lstrip("/")).read_text() for p in old_docs}
    path_map = {}
    new_bodies = {}
    removed = []
    for index, path in enumerate(sorted(old_docs)):
        if path == "/AGENTS.md":
            path_map[path] = path
            new_bodies[path] = bodies[path]
            continue
        folder = ["service_notes", "exports", "product_help", "faq"][index % 4]
        area = "policies" if path.startswith("/policies/") else "knowledge"
        path_map[path] = f"/{area}/{folder}/doc_{index:04}.md"

    def plain(text):
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        for old, descriptor in old_docs.items():
            text = text.replace(
                old,
                "«"
                + descriptor["metadata"].get("title", descriptor["metadata"]["id"])
                + "»",
            )
        for pattern in COACHING:

            def omit(match):
                removed.append(match.group())
                return ""

            text = re.sub(pattern, omit, text)
        return text

    def render(path, body, index):
        meta = old_docs[path]["metadata"]
        title = meta.get("title", meta["id"])
        text = plain(stripped(body))
        text = re.sub(r"^# [^\n]+\n*", "", text, count=1)
        # Office exports often flatten paragraphs, while still returning the full article.
        blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", text) if b.strip()]
        if index % 4 == 0:
            article = (
                "# "
                + title
                + "\nВопрос: какие условия относятся к этой операции?\nОтвет: "
                + "\n".join(blocks)
            )
        elif index % 4 == 1:
            article = title + "\nСлужебная копия для сотрудников.\n" + "\n".join(blocks)
        elif index % 4 == 2:
            article = "# " + title + "\n" + "\n".join(blocks)
        else:
            article = title + "\nСправочная информация.\n" + "\n".join(blocks)
        notes = []
        if meta.get("section"):
            notes.append("Раздел: " + str(meta["section"]) + ".")
        if meta.get("revision_id"):
            notes.append("Редакция " + str(meta["revision_id"]) + ".")
        if meta.get("effective_from"):
            notes.append("Применяется с " + str(meta["effective_from"]) + ".")
        if meta.get("effective_to"):
            notes.append("Действует по " + str(meta["effective_to"]) + " включительно.")
        if meta.get("superseded_by"):
            successor = meta["superseded_by"]
            titles = {
                d["metadata"]["id"]: d["metadata"].get("title", d["metadata"]["id"])
                for d in old_docs.values()
            }
            notes.append(
                "Заменён материалом «" + titles.get(successor, str(successor)) + "»."
            )
        note = " ".join(notes)
        if index % 2:
            article += "\n" + note
        else:
            article = note + "\n" + article
        return article.strip() + "\n"

    for index, path in enumerate(sorted(old_docs)):
        if path == "/AGENTS.md":
            continue
        if old_docs[path]["metadata"].get("source_paths"):
            continue
        ident = old_docs[path]["metadata"]["id"]
        if ident in REWRITES:
            # Curated FAQ/product/table formats retain all local business conditions.
            meta = old_docs[path]["metadata"]
            body = REWRITES[ident]
            appendix = " ".join(
                label + str(meta[k]) + "."
                for k, label in [
                    ("revision_id", "Редакция "),
                    ("effective_from", "Применяется с "),
                    ("effective_to", "Действует по "),
                ]
                if meta.get(k) is not None
            )
            new_bodies[path] = body + "\nСведения выгрузки: " + appendix + "\n"
        else:
            new_bodies[path] = render(path, bodies[path], index)

    def aggregate(path):
        if path in new_bodies:
            return new_bodies[path]
        doc = old_docs[path]
        sources = doc["metadata"].get("source_paths", [])
        remaining = stripped(bodies[path])
        for source in sources:
            remaining = remaining.replace(stripped(bodies[source]), "")
        preface = render(path, remaining, 0)
        new_bodies[path] = (
            preface + "\n" + "\n".join(aggregate(source) for source in sources)
        )
        return new_bodies[path]

    for path in old_docs:
        aggregate(path)
    # Rebuild the allowlist; there is no alias that reveals the old procedural map.
    shutil.rmtree(destination / "corpus")
    manifest["documents"] = {}
    manifest["presentation"] = "legacy_helpdesk"
    for path, descriptor in old_docs.items():
        target = path_map[path]
        body = new_bodies[path]
        assert len(body.encode()) <= 65536, (path, len(body.encode()))
        assert len(body.splitlines()) <= 200, (path, len(body.splitlines()))
        assert not re.search(r"\[[^\]]+\]\([^)]+\)", body), path
        assert not body.startswith("---\n"), path
        file = destination / "corpus" / target.lstrip("/")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body)
        d = deepcopy(descriptor)
        d["sha256"] = hashlib.sha256(body.encode()).hexdigest()
        if d["metadata"].get("source_paths"):
            d["metadata"]["source_paths"] = [
                path_map[x] for x in d["metadata"]["source_paths"]
            ]
        manifest["documents"][target] = d
    id_paths = {d["metadata"]["id"]: p for p, d in old_docs.items()}

    def receipt(path):
        return dict(
            id=old_docs[path]["metadata"]["id"],
            title=old_docs[path]["metadata"].get(
                "title", old_docs[path]["metadata"]["id"]
            ),
            content=new_bodies[path],
        )

    # Preserve direct, embedded-source and all-parts source routes without exposing audit receipts.
    contains = {p: [] for p in old_docs}
    for aggregate_path, d in old_docs.items():
        for source in d["metadata"].get("source_paths", []):
            if new_bodies[source] in new_bodies[aggregate_path]:
                contains[source].append(aggregate_path)

    def rebind(item):
        if "file_read" not in item.get("tool_names", []):
            row = deepcopy(item)
            for alt in row.get("alternatives", []):
                if "file_read" in alt.get("tool_names", []):
                    ap = alt.get("expected_fields", {}).get("path") or id_paths.get(
                        alt.get("record_id")
                    )
                    if ap not in old_docs:
                        raise ValueError(("Unknown alternative source", alt))
                    alt["record_id"] = receipt(ap)["id"]
                    alt["expected_fields"] = receipt(ap)
            return [row]
        fields = item.get("expected_fields", {})
        path = fields.get("path")
        if path not in old_docs:
            path = id_paths.get(item.get("record_id"))
        if path not in old_docs:
            raise ValueError(("Unknown source", item))
        parts = old_docs[path]["metadata"].get("source_paths", []) or [path]
        result = []
        for index, part in enumerate(parts):
            row = deepcopy(item)
            row["record_id"] = receipt(part)["id"]
            row["expected_fields"] = receipt(part)
            if len(parts) > 1:
                row["evidence_id"] += ":part:" + str(index)
            alternatives = []
            for alt in item.get("alternatives", []):
                if "file_read" in alt["tool_names"]:
                    ap = alt["expected_fields"].get("path") or id_paths.get(
                        alt["record_id"]
                    )
                    alternatives.append(
                        dict(
                            tool_names=["file_read"],
                            record_id=receipt(ap)["id"],
                            expected_fields=receipt(ap),
                        )
                    )
                else:
                    alternatives.append(alt)
            for ap in set(contains[part] + ([path] if part != path else [])):
                alternatives.append(
                    dict(
                        tool_names=["file_read"],
                        record_id=receipt(ap)["id"],
                        expected_fields=receipt(ap),
                    )
                )
            row["alternatives"] = alternatives
            result.append(row)
        return result

    def remap(value):
        if isinstance(value, dict):
            return {path_map.get(k, k): remap(v) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        if isinstance(value, str):
            return path_map.get(value, value)
        return value

    for task in tasks:
        ec = task["evaluation_criteria"]
        updated = []
        for item in ec.get("result_evidence", []):
            updated.extend(rebind(item))
        ec["result_evidence"] = updated
        for action in ec["actions"]:
            if action["name"] == "file_read":
                action.pop("result_contains", None)
                action.pop("result_patterns", None)
        for assertion in ec.get("env_assertions", []):
            if (
                assertion["env_type"] == "tool"
                and assertion.get("env_function_call", {}).get("env_function_name")
                == "assert_file_is_reachable"
            ):
                pass
        task.update(remap(task))

    # Reachability predicates refer to article text now, not removed YAML identifiers.
    def reachability(value):
        if isinstance(value, dict):
            if value.get("env_function_name") == "assert_file_is_reachable":
                args = value["arguments"]
                old = next(
                    (p for p, new in path_map.items() if new == args["path"]), None
                )
                if old:
                    args["pattern"] = re.escape(
                        old_docs[old]["metadata"].get(
                            "title", old_docs[old]["metadata"]["id"]
                        )
                    )
            for child in value.values():
                reachability(child)
        elif isinstance(value, list):
            for child in value:
                reachability(child)

    reachability(tasks)
    bank2 = next(t for t in tasks if t["id"] == "Bank 2")
    # Semantic source alternatives demonstrated in the scout. No search query or
    # ordering is prescribed: successful complete normative paragraphs suffice.
    # Replacement fields and owner/account are independently bound to the received
    # get_reward_contracts result already required by the same protected action.
    searchable = {
        "reward_product_scope": (
            "Ответ: Область расчёта",
            "При наличии банковских предложений",
        ),
        "reward_posting_period": ("В редакции multi-program-v1 период",),
        "reward_card_reissue": ("В multi-program-v1 связанные карты",),
    }
    revised = []
    for row in bank2["evaluation_criteria"]["result_evidence"]:
        ident = row.get("record_id")
        if ident not in searchable or "file_read" not in row.get("tool_names", []):
            revised.append(row)
            continue
        path = id_paths[ident]
        for i, prefix in enumerate(searchable[ident]):
            matches = [
                (line, text)
                for line, text in enumerate(new_bodies[path].splitlines(), 1)
                if text.startswith(prefix)
            ]
            assert len(matches) == 1, (ident, prefix, matches)
            line, text = matches[0]
            assert len(text) <= 800, (ident, len(text))
            guard = deepcopy(row)
            guard["evidence_id"] += ":rule:" + str(i)
            guard.setdefault("alternatives", []).append(
                dict(
                    tool_names=["file_search"],
                    record_id=ident,
                    expected_fields=dict(
                        id=ident,
                        path=path_map[path],
                        line=line,
                        text=text,
                        line_truncated=False,
                    ),
                )
            )
            revised.append(guard)
    bank2["evaluation_criteria"]["result_evidence"] = revised
    bank2["scenario_version"] = VERSION
    bank2["description"]["notes"] = (
        VERSION
        + "; parent v23-unknown; business state and objective unchanged; shared corpus presentation experiment"
    )
    (destination / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n"
    )
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    sync_manifest(destination)
    assert (
        before_db == hashlib.sha256((destination / "db.json").read_bytes()).hexdigest()
    )
    report = dict(
        version=VERSION,
        parent=str(parent),
        paths=path_map,
        removed_solver_coaching=removed,
        curated_rewrites=list(REWRITES),
        documents=len(old_docs),
        db_unchanged=True,
        intake_unchanged=(parent / "intake_answers.json").read_bytes()
        == (destination / "intake_answers.json").read_bytes(),
        shared_for_all_tasks=True,
        other60_behavioral_validation="deferred",
        no_new_runtime_task_id_branches=True,
    )
    (destination / "presentation-map.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return destination


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.parent, args.out))
