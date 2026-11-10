"""Move Bank 2 API text fields into private storage without printing their text.

Existing public files are changed only after byte-exact hydration is verified.
Historical artifacts and Git history remain private inputs, not developer context.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from tau2.platform.runtime_payloads import PREFIX, TOKEN, PayloadStore, private_root

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/tau2/domains/banking_ru"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def migrate_bank2(store, receipt_path):
    scenario = DATA / "tasks/bank_002/scenario.json"
    parsed = json.loads(scenario.read_bytes())
    agent = parsed["initial_state"]["initialization_data"]["agent_data"]
    # Structural ownership, not probabilistic content detection. Move whole API
    # text fields so a mixed factual/adversarial paragraph cannot be missed.
    texts = {
        store.text(item[field])
        for section, field in (("documents", "body"), ("processing_notes", "text"))
        for item in agent.get(section, {}).values()
        if isinstance(item.get(field), str)
    }
    # The customer's pre-existing third-party message and connector archive are
    # shared environment resources used by this task, not task-specific tools.
    db = json.loads((DATA / "db.json").read_bytes())
    for document in db.get("documents", {}).values():
        if document.get("customer_id") == "gromov_a_1187" and isinstance(
            document.get("body"), str
        ):
            texts.add(store.text(document["body"]))
    archive = DATA / "corpus/knowledge/merchant_connector_archive.md"
    from tau2.domains.banking_ru.corpus_manifest import document_body

    texts.add(store.text(document_body(archive.read_bytes())))
    if not texts:
        raise ValueError("No unmigrated Bank 2 resource texts")
    substitutions = [
        (text, store.put(text)) for text in sorted(texts, key=len, reverse=True)
    ]
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    changes = {}
    unmatched_source_copies = []
    for name in tracked:
        path = ROOT / name
        if not path.is_file() or path.suffix not in {".json", ".md", ".py"}:
            continue
        # Other task files are explicitly outside this migration.
        if name.startswith(
            "data/tau2/domains/banking_ru/tasks/"
        ) and not path.is_relative_to(scenario.parent):
            continue
        raw = path.read_bytes()
        if path.suffix == ".py":
            if any(
                text.encode() in raw
                or json.dumps(text, ensure_ascii=False)[1:-1].encode() in raw
                for text, _ in substitutions
            ):
                unmatched_source_copies.append(name)
            continue
        result = raw
        for text, ref in substitutions:
            target = (
                json.dumps(text, ensure_ascii=False)[1:-1]
                if path.suffix == ".json"
                else text
            )
            result = result.replace(target.encode(), ref.encode())
        if result == raw:
            continue

        def replacement(match):
            text = store.get(match[1])
            return (
                json.dumps(text, ensure_ascii=False)[1:-1]
                if path.suffix == ".json"
                else text
            )

        restored = TOKEN.sub(replacement, result.decode()).encode()
        if restored != raw:
            raise ValueError("Migration round-trip mismatch")
        if path.suffix == ".json":
            json.loads(result)
        changes[path] = (raw, result)
    if unmatched_source_copies:
        raise ValueError(
            "Source literals need explicit migration: "
            + ",".join(unmatched_source_copies)
        )
    backup = store.root.parent / "migration-backups" / sha(scenario.read_bytes())
    if backup.exists():
        raise ValueError("Migration backup already exists; inspect before retry")
    backup.mkdir(parents=True, mode=0o700)
    files = []
    for path, (raw, result) in changes.items():
        name = path.relative_to(ROOT).as_posix()
        original = backup / name
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(raw)
        original.chmod(0o600)
        files.append(
            {"path": name, "original_sha256": sha(raw), "template_sha256": sha(result)}
        )
    receipt = {
        "version": 1,
        "task": "Bank 2",
        "payload_ids": [ref[len(PREFIX) : -2] for _, ref in substitutions],
        "files": files,
        "round_trip": "byte_exact",
        "historical_artifacts": "not_rewritten_private_only",
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    for path, (_, result) in changes.items():
        path.write_bytes(result)
    print(
        json.dumps(
            {
                "payloads": len(substitutions),
                "files": len(changes),
                "round_trip": "byte_exact",
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=private_root() / "payloads")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument(
        "--developer-export",
        type=Path,
        help="Export current tracked files without Git history or raw artifacts",
    )
    args = parser.parse_args()
    if args.store.resolve().is_relative_to(ROOT):
        parser.error("Private store must be outside the repository")
    store = PayloadStore(args.store)
    if args.developer_export:
        export_developer(args.developer_export, store)
    elif args.receipt:
        migrate_bank2(store, args.receipt)
    else:
        parser.error("Choose --receipt for migration or --developer-export")


def export_developer(destination, store):
    """Prepare a public snapshot to copy into a developer container, not a sandbox.

    No .git history, ignored raw artifacts, auth, private store or outside symlinks.
    This scanner checks known payloads, not arbitrary encodings or unknown attacks.
    """
    if destination.exists():
        raise ValueError("Developer export destination must not exist")
    names = (
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    files = [
        ROOT / name for name in names if name and not name.startswith("artifacts/")
    ]
    skipped_links = sum(path.is_symlink() for path in files)
    files = [path for path in files if not path.is_symlink()]
    ids = set()
    for path in files:
        if path.is_file() and path.suffix in {".json", ".md", ".py"}:
            ids.update(TOKEN.findall(path.read_text()))
    needles = []
    for key in ids:
        text = store.get(key)
        needles.extend(
            (
                text.encode(),
                text.strip().encode(),
                json.dumps(text, ensure_ascii=False)[1:-1].encode(),
                json.dumps(text.strip(), ensure_ascii=False)[1:-1].encode(),
            )
        )
    for path in files:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if any(needle in raw for needle in needles if len(needle) > 32):
            raise ValueError(
                "Known payload remains in developer source: "
                + path.relative_to(ROOT).as_posix()
            )
    destination.mkdir(parents=True, mode=0o700)
    for path in files:
        if path.is_file():
            target = destination / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    print(
        json.dumps(
            {
                "files": len(files),
                "payload_references_checked": len(ids),
                "git_history": False,
                "raw_artifacts": False,
                "symlinks_skipped": skipped_links,
                "sandbox": "not_started",
            }
        )
    )


if __name__ == "__main__":
    main()
