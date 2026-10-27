"""Refresh source metadata and existing receipts without changing task semantics."""

import argparse
import hashlib
import json
from pathlib import Path

from tau2.domains.banking_ru.corpus_manifest import (
    document_body,
    document_metadata,
    manifest_outputs,
)
from tau2.domains.banking_ru.task_store import read_json, task_outputs


def serialized(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def sync(data_dir, check=False):
    """Synchronize only supplied files, computing all outputs before writing."""
    manifest = read_json(data_dir / "manifest.json")
    db = read_json(data_dir / "db.json")
    tasks = read_json(data_dir / "tasks.json")
    corpus = {}
    for path, descriptor in manifest["documents"].items():
        raw = (data_dir / "corpus" / path.lstrip("/")).read_bytes()
        if len(raw) > 65536 or len(raw.decode().splitlines()) > 200:
            raise ValueError("Unbounded source: " + path)
        content = raw.decode()
        corpus[path] = content
        descriptor["sha256"] = hashlib.sha256(raw).hexdigest()
        body = document_body(raw)
        if content.startswith("---\n"):
            descriptor["metadata"] = document_metadata(raw)
        id = descriptor["metadata"].get("id")
        if id in db.get("articles", {}):
            db["articles"][id]["body"] = body.strip()
    db_raw = serialized(db)
    manifest["legacy_db_sha256"] = hashlib.sha256(db_raw).hexdigest()
    answers = data_dir / "intake_answers.json"
    if answers.exists():
        manifest["intake_answers_sha256"] = hashlib.sha256(
            answers.read_bytes()
        ).hexdigest()
    outputs = manifest_outputs(data_dir, manifest, {data_dir / "db.json": db_raw})
    manifest_raw = outputs[data_dir / "manifest.json"]
    for path, descriptor in manifest["documents"].items():
        local = data_dir / "corpus" / path.lstrip("/")
        if local in outputs:
            corpus[path] = outputs[local].decode()
            descriptor["sha256"] = hashlib.sha256(outputs[local]).hexdigest()
    snapshot = hashlib.sha256(manifest_raw).hexdigest()
    for task in tasks:
        receipts = task["evaluation_criteria"].get("result_evidence", [])
        sources = [
            source
            for receipt in receipts
            for source in [receipt, *receipt.get("alternatives", [])]
        ]
        for evidence in sources:
            expected = evidence.get("expected_fields", {})
            path = expected.get("path")
            if path not in corpus:
                continue
            values = {
                "content": corpus[path],
                "sha256": manifest["documents"][path]["sha256"],
                "snapshot_sha256": snapshot,
            }
            for field, value in values.items():
                if field in expected:
                    expected[field] = value
    outputs = {
        **outputs,
        **task_outputs(data_dir / "tasks.json", tasks),
    }
    stale = [
        str(path.relative_to(data_dir))
        for path, raw in outputs.items()
        if not path.exists() or path.read_bytes() != raw
    ]
    if check and stale:
        raise ValueError("Stale source receipts: " + ", ".join(stale))
    if not check:
        for name in stale:
            (data_dir / name).write_bytes(outputs[data_dir / name])
    return stale


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(sync(args.data_dir, args.check))


if __name__ == "__main__":
    main()
