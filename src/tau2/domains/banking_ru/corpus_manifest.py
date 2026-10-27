"""Compact banking input versions and document metadata derived from the corpus.

Legacy manifests remain readable for published historical fixtures. The compact
format stores only hashes; its file inventory is derived from the corpus root.
"""

import hashlib
import json
import re
from pathlib import Path

import yaml

COMPACT_KEYS = {"corpus_sha256", "db_sha256", "intake_answers_sha256"}
_DOCUMENT_OPTIONS = ("discoverable_workflows",)


def serialized(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _frontmatter(raw: bytes):
    lines = raw.decode("utf-8").splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    raise ValueError("Unclosed corpus document metadata")


def document_metadata(raw: bytes) -> dict:
    parts = _frontmatter(raw)
    if parts is None:
        raise ValueError("Corpus document must declare YAML metadata")
    metadata = yaml.safe_load(parts[0])
    if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
        raise ValueError("Corpus document metadata requires a string id")
    return json.loads(json.dumps(metadata, default=str))


def document_body(raw: bytes) -> str:
    parts = _frontmatter(raw)
    return parts[1].lstrip("\r\n") if parts else raw.decode("utf-8")


def with_metadata(raw: bytes, metadata: dict) -> bytes:
    """Keep original document bytes whenever its metadata already matches."""
    if _frontmatter(raw) is not None and document_metadata(raw) == metadata:
        return raw
    front = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip()
    return ("---\n" + front + "\n---\n\n" + document_body(raw)).encode()


def _overrides(values):
    return {Path(path).resolve(): raw for path, raw in (values or {}).items()}


def corpus_files(data_dir: Path, overrides=None) -> dict[str, bytes]:
    """Scan every regular corpus file; reject links and paths leaving the root."""
    root = Path(data_dir) / "corpus"
    if not root.is_dir():
        raise ValueError("Corpus directory is missing")
    if root.is_symlink():
        raise ValueError("Corpus symlinks are not allowed")
    root = root.resolve()
    files = {}
    replacement = _overrides(overrides)
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Corpus symlinks are not allowed")
        if path.is_file():
            raw = replacement.get(path.resolve(), path.read_bytes())
            if raw is not None:
                files["/" + path.relative_to(root).as_posix()] = raw
        elif not path.is_dir():
            raise ValueError(f"Unsupported corpus entry: {path}")
    for path, raw in replacement.items():
        if path.is_relative_to(root.resolve()) and raw is not None:
            files["/" + path.relative_to(root.resolve()).as_posix()] = raw
    for name, raw in files.items():
        if len(raw) > 65536:
            raise ValueError(f"Corpus file exceeds 65536 bytes: {name}")
        if len(raw.decode("utf-8").splitlines()) > 200:
            raise ValueError(f"Corpus file exceeds 200 lines: {name}")
    return dict(sorted(files.items()))


def corpus_hash(files: dict[str, bytes]) -> str:
    """Hash sorted UTF-8 paths and bytes with length framing, independent of mtimes."""
    digest = hashlib.sha256()
    for path, raw in sorted(files.items()):
        name = path.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _compact(value) -> bool:
    return isinstance(value, dict) and set(value) == COMPACT_KEYS


def load_manifest(data_dir: Path, verify: bool = False) -> dict:
    """Build an in-memory descriptor view; never store its document inventory."""
    data_dir = Path(data_dir)
    stored = json.loads((data_dir / "manifest.json").read_bytes())
    if not _compact(stored):
        if not isinstance(stored, dict) or "documents" not in stored:
            raise ValueError("Unsupported corpus manifest")
        return stored
    if any(
        not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value)
        for value in stored.values()
    ):
        raise ValueError("Invalid compact manifest hashes")
    files = corpus_files(data_dir)
    if verify:
        if corpus_hash(files) != stored["corpus_sha256"]:
            raise ValueError("Corpus hash mismatch")
        for filename, key in [
            ("db.json", "db_sha256"),
            ("intake_answers.json", "intake_answers_sha256"),
        ]:
            if (
                hashlib.sha256((data_dir / filename).read_bytes()).hexdigest()
                != stored[key]
            ):
                raise ValueError(f"Input hash mismatch: {filename}")
    expanded = {
        "version": 1,
        "documents": {
            path: {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "metadata": document_metadata(raw),
            }
            for path, raw in files.items()
        },
        "legacy_db_sha256": stored["db_sha256"],
        "intake_answers_sha256": stored["intake_answers_sha256"],
    }
    startup = expanded["documents"].get("/AGENTS.md", {}).get("metadata", {})
    expanded.update({key: startup[key] for key in _DOCUMENT_OPTIONS if key in startup})
    return expanded


def manifest_outputs(
    data_dir: Path, expanded: dict, overrides=None
) -> dict[Path, bytes]:
    """Project maintenance edits while preserving legacy/compact storage format.

    Overrides are prospective file contents. None removes a file from the hash;
    the calling maintenance command must perform that deletion when applying.
    """
    data_dir = Path(data_dir)
    path = data_dir / "manifest.json"
    stored = json.loads(path.read_bytes())
    outputs = {Path(p): raw for p, raw in (overrides or {}).items() if raw is not None}
    if not _compact(stored):
        outputs[path] = (
            path.read_bytes() if stored == expanded else serialized(expanded)
        )
        return outputs
    replacement = _overrides(overrides)
    files = corpus_files(data_dir, overrides)
    for virtual, descriptor in expanded["documents"].items():
        member = data_dir / "corpus" / virtual.lstrip("/")
        if (
            not virtual.startswith("/")
            or ".." in Path(virtual).parts
            or not member.resolve().is_relative_to((data_dir / "corpus").resolve())
        ):
            raise ValueError("Invalid corpus document path")
        if virtual not in files:
            if replacement.get(member.resolve(), b"") is None:
                continue
            raise ValueError(f"Corpus document is missing: {virtual}")
        metadata = dict(descriptor["metadata"])
        if virtual == "/AGENTS.md":
            for key in _DOCUMENT_OPTIONS:
                if key in expanded:
                    metadata[key] = expanded[key]
                else:
                    metadata.pop(key, None)
        raw = with_metadata(files[virtual], metadata)
        if raw != files[virtual]:
            outputs[member] = raw
            files[virtual] = raw

    def input_bytes(filename):
        member = data_dir / filename
        return replacement.get(member.resolve(), member.read_bytes())

    compact = {
        "corpus_sha256": corpus_hash(files),
        "db_sha256": hashlib.sha256(input_bytes("db.json")).hexdigest(),
        "intake_answers_sha256": hashlib.sha256(
            input_bytes("intake_answers.json")
        ).hexdigest(),
    }
    outputs[path] = path.read_bytes() if compact == stored else serialized(compact)
    return outputs
