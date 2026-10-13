"""Auditor solution documents must be unreachable in Bank32's allowed corpus."""

import json
from pathlib import Path

from scripts.banking_ru.run_codex_harness import build_prompt
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.file_corpus import FileCorpus
from tau2.platform.session import PlatformSession


def test_bank32_solution_is_excluded_from_visible_context_and_corpus():
    task = next(t for t in get_tasks() if t.id == "Bank 32")
    session = PlatformSession(task, get_environment, mcp_layout="systems")
    data = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
    corpus = FileCorpus(data)
    visible = build_prompt(policy=session.policy, ticket=task.ticket)
    visible += json.dumps([t.model_dump(mode="json") for t in session.mcp_tools])
    visible += json.dumps(corpus.documents, ensure_ascii=False)
    assert "Solution Contract" not in visible
    assert "solution-contracts" not in visible
    assert "Solution Contract для проверки траектории" not in visible
    assert not session.call(
        "file_find", {"name": "Solution", "root": "/"}, system="knowledge"
    ).is_error
    assert corpus.find("Solution")["paths"] == []
    assert corpus.search("Solution Contract|solution-contracts")["matches"] == []
    paths = [
        "/Solution Contract.md",
        "/artifacts/solution-contracts/Bank-32/Solution Contract.md",
        "/Users/maksimpiskaev/my_wiki/projects/laboratory/evaluation-platform/artifacts/solution-contracts/Bank-32/Solution Contract.md",
        "/../../artifacts/solution-contracts/Bank-32/Solution Contract.md",
    ]
    for path in paths:
        assert session.call("file_read", {"path": path}, system="knowledge").is_error
