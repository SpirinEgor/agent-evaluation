"""Return N40 document edits for sequential shared integration; no writes on import."""

from pathlib import Path


def with_documents(data_dir: Path):
    files = {}
    bodies = {
        "kb_322": """# Уже выплаченный кешбэк и остаток компенсации

Булевый cashback_granted — только признак произошедшего начисления, а не размер
и не доказательство полной компенсации. Фактическую сумму получите из проведённых
kind=cashback с original_transaction_id нужной покупки. Отсутствующая, частичная
и полная выплата различаются суммой. Частичная выплата не исключает покупку целиком:
если после расчёта остаётся положительная недоплата, она включается в расследование.
Полная компенсация исключает повторное возмещение. Сначала применяется потолок,
затем вычитается фактически выплаченная сумма.
Общие правила: [сверка возвратов и выплат](/knowledge/kb_reward_reconciliation.md).
""",
        "kb_333": """# Потолок компенсации N40

Базу каждой подходящей покупки уменьшите только на её проведённые связанные
возвраты. Примените скорректированную ставку, затем потолок 130 ₽ отдельно к каждой
покупке. После этого вычтите фактические проведённые выплаты по этой покупке.
Положительные остатки суммируются в расследовании; потолок к общей группе не применяется.
Общие правила: [сверка возвратов и выплат](/knowledge/kb_reward_reconciliation.md).
""",
    }
    for stem in ["kb_320", "kb_322", "kb_329", "kb_330", "kb_333"]:
        path = data_dir / "corpus/knowledge" / f"{stem}.md"
        text = path.read_text()
        _, meta, body = text.split("---", 2)
        import re

        meta = re.sub(
            r"revision_id:.*", 'revision_id: "2026-10-14-investigation"', meta
        )
        if stem in bodies:
            body = "\n\n" + bodies[stem]
        if stem == "kb_320":
            body = body.replace(
                "Подходят некомпенсированные покупки",
                "Подходят покупки с положительным невозмещённым остатком",
            )
            body = body.replace(
                "Cashback_granted=true исключает повторную компенсацию.",
                "Булевый cashback_granted не определяет размер выплаты: фактические частичные и полные компенсации сверяются по связанным проведённым выплатам.",
            )
            body = body.replace(
                "По каждой операции рассчитать сумму и применить потолок 130 ₽, затем проверить отсутствие начисления.",
                "По каждой операции уменьшить базу на проведённые связанные возвраты, применить скорректированную ставку и потолок 130 ₽ отдельно к каждой покупке, затем вычесть фактически выплаченный связанный кешбэк. Общая сверка определяется [правилами возвратов и выплат](/knowledge/kb_reward_reconciliation.md).",
            )
            body = body.replace(
                "направить диагностику профильному подразделению.",
                "зарегистрировать одно расследование create_cashback_investigation с program=N40, суммой всех положительных остатков и items с разбивкой по исходным покупкам. Расследование не зачисляет деньги.",
            )
        if stem == "kb_329":
            body = body.replace(
                "отсутствие уже начисленного кешбэка",
                "положительный невозмещённый остаток после фактических проведённых связанных выплат",
            )
        if stem == "kb_330":
            body = body.replace(
                "зарегистрировать диагностику в лояльности и запросить корректировку в пределах полномочий.",
                "зарегистрировать одно немонетарное расследование create_cashback_investigation в лояльности: program=N40, amount равна общей недоплате, items содержат исходные покупки и положительные остатки по каждой. До 1 сентября этот маршрут просроченной недоплаты запрещён. Само расследование и наступление срока не дают разрешения grant_cashback.",
            )
            body = body.replace(
                "Уже зачисленная компенсация исключает повторное начисление;",
                "Полностью зачисленная компенсация исключает повторное начисление; частичная выплата сохраняет положительный остаток для расследования;",
            )
        files[path] = "---" + meta + "---" + body
    return files


def with_bundle(data_dir: Path):
    """Rebuild only N40 bundles from current atomic documents; parent applies outputs."""
    import json

    import yaml

    folder = data_dir / "corpus/knowledge"
    overview = folder / "kb_final_41.md"
    raw = overview.read_text()
    _, header, body = raw.split("---", 2)
    meta = yaml.safe_load(header)
    sources = [f"/knowledge/kb_{n}.md" for n in range(320, 334)] + [
        "/knowledge/kb_reward_reconciliation.md"
    ]
    meta["related_paths"] = sources
    groups = []
    current = []
    lines = 0
    for source in sources:
        text = (data_dir / "corpus" / source.lstrip("/")).read_text()
        source_body = (
            text.split("---", 2)[-1].strip() if text.startswith("---") else text.strip()
        )
        count = len(source_body.splitlines()) + 4
        if current and lines + count > 170:
            groups.append(current)
            current = []
            lines = 0
        current.append((source, text, source_body))
        lines += count
    if current:
        groups.append(current)
    outputs = {}
    bundles = []
    for index, group in enumerate(groups, 1):
        target = folder / f"kb_final_41_part_{index}.md"
        bundles.append(f"/knowledge/{target.name}")
        data = dict(
            meta,
            id=f"kb_final_41_part_{index}",
            document_type="bank-consolidated-procedure",
            revision_id="2026-10-14-investigation",
            revision_date="2026-10-14",
        )
        data.pop("related_paths", None)
        data.pop("related_bundle_paths", None)
        data["source_paths"] = [s for s, _, _ in group]
        records = [yaml.safe_load(t.split("---", 2)[1]) for _, t, _ in group]
        data["source_ids"] = [r["id"] for r in records]
        data["source_revisions"] = {r["id"]: r["revision_id"] for r in records}
        result = "# N40 — полные источники\n\n" + "".join(
            f"Источник: `{source}`.\n\n{text}\n\n" for source, _, text in group
        )
        rendered = (
            "---\n"
            + "\n".join(
                f"{key}: {json.dumps(value, ensure_ascii=False)}"
                for key, value in data.items()
            )
            + "\n---\n\n"
            + result
        )
        if len(rendered.splitlines()) > 200:
            raise ValueError("Bundle exceeds full read limit")
        outputs[target] = rendered
    meta.update(
        related_bundle_paths=bundles,
        revision_id="2026-10-14-investigation",
        revision_date="2026-10-14",
    )
    nav = (
        "\n\n## Полные источники\n\n"
        + "\n".join(f"- [{Path(path).stem}]({path})" for path in sources)
        + "\n\n## Сборники полных текстов\n\n"
        + "\n".join(
            f"- [Часть {index}]({path})" for index, path in enumerate(bundles, 1)
        )
        + "\n"
    )
    intro = body.split("## Полные источники", 1)[0].rstrip()
    outputs[overview] = (
        "---\n"
        + "\n".join(
            f"{key}: {json.dumps(value, ensure_ascii=False)}"
            for key, value in meta.items()
        )
        + "\n---"
        + intro
        + nav
    )
    return outputs
