"""Pin Bank 2 dependencies and reject silent changes; never calls a model."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASES = Path("releases/banking_ru/bank_002")
SCOPES = [
    "data/tau2/domains/banking_ru/corpus",
    "data/tau2/domains/banking_ru/tasks/bank_002",
    "data/tau2/domains/banking_ru/db.json",
    "data/tau2/domains/banking_ru/intake_answers.json",
    "data/tau2/domains/banking_ru/manifest.json",
    "data/tau2/domains/banking_ru/tasks.json",
    "src/tau2",
    "integrations/eval_platform",
    "pyproject.toml",
    "uv.lock",
    "docs/banking-ru.md",
    "docs/banking-ru/evaluation.md",
    "docs/banking-ru/task-format.md",
    "docs/banking-ru/knowledge.md",
    "docs/banking-ru/discovery.md",
    "scripts/banking_ru/codex_broker.py",
    "scripts/banking_ru/codex_container.py",
    "scripts/banking_ru/banking_preflight.py",
    "scripts/banking_ru/run_codex_harness.py",
    "scripts/banking_ru/build_platform_bundle.py",
    "scripts/banking_ru/Dockerfile.workbench-v1",
    "scripts/banking_ru/isolated-codex",
]


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def snapshot(root):
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    result = {}
    for name in sorted(set(names.decode().split("\0")) - {""}):
        if not any(name == scope or name.startswith(scope + "/") for scope in SCOPES):
            continue
        path = root / name
        if path.is_symlink():
            raise ValueError(f"Symlink is not a pinned dependency: {name}")
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def check_history(root, base):
    """Existing release/result blobs must survive unchanged, including renames."""
    names = git(root, "ls-tree", "-r", "--name-only", base).decode().splitlines()
    for name in names:
        immutable = (
            name.startswith(str(RELEASES) + "/")
            and name.endswith(".json")
            and not name.endswith("/current.json")
        ) or name.startswith("docs/banking-ru/runs/")
        if immutable:
            old = git(root, "show", f"{base}:{name}")
            path = root / name
            if not path.is_file() or path.read_bytes() != old:
                raise ValueError(f"Immutable evidence changed: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create", help="Create a new version, e.g. v43; never overwrite"
    )
    parser.add_argument(
        "--base", help="Also check immutable evidence against this Git ref"
    )
    args = parser.parse_args()
    folder = ROOT / RELEASES
    current = folder / "current.json"
    if args.base:
        check_history(ROOT, args.base)
    actual = snapshot(ROOT)
    if args.create:
        if not re.fullmatch(r"v[1-9][0-9]*", args.create):
            raise ValueError("Version must be v followed by a positive integer")
        folder.mkdir(parents=True, exist_ok=True)
        if current.exists():
            previous = json.loads(current.read_text())["version"]
            if int(args.create[1:]) <= int(previous[1:]):
                raise ValueError("New version must increase")
        manifest = {
            "task_id": "bank_002",
            "version": args.create,
            "scopes": SCOPES,
            "files": actual,
        }
        with (folder / f"{args.create}.json").open("x") as stream:
            stream.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        current.write_text(json.dumps({"version": args.create}, indent=2) + "\n")
    pinned = json.loads(current.read_text())["version"]
    if not re.fullmatch(r"v[1-9][0-9]*", pinned):
        raise ValueError("Invalid active version")
    expected = json.loads((folder / f"{pinned}.json").read_text())
    if expected["scopes"] != SCOPES:
        raise ValueError("Dependency scope changed; publish a new version")
    changed = [
        name
        for name in sorted(set(actual) | set(expected["files"]))
        if actual.get(name) != expected["files"].get(name)
    ]
    if changed:
        raise ValueError(
            "Frozen Bank 2 changed; create a new version:\n" + "\n".join(changed)
        )
    print(f"Bank 2 {pinned}: {len(actual)} dependency hashes match")


if __name__ == "__main__":
    main()
