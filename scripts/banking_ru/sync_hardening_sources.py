"""Refresh source metadata and existing receipts without changing task semantics."""

import argparse
import hashlib
import json
from pathlib import Path

import yaml


def serialized(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def sync(data_dir, check=False):
    """Synchronize only supplied files, computing all outputs before writing."""
    manifest = json.loads((data_dir / "manifest.json").read_text())
    db = json.loads((data_dir / "db.json").read_text())
    tasks = json.loads((data_dir / "tasks.json").read_text())
    corpus = {}
    for path, descriptor in manifest["documents"].items():
        raw = (data_dir / "corpus" / path.lstrip("/")).read_bytes()
        if len(raw) > 65536 or len(raw.decode().splitlines()) > 200:
            raise ValueError("Unbounded source: " + path)
        content = raw.decode()
        corpus[path] = content
        descriptor["sha256"] = hashlib.sha256(raw).hexdigest()
        body = content
        if content.startswith("---\n"):
            _, front, body = content.split("---", 2)
            descriptor["metadata"] = json.loads(
                json.dumps(yaml.safe_load(front), default=str)
            )
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
    manifest_raw = serialized(manifest)
    snapshot = hashlib.sha256(manifest_raw).hexdigest()
    for task in tasks:
        for evidence in task["evaluation_criteria"].get("result_evidence", []):
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
        "db.json": db_raw,
        "manifest.json": manifest_raw,
        "tasks.json": serialized(tasks),
    }
    stale = [
        name for name, raw in outputs.items() if (data_dir / name).read_bytes() != raw
    ]
    if check and stale:
        raise ValueError("Stale source receipts: " + ", ".join(stale))
    if not check:
        for name in stale:
            (data_dir / name).write_bytes(outputs[name])
    return stale


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(sync(args.data_dir, args.check))


if __name__ == "__main__":
    main()
