"""Read-only knowledge API over a frozen FileCorpus snapshot.

Target metadata is validated, never guessed from effective dates or filenames.
Legacy corpora can be searched with grep, but cannot fabricate dated receipts.
"""

import fnmatch
import hashlib
import json
import re
import shlex
import subprocess
from copy import deepcopy
from datetime import date
from pathlib import PurePosixPath

import mcp.types as t

from tau2.domains.banking_ru.file_corpus import FileCorpus
from tau2.platform.bank_directory import DirectoryError, _normal
from tau2.platform.catalog_pages import CatalogPages, CursorError

TOOL_NAMES = frozenset(
    {
        "list_documents",
        "search_documents",
        "list_revisions",
        "read_document",
        "corpus_command",
    }
)
CARD_FIELDS = (
    "document_id",
    "revision_id",
    "document_type",
    "title",
    "created_at",
    "updated_at",
)


class KnowledgeError(DirectoryError):
    def __init__(self, code, message, *, origin="request"):
        self.origin = origin
        super().__init__(code, message)


def knowledge_tools():
    def text(description):
        return {"type": "string", "description": description}

    cursor = {"type": ["string", "null"], "default": None}
    kind = {
        "type": ["string", "null"],
        "enum": ["policy", "knowledge", None],
        "default": None,
    }
    specs = [
        (
            "list_documents",
            "Просмотреть каталог знаний и политик.",
            {"document_type": kind, "cursor": cursor},
            [],
        ),
        (
            "search_documents",
            "Найти редакции по ID, названию и тексту.",
            {
                "query": text("Поисковый запрос"),
                "document_type": kind,
                "cursor": cursor,
            },
            ["query"],
        ),
        (
            "list_revisions",
            "Получить редакции документа по датам обновления.",
            {"document_id": text("ID документа"), "cursor": cursor},
            ["document_id"],
        ),
        (
            "read_document",
            "Прочитать полный текст указанной редакции.",
            {"document_id": text("ID документа"), "revision_id": text("ID редакции")},
            ["document_id", "revision_id"],
        ),
        (
            "corpus_command",
            "Исследовать корпус командами ls/find/grep/cat; grep возвращает только совпадения.",
            {
                "command": text("Одна команда над виртуальным корпусом"),
                "cursor": cursor,
            },
            ["command"],
        ),
    ]
    return [
        t.Tool(
            name=name,
            description=description,
            inputSchema={
                "type": "object",
                "properties": deepcopy(props),
                "required": required,
                "additionalProperties": False,
            },
        )
        for name, description, props, required in specs
    ]


class KnowledgeMCP:
    GREP_PAGE_SIZE = 50
    GREP_LINE_LIMIT = 800

    def __init__(self, corpus, *, token, cursor_secret=None):
        self._files = {}
        for path, source in corpus.documents.items():
            if not path.startswith(("/knowledge/", "/policies/")):
                continue
            self._path(path)
            doc = deepcopy(source)
            digest = hashlib.sha256(doc["content"].encode("utf-8")).hexdigest()
            if digest != doc["sha256"]:
                raise KnowledgeError(
                    "CORPUS_INTEGRITY_ERROR",
                    f"Хеш не совпадает: {path}",
                    origin="corpus",
                )
            self._files[path] = doc
        self._files = dict(sorted(self._files.items()))
        signature = hashlib.sha256(
            json.dumps(
                [(p, d["sha256"], d["metadata"]) for p, d in self._files.items()],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self._pages = CatalogPages([token, signature], secret=cursor_secret)
        self._cards = None

    @staticmethod
    def _path(path):
        try:
            FileCorpus.validate_path(path)
        except (TypeError, ValueError):
            raise KnowledgeError(
                "INVALID_PATH", "Используйте абсолютный виртуальный путь корпуса"
            ) from None
        if (
            path != "/"
            and path not in {"/knowledge", "/policies"}
            and not path.startswith(("/knowledge/", "/policies/"))
        ):
            raise KnowledgeError("INVALID_PATH", "Путь вне опубликованного корпуса")

    def _paths(self, root):
        self._path(root)
        found = [
            p for p in self._files if p == root or p.startswith(root.rstrip("/") + "/")
        ]
        if not found and root not in {"/", "/knowledge", "/policies"}:
            raise KnowledgeError("UNKNOWN_DOCUMENT", "Путь не найден в корпусе")
        return found

    def _index(self):
        if self._cards is not None:
            return self._cards
        cards, pairs, groups = {}, set(), {}
        for path, doc in self._files.items():
            meta = doc["metadata"]
            missing = [k for k in CARD_FIELDS if k not in meta]
            if missing:
                raise KnowledgeError(
                    "CORPUS_METADATA_INCOMPLETE",
                    f"{path}: отсутствуют {', '.join(missing)}; нужна миграция метаданных",
                    origin="corpus",
                )
            if any(
                not isinstance(meta[k], str) or not meta[k].strip() for k in CARD_FIELDS
            ):
                raise KnowledgeError(
                    "CORPUS_METADATA_INVALID",
                    f"{path}: метаданные должны быть непустыми строками",
                    origin="corpus",
                )
            try:
                for key in ("created_at", "updated_at"):
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta[key]):
                        raise ValueError()
                    date.fromisoformat(meta[key])
                if meta["updated_at"] < meta["created_at"]:
                    raise ValueError()
            except ValueError:
                raise KnowledgeError(
                    "CORPUS_METADATA_INVALID",
                    f"{path}: некорректные даты создания/обновления",
                    origin="corpus",
                ) from None
            expected_type = "policy" if path.startswith("/policies/") else "knowledge"
            if meta["document_type"] != expected_type:
                raise KnowledgeError(
                    "CORPUS_METADATA_INVALID",
                    f"{path}: document_type не соответствует разделу",
                    origin="corpus",
                )
            pair = meta["document_id"], meta["revision_id"]
            group = groups.setdefault(
                meta["document_id"],
                {
                    "created": meta["created_at"],
                    "type": meta["document_type"],
                    "dates": set(),
                },
            )
            if (
                pair in pairs
                or meta["updated_at"] in group["dates"]
                or group["created"] != meta["created_at"]
                or group["type"] != meta["document_type"]
            ):
                raise KnowledgeError(
                    "CORPUS_METADATA_INVALID",
                    f"{path}: неоднозначные редакции документа",
                    origin="corpus",
                )
            pairs.add(pair)
            group["dates"].add(meta["updated_at"])
            cards[path] = {**{k: meta[k] for k in CARD_FIELDS}, "path": path}
        self._cards = cards
        return cards

    def _page(self, items, context, cursor, *, size=5, key="items"):
        try:
            return self._pages.page(items, context, cursor, size=size, key=key)
        except CursorError as error:
            raise KnowledgeError("INVALID_CURSOR", str(error)) from None

    @staticmethod
    def _filter(document_type):
        if document_type is not None and (
            not isinstance(document_type, str)
            or document_type not in {"policy", "knowledge"}
        ):
            raise KnowledgeError(
                "INVALID_ARGUMENT", "document_type: policy или knowledge"
            )

    def _ordered(self):
        cards = list(self._index().values())
        cards.sort(key=lambda row: row["updated_at"], reverse=True)
        cards.sort(key=lambda row: row["document_id"])
        return cards

    def list_documents(self, document_type=None, cursor=None):
        self._filter(document_type)
        rows = [
            row
            for row in self._ordered()
            if document_type is None or row["document_type"] == document_type
        ]
        return self._page(rows, ["list", document_type], cursor)

    def search_documents(self, query, document_type=None, cursor=None):
        self._filter(document_type)
        if not isinstance(query, str) or not query.strip():
            raise KnowledgeError("INVALID_ARGUMENT", "Пустой поисковый запрос")
        words = _normal(query).split()
        rows = []
        for row in self._ordered():
            text = _normal(
                row["document_id"]
                + " "
                + row["revision_id"]
                + " "
                + row["title"]
                + " "
                + self._files[row["path"]]["content"]
            )
            if (document_type is None or row["document_type"] == document_type) and all(
                word in text for word in words
            ):
                rows.append(row)
        return self._page(rows, ["search", _normal(query), document_type], cursor)

    def list_revisions(self, document_id, cursor=None):
        rows = [row for row in self._ordered() if row["document_id"] == document_id]
        if not rows:
            raise KnowledgeError("UNKNOWN_DOCUMENT", "Документ не найден")
        return self._page(rows, ["revisions", document_id], cursor)

    def read_document(self, document_id, revision_id):
        row = next(
            (
                row
                for row in self._index().values()
                if row["document_id"] == document_id
                and row["revision_id"] == revision_id
            ),
            None,
        )
        if row is None:
            raise KnowledgeError(
                "UNKNOWN_DOCUMENT", "Пара документа и редакции не найдена"
            )
        return self._read(row["path"])

    def _read(self, path):
        self._path(path)
        if path not in self._files:
            raise KnowledgeError("UNKNOWN_DOCUMENT", "Документ не найден")
        row = self._index()[path]
        doc = self._files[path]
        return deepcopy(
            {**row, "content": doc["content"], "content_sha256": doc["sha256"]}
        )

    @staticmethod
    def command_kind(command):
        if not isinstance(command, str):
            return "discovery"
        try:
            words = shlex.split(command)
        except ValueError:
            return "discovery"
        return "banking" if words and words[0] == "cat" else "discovery"

    @classmethod
    def is_discovery(cls, name, arguments):
        return name in {"list_documents", "search_documents", "list_revisions"} or (
            name == "corpus_command"
            and cls.command_kind(arguments.get("command")) == "discovery"
        )

    def corpus_command(self, command, cursor=None):
        try:
            words = shlex.split(command)
        except (ValueError, TypeError):
            raise KnowledgeError("INVALID_COMMAND", "Некорректная команда") from None
        if not words:
            raise KnowledgeError("INVALID_COMMAND", "Пустая команда")
        verb, *args = words
        if verb == "cat" and len(args) == 1 and cursor is None:
            return self._read(args[0])
        if verb == "ls" and len(args) <= 1:
            root = args[0] if args else "/"
            paths = self._paths(root)
            if root in self._files:
                rows = [self._index()[root]]
            else:
                children = {}
                for path in paths:
                    tail = path[len(root.rstrip("/")) + 1 :]
                    child = root.rstrip("/") + "/" + tail.split("/", 1)[0]
                    children[child] = None if "/" in tail else path
                rows = [
                    {"entry_type": "directory", "path": child}
                    if path is None
                    else self._index()[path]
                    for child, path in sorted(children.items())
                ]
            return self._page(rows, ["ls", command], cursor)
        if verb == "find" and len(args) == 3 and args[1] == "-name":
            root, _, pattern = args
            rows = [
                self._index()[path]
                for path in self._paths(root)
                if fnmatch.fnmatchcase(PurePosixPath(path).name, pattern)
            ]
            return self._page(rows, ["find", command], cursor)
        if verb == "grep":
            flags = []
            while args and args[0].startswith("-") and args[0] != "--":
                flag = args.pop(0)
                if flag not in {"-n", "-i", "-E"}:
                    raise KnowledgeError("INVALID_COMMAND", "Неизвестный флаг grep")
                if flag == "-i":
                    flags.append("--ignore-case")
            if args and args[0] == "--":
                args.pop(0)
            if len(args) not in (1, 2) or not args[0] or len(args[0]) > 200:
                raise KnowledgeError(
                    "INVALID_COMMAND", "Нужны шаблон grep и необязательный корень"
                )
            pattern, root = args[0], args[1] if len(args) == 2 else "/"
            if "\x00" in pattern:
                raise KnowledgeError("INVALID_REGEX", "Шаблон не может содержать NUL")
            rows = [
                (path, n, line)
                for path in self._paths(root)
                for n, line in enumerate(self._files[path]["content"].splitlines(), 1)
            ]
            try:
                found = subprocess.run(
                    [
                        "rg",
                        "--no-config",
                        "--text",
                        "--color=never",
                        "--line-number",
                        *flags,
                        "--",
                        pattern,
                    ],
                    input="\n".join(row[2] for row in rows) + ("\n" if rows else ""),
                    text=True,
                    capture_output=True,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise KnowledgeError(
                    "CORPUS_SEARCH_UNAVAILABLE",
                    "Поиск недоступен или превысил лимит времени",
                    origin="infrastructure",
                ) from None
            if found.returncode not in (0, 1):
                raise KnowledgeError(
                    "INVALID_REGEX",
                    "Некорректное или неподдерживаемое регулярное выражение",
                )
            matches = []
            for hit in found.stdout.splitlines():
                i = int(hit.split(":", 1)[0]) - 1
                path, number, text = rows[i]
                matches.append(
                    {
                        "path": path,
                        "line": number,
                        "text": text[: self.GREP_LINE_LIMIT],
                        "line_truncated": len(text) > self.GREP_LINE_LIMIT,
                    }
                )
            return self._page(
                matches,
                ["grep", command],
                cursor,
                size=self.GREP_PAGE_SIZE,
                key="matches",
            )
        raise KnowledgeError(
            "INVALID_COMMAND", "Поддерживаются одиночные ls/find/grep/cat"
        )

    def call(self, name, arguments):
        if name not in TOOL_NAMES:
            raise KnowledgeError("UNKNOWN_TOOL", "Неизвестный инструмент knowledge")
        return getattr(self, name)(**arguments)
