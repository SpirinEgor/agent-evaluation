"""Bound oversized source packages while preserving full original article access.

Only Bank 41/53 reference reads and the Bank 41 aggregate evidence are migrated.
Other tasks receive updated hashes only where they already pin a changed snapshot.
This is a data migration, not validation of the affected task trajectories.
"""

import argparse
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
PACKAGES = ("41", "53")


def serialized(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def validate_corpus(data):
    """Reject any exposed Markdown file that cannot fit a single full read."""
    manifest = json.loads((data / "manifest.json").read_text())
    for path in manifest["documents"]:
        raw = (data / "corpus" / path.lstrip("/")).read_bytes()
        if len(raw.decode().splitlines()) > 200:
            raise ValueError(f"Corpus file exceeds 200 lines: {path}")
        if len(raw) > 65536:
            raise ValueError(f"Corpus file exceeds 65536 bytes: {path}")


def _body(content):
    return (
        content.split("---", 2)[-1].strip()
        if content.startswith("---\n")
        else content.strip()
    )


def build(data):
    """Compute bounded corpus files, immutable manifest, and scoped task migration."""
    manifest = json.loads((data / "manifest.json").read_text())
    original_tasks = json.loads((data / "tasks.json").read_text())
    tasks = deepcopy(original_tasks)
    outputs = {}
    migration = {}
    for number in PACKAGES:
        path = f"/knowledge/kb_final_{number}.md"
        descriptor = manifest["documents"][path]
        meta = descriptor["metadata"]
        sources = meta.get("source_paths")
        if not sources:
            continue  # already migrated overview
        original = (data / "corpus" / path.lstrip("/")).read_text()
        overview_body = (
            _body(original).split("## Полные тексты источников", 1)[0].rstrip()
        )
        overview_body = overview_body.replace(
            "Сопроводить обращение об инциденте N40 по одному полному рабочему документу.",
            "Сопроводить обращение об инциденте N40 по обзору и связанным полным источникам.",
        ).replace(
            "Полное чтение этого документа даёт все перечисленные правила; отдельный поиск каждого фрагмента не требуется.",
            "Получить связанные полные источники. Этот обзор не заменяет чтение правил.",
        )
        overview_meta = deepcopy(meta)
        for key in ("source_paths", "source_ids", "source_revisions"):
            overview_meta.pop(key, None)
        overview_meta["document_type"] = "bank-procedure-overview"
        overview_meta["revision_id"] = "2026-10-12-bounded-overview"
        overview_meta["revision_date"] = "2026-10-12"
        # Provenance/navigation deliberately has no receipt-equivalence meaning.
        overview_meta["related_paths"] = sources
        groups = []
        current = []
        current_lines = 0
        for source in sources:
            content = (data / "corpus" / source.lstrip("/")).read_text()
            lines = len(_body(content).splitlines()) + 4
            if current and current_lines + lines > 175:
                groups.append(current)
                current, current_lines = [], 0
            current.append(source)
            current_lines += lines
        if current:
            groups.append(current)
        bundle_paths = []
        for index, group in enumerate(groups, 1):
            bundle_path = f"/knowledge/kb_final_{number}_part_{index}.md"
            bundle_paths.append(bundle_path)
            bundle_meta = deepcopy(meta)
            bundle_meta.update(
                {
                    "id": f"kb_final_{number}_part_{index}",
                    "title": f"{meta['title']} — полные источники, часть {index}",
                    "revision_id": "2026-10-12-bounded-sources",
                    "revision_date": "2026-10-12",
                    "source_paths": group,
                    "source_ids": [
                        manifest["documents"][p]["metadata"]["id"] for p in group
                    ],
                    "source_revisions": {
                        manifest["documents"][p]["metadata"]["id"]: manifest[
                            "documents"
                        ][p]["metadata"]["revision_id"]
                        for p in group
                    },
                }
            )
            body = f"# {bundle_meta['title']}\n\nОбласть применения и уточнения: [{meta['title']}]({path}).\n\n"
            for source in group:
                content = (data / "corpus" / source.lstrip("/")).read_text()
                body += f"Источник: `{source}`.\n\n{_body(content)}\n\n"
            raw = (
                "---\n"
                + "\n".join(
                    f"{k}: {json.dumps(v, ensure_ascii=False)}"
                    for k, v in bundle_meta.items()
                )
                + "\n---\n\n"
                + body
            ).encode()
            if len(raw.decode().splitlines()) > 200:
                raise ValueError(f"Generated bundle exceeds 200 lines: {bundle_path}")
            outputs[data / "corpus" / bundle_path.lstrip("/")] = raw
            manifest["documents"][bundle_path] = {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "metadata": bundle_meta,
            }
        overview_meta["related_bundle_paths"] = bundle_paths
        navigation = (
            "\n\n## Полные источники\n\n"
            + "\n".join(
                f"- [{manifest['documents'][p]['metadata']['title']}]({p})"
                for p in sources
            )
            + "\n\n## Сборники полных текстов\n\n"
            + "\n".join(f"- [Часть {i}]({p})" for i, p in enumerate(bundle_paths, 1))
            + "\n"
        )
        raw = (
            "---\n"
            + "\n".join(
                f"{k}: {json.dumps(v, ensure_ascii=False)}"
                for k, v in overview_meta.items()
            )
            + "\n---\n\n"
            + overview_body
            + navigation
        ).encode()
        outputs[data / "corpus" / path.lstrip("/")] = raw
        manifest["documents"][path] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "metadata": overview_meta,
        }
        migration[number] = {
            "overview": path,
            "sources": sources,
            "bundles": bundle_paths,
        }
        task = next(t for t in tasks if t["id"] == f"Bank {number}")
        criteria = task["evaluation_criteria"]
        action = next(
            a
            for a in criteria["actions"]
            if a["name"] == "file_read" and a["arguments"].get("path") == path
        )
        offset = criteria["actions"].index(action) + 1
        reads = [
            {
                "action_id": f"bounded-source:{source.rsplit('/', 1)[-1]}:Bank {number}",
                "name": "file_read",
                "arguments": {"path": source},
                "required_for_reward": False,
                "compare_args": ["path"],
            }
            for source in sources
        ]
        criteria["actions"][offset:offset] = reads
        if number == "41":
            evidence = next(
                e
                for e in criteria["result_evidence"]
                if e.get("expected_fields", {}).get("path") == path
            )
            for source in sources:
                item = deepcopy(evidence)
                document = manifest["documents"][source]
                item["evidence_id"] = (
                    f"bounded-source:{document['metadata']['id']}:Bank 41"
                )
                item["record_id"] = document["metadata"]["id"]
                item["expected_fields"] = {
                    "path": source,
                    "sha256": document["sha256"],
                    "complete": True,
                }
                criteria["result_evidence"].append(item)
    for task in tasks:
        if task["id"] in {"Bank 41", "Bank 53"}:
            evidence = task["evaluation_criteria"]["result_evidence"]
            task["evaluation_criteria"]["result_evidence"] = [
                e
                for e in evidence
                if not e["evidence_id"].startswith("common_discovery_")
            ] + [
                e for e in evidence if e["evidence_id"].startswith("common_discovery_")
            ]
    snapshot_sha = hashlib.sha256(serialized(manifest)).hexdigest()
    for task in tasks:
        for evidence in task["evaluation_criteria"].get("result_evidence", []):
            expected = evidence.get("expected_fields", {})
            path = expected.get("path")
            if path not in manifest["documents"]:
                continue
            descriptor = manifest["documents"][path]
            content = outputs.get(data / "corpus" / path.lstrip("/"))
            if content is None:
                content = (data / "corpus" / path.lstrip("/")).read_bytes()
            for key, value in {
                "sha256": descriptor["sha256"],
                "snapshot_sha256": snapshot_sha,
                "content": content.decode(),
            }.items():
                if key in expected:
                    expected[key] = value
    outputs[data / "manifest.json"] = serialized(manifest)
    outputs[data / "tasks.json"] = serialized(tasks)
    for path, raw in outputs.items():
        if path.suffix == ".md" and len(raw.decode().splitlines()) > 200:
            raise ValueError(f"Generated corpus file exceeds 200 lines: {path}")
    for virtual_path in manifest["documents"]:
        local_path = data / "corpus" / virtual_path.lstrip("/")
        raw = outputs.get(local_path)
        if raw is None:
            raw = local_path.read_bytes()
        if len(raw.decode().splitlines()) > 200:
            raise ValueError(f"Corpus file exceeds 200 lines: {virtual_path}")
        if len(raw) > 65536:
            raise ValueError(f"Corpus file exceeds 65536 bytes: {virtual_path}")
    return outputs, migration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    before = {
        p: p.read_bytes()
        for p in [
            args.data_dir / "tasks.json",
            args.data_dir / "manifest.json",
            *(args.data_dir / "corpus").rglob("*.md"),
        ]
    }
    outputs, migration = build(args.data_dir)
    if any(p.read_bytes() != raw for p, raw in before.items()):
        raise RuntimeError("Concurrent corpus/tasks change; refusing overwrite")
    changed = [
        p for p, raw in outputs.items() if not p.exists() or p.read_bytes() != raw
    ]
    if args.check and changed:
        raise SystemExit(
            "Bounded corpus migration stale: " + ", ".join(p.name for p in changed)
        )
    if args.apply:
        for path in changed:
            temporary = path.with_name(path.name + f".{os.getpid()}.bounded.tmp")
            temporary.write_bytes(outputs[path])
            os.replace(temporary, path)
    validate_corpus(args.data_dir)
    print(
        json.dumps(
            {"bounded_corpus_current": True, "migration": migration}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
