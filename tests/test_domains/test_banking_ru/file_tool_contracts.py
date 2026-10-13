"""Real contracts for the shared immutable file API."""

import hashlib

from tests.test_domains.test_banking_ru.read_tool_contracts import _read
from tests.test_domains.test_banking_ru.tool_contract_support import reject


def list_files(env):
    result = _read(env, "file_list", path="/")
    paths = {row["path"] for row in result["entries"]}
    assert {"/AGENTS.md", "/knowledge", "/policies"} <= paths


def find_file(env):
    result = _read(env, "file_find", name="identity.md", root="/policies")
    assert result == {"paths": ["/policies/identity.md"], "truncated": False}


def search_file(env):
    result = _read(
        env, "file_search", pattern="идентификац", root="/policies/identity.md"
    )
    assert result["matches"]
    assert all(row["path"] == "/policies/identity.md" for row in result["matches"])
    assert all(row["line"] > 0 for row in result["matches"])


def read_file(env):
    result = _read(env, "file_read", path="/policies/identity.md")
    assert result["complete"] is True
    assert result["path"] == "/policies/identity.md"
    assert hashlib.sha256(result["content"].encode()).hexdigest() == result["sha256"]
    assert result["total_lines"] == len(result["content"].splitlines())


def list_error(env):
    reject(env, "file_list", "file_list requires a directory", path="/AGENTS.md")


def find_error(env):
    reject(env, "file_find", "Use a name", name="")


def search_error(env):
    reject(env, "file_search", "invalid ripgrep expression", pattern="[")


def read_error(env):
    reject(env, "file_read", "canonical absolute corpus path", path="/../audit.md")


SUCCESS_SCENARIOS = {
    "file_list": list_files,
    "file_find": find_file,
    "file_search": search_file,
    "file_read": read_file,
}
ERROR_SCENARIOS = {
    "file_list": list_error,
    "file_find": find_error,
    "file_search": search_error,
    "file_read": read_error,
}
