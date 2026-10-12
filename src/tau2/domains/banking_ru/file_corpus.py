"""Immutable, allowlisted Markdown snapshot with grep-like read-only RPC."""

import fnmatch
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path, PurePosixPath


class FileCorpus:
    """Load and verify every file once; requests never read the host filesystem."""

    def __init__(self, root: Path):
        raw = (root / "manifest.json").read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.manifest = json.loads(raw)
        if self.manifest["version"] != 1:
            raise ValueError("Unsupported corpus manifest version")
        self.documents = {}
        self._read_lines: dict[str, set[int]] = {}
        for path, descriptor in self.manifest["documents"].items():
            self.validate_path(path)
            source = root / "corpus" / path.lstrip("/")
            if any(p.is_symlink() for p in (source, *source.parents)):
                raise ValueError("Corpus symlinks are not allowed")
            content = source.read_bytes()
            if len(content) > 65536:
                raise ValueError("Corpus file exceeds 65536 bytes")
            if hashlib.sha256(content).hexdigest() != descriptor["sha256"]:
                raise ValueError(f"Corpus hash mismatch: {path}")
            self.documents[path] = {
                **deepcopy(descriptor),
                "content": content.decode("utf-8"),
            }

    @staticmethod
    def validate_path(path: str) -> None:
        """Require canonical absolute virtual paths, never host paths."""
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or str(PurePosixPath(path)) != path
            or ".." in PurePosixPath(path).parts
        ):
            raise ValueError("Use a canonical absolute corpus path")

    def paths(self, root: str) -> list[str]:
        """Return files under an existing virtual file or directory."""
        self.validate_path(root)
        paths = sorted(
            p
            for p in self.documents
            if p == root or p.startswith(root.rstrip("/") + "/")
        )
        if not paths:
            raise ValueError("Corpus path not found")
        return paths

    def read(self, path: str, start_line: int = 0, end_line: int = 0) -> dict:
        """Return bounded text with exact snapshot, revision and completeness."""
        self.validate_path(path)
        if path not in self.documents:
            raise ValueError("Corpus file not found")
        doc = self.documents[path]
        lines = doc["content"].splitlines(keepends=True)
        start = start_line or 1
        end = end_line or len(lines)
        if not 1 <= start <= end <= len(lines):
            raise ValueError("Invalid inclusive line range")
        covered = self._read_lines.setdefault(path, set())
        covered.update(range(start, end + 1))
        complete = len(covered) == len(lines)
        requested_start, requested_end = start, end
        if complete:
            # Re-emit the entire text, so the receipt proves what was returned.
            start, end = 1, len(lines)
        receipt = {
            "requested_start_line": requested_start,
            "requested_end_line": requested_end,
            "id": doc["metadata"]["id"],
            "path": path,
            "sha256": doc["sha256"],
            "snapshot_sha256": self.sha256,
            "metadata": deepcopy(doc["metadata"]),
            "start_line": start,
            "end_line": end,
            "total_lines": len(lines),
            "complete": complete,
            "content": "".join(lines[start - 1 : end]),
        }
        # A complete consolidated read may satisfy individual source obligations.
        # Re-emit the actual verified source text, never synthesize a receipt from
        # a title or metadata alone. Incomplete aggregates emit no source bodies.
        included = []
        if complete:
            for source_path in doc["metadata"].get("source_paths", []):
                source = self.documents.get(source_path)
                if source is None:
                    continue
                body = source["content"]
                if body.startswith("---\n"):
                    body = body.split("---", 2)[-1].strip()
                revision = source["metadata"].get("revision_id")
                if (
                    not body
                    or body not in doc["content"]
                    or doc["metadata"]
                    .get("source_revisions", {})
                    .get(source["metadata"]["id"])
                    != revision
                ):
                    continue
                included.append(
                    {
                        "id": source["metadata"]["id"],
                        "path": source_path,
                        "sha256": source["sha256"],
                        "complete": True,
                        "content": source["content"],
                    }
                )
        if included:
            receipt["included_documents"] = included
        equivalents = []
        for aggregate_path, aggregate in self.documents.items():
            sources = aggregate["metadata"].get("source_paths", [])
            if (
                aggregate_path == path
                or not sources
                or path not in sources
                or not all(
                    source in self.documents
                    and len(self._read_lines.get(source, set()))
                    == len(self.documents[source]["content"].splitlines())
                    for source in sources
                )
            ):
                continue
            # Return the actual consolidated text as part of this tool result.
            # Do not mark it read or recurse: only actual source reads count.
            total = len(aggregate["content"].splitlines())
            equivalents.append(
                {
                    "requested_start_line": 1,
                    "requested_end_line": total,
                    "id": aggregate["metadata"]["id"],
                    "path": aggregate_path,
                    "sha256": aggregate["sha256"],
                    "snapshot_sha256": self.sha256,
                    "metadata": deepcopy(aggregate["metadata"]),
                    "start_line": 1,
                    "end_line": total,
                    "total_lines": total,
                    "complete": True,
                    "content": aggregate["content"],
                    "source_receipts": [
                        {
                            "path": source,
                            "sha256": self.documents[source]["sha256"],
                            "complete": True,
                        }
                        for source in sources
                    ],
                }
            )
        if equivalents:
            receipt["equivalent_documents"] = equivalents
        return receipt

    def list(self, path: str) -> dict:
        """List immediate children without exposing full documents."""
        paths = self.paths(path)
        if path in self.documents:
            raise ValueError("file_list requires a directory")
        prefix = path.rstrip("/") + "/"
        children = {}
        for p in paths:
            name = p[len(prefix) :].split("/")[0]
            child = prefix + name
            children[child] = {"path": child, "is_dir": child not in self.documents}
        return {"entries": [children[p] for p in sorted(children)]}

    def find(self, name: str, root: str = "/", limit: int = 20) -> dict:
        """Match a substring or glob in filenames, with an explicit result cap."""
        if not name or len(name) > 200 or not 1 <= limit <= 200:
            raise ValueError("Use a name of 1..200 characters and limit 1..200")
        found = [
            p
            for p in self.paths(root)
            if fnmatch.fnmatchcase(PurePosixPath(p).name, "*" + name + "*")
        ]
        return {"paths": found[:limit], "truncated": len(found) > limit}

    def search(self, pattern: str, root: str = "/", limit: int = 20) -> dict:
        """Search snapshot text with ripgrep's bounded regex engine, via stdin."""
        if not pattern or len(pattern) > 200 or not 1 <= limit <= 200:
            raise ValueError("Use a pattern of 1..200 characters and limit 1..200")
        rows = []
        for path in self.paths(root):
            rows.extend(
                (path, n, line)
                for n, line in enumerate(
                    self.documents[path]["content"].splitlines(), 1
                )
            )
        try:
            result = subprocess.run(
                [
                    "rg",
                    "--no-config",
                    "--color=never",
                    "--line-number",
                    "--ignore-case",
                    "--max-count",
                    str(limit + 1),
                    "--",
                    pattern,
                ],
                input="\n".join(row[2] for row in rows) + "\n",
                text=True,
                capture_output=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(
                "File search requires ripgrep and a query under 2 seconds"
            ) from exc
        if result.returncode not in (0, 1):
            raise ValueError("Unsupported or invalid ripgrep expression")
        hits = []
        for line in result.stdout.splitlines():
            index = int(line.split(":", 1)[0]) - 1
            path, number, text = rows[index]
            hits.append(
                {
                    "path": path,
                    "line": number,
                    "text": text[:800],
                    "line_truncated": len(text) > 800,
                }
            )
        return {"matches": hits[:limit], "truncated": len(hits) > limit}
