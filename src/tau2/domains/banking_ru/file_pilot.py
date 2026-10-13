"""Shared banking toolkit/environment with immutable file knowledge access."""

import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Optional

from pydantic import RootModel

from tau2.domains.banking_ru.data_model import BankingDB
from tau2.domains.banking_ru.file_corpus import FileCorpus
from tau2.domains.banking_ru.tools import BankingTools
from tau2.domains.banking_ru.utils import BANKING_RU_DB_PATH
from tau2.environment.environment import Environment
from tau2.environment.toolkit import ToolType, is_tool


class FileResponse(RootModel[dict]):
    """Preserve JSON numbers/bools through Environment.to_json_str."""


class FileBankingTools(BankingTools):
    """Unchanged banking operations with a separate read-only corpus."""

    def __init__(self, db: BankingDB, corpus: FileCorpus):
        super().__init__(db)
        self.corpus = corpus
        self.excluded_tools: frozenset[str] = frozenset()

    @property
    def tools(self):
        """Remove both discovery and invocation of legacy knowledge APIs."""
        return {
            k: v
            for k, v in super().tools.items()
            if k not in {"search_knowledge", "get_article"} | self.excluded_tools
        }

    @is_tool(ToolType.READ)
    def file_list(self, path: str = "/") -> FileResponse:
        """List immediate children of a corpus directory.

        Args:
            path: Canonical absolute virtual directory, such as /knowledge.
        """
        return FileResponse(self.corpus.list(path))

    @is_tool(ToolType.READ)
    def file_find(self, name: str, root: str = "/", limit: int = 20) -> FileResponse:
        """Find files by filename substring or glob.

        Args:
            name: Filename fragment or glob, not a content query.
            root: Canonical absolute corpus directory.
            limit: Maximum paths, 1..200.
        """
        return FileResponse(self.corpus.find(name, root, limit))

    @is_tool(ToolType.READ)
    def file_search(
        self, pattern: str, root: str = "/", limit: int = 20
    ) -> FileResponse:
        """Search files with case-insensitive ripgrep regex; return path:line:text.

        Args:
            pattern: Ripgrep expression, 1..200 characters; search is not semantic.
            root: Canonical absolute corpus directory or file.
            limit: Maximum matching lines, 1..200. Truncation is explicit.
        """
        return FileResponse(self.corpus.search(pattern, root, limit))

    @is_tool(ToolType.READ)
    def file_read(self, path: str) -> FileResponse:
        """Read an entire corpus file with revision, SHA256 and completeness.

        Args:
            path: Canonical absolute virtual file path obtained from the corpus.
        """
        return FileResponse(self.corpus.read(path))

    def assert_file_is_reachable(self, path: str, pattern: str) -> bool:
        """Private assertion: the required document is available via file search."""
        return any(
            hit["path"] == path
            for hit in self.corpus.search(pattern, root=path, limit=200)["matches"]
        )

    def assert_bank_1_reply(self, customer_id: str) -> bool:
        """Check Bank 1's reply, including bounded categorical refusal examples.

        This finite grammar is not a universal judge of natural language.
        The reference profile registers a claim through the anti-fraud route;
        disclosing a code does not itself justify refusing its acceptance.
        """
        if not super().assert_bank_1_reply(customer_id):
            return False
        text = self.db.ticket_replies[customer_id].lower().replace("ё", "е")
        for sentence in re.split(r"[.!?;\n]", text):
            if "код" in sentence and re.search(r"заявлен|несоглас|оспари", sentence):
                if re.search(
                    r"принять\s+нельзя|нельзя\s+принять|не\s+принима\w*|не\s+мож\w*\s+принять",
                    sentence,
                ):
                    return False
        return True


def get_file_environment(
    data_dir: Path,
    domain_name: str,
    db: Optional[BankingDB] = None,
    solo_mode: bool = True,
    snapshot: Optional[tuple] = None,
    *,
    allow_non_solo: bool = False,
    structured_identity: bool = False,
) -> Environment:
    """Build a named file variant with its own immutable source manifest."""
    if not solo_mode and not allow_non_solo:
        raise ValueError(f"{domain_name} supports solo mode only")
    if snapshot is None:
        corpus = FileCorpus(data_dir)
        db_bytes = BANKING_RU_DB_PATH.read_bytes()
        if hashlib.sha256(db_bytes).hexdigest() != corpus.manifest["legacy_db_sha256"]:
            raise ValueError(
                "Pilot initial DB changed; explicitly refresh the pilot snapshot"
            )
        initial_db = (
            BankingDB.model_validate_json(db_bytes) if db is None else deepcopy(db)
        )
        initial_db.articles = {}
    else:
        corpus, initial_db = deepcopy(snapshot)
        if db is not None:
            raise ValueError("Cannot override a frozen snapshot DB")
    db_hash = hashlib.sha256(initial_db.model_dump_json().encode()).hexdigest()
    initial_snapshot = (deepcopy(corpus), deepcopy(initial_db))
    db = deepcopy(initial_db)
    toolkit = FileBankingTools
    if structured_identity:
        from tau2.domains.banking_ru.client_requests import (
            StructuredIdentityBankingTools,
        )

        toolkit = StructuredIdentityBankingTools
    env = Environment(
        domain_name=domain_name,
        policy=corpus.read("/AGENTS.md")["content"],
        tools=toolkit(db, corpus),
        solo_mode=solo_mode,
    )
    env.evaluation_env_kwargs = {"snapshot": initial_snapshot}
    env.run_metadata = {
        "knowledge_access": "files-rpc",
        "startup": {
            "path": "/AGENTS.md",
            "content": env.policy,
            "sha256": corpus.documents["/AGENTS.md"]["sha256"],
        },
        "corpus": {
            "sha256": corpus.sha256,
            "documents": deepcopy(corpus.manifest["documents"]),
        },
        "initial_db_sha256": db_hash,
    }
    return env
