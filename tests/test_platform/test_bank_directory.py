"""Offline contract controls for paged discovery and guarded dispatch."""

import json
from copy import deepcopy

import mcp.types as t
import pytest

from tau2.platform.bank_directory import BankDirectory, DirectoryError


def catalog():
    return [
        t.Tool(
            name=f"tool_{i:02d}",
            description="Поиск клиента.\n\nReturns: hidden",
            inputSchema={
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
            },
        )
        for i in range(12)
    ]


def directory(token="session-a"):
    return BankDirectory({"crm": catalog()}, token=token)


def test_system_pages_and_short_cards():
    d = directory()
    page = d.handle({"operation": "list_systems"})
    assert len(page["items"]) == 5
    assert set(page["items"][0]) == {"system_id", "name", "description"}
    pages = [page]
    while pages[-1]["next_cursor"]:
        pages.append(
            d.handle({"operation": "list_systems", "cursor": pages[-1]["next_cursor"]})
        )
    assert [len(p["items"]) for p in pages] == [5, 5, 3]
    assert len({x["system_id"] for p in pages for x in p["items"]}) == 13


def test_tool_pages_and_global_search():
    d = directory()
    page = d.handle({"operation": "list_tools", "system_id": "crm"})
    assert len(page["items"]) == 5
    assert set(page["items"][0]) == {"system_id", "tool_name", "description"}
    assert "Returns" not in json.dumps(page)
    assert (
        d.handle({"operation": "search_tools", "query": " ПОИСК   КЛИЕНТА "})["items"]
        == page["items"]
    )
    assert d.handle({"operation": "search_tools", "query": "nothing"}) == {
        "items": [],
        "next_cursor": None,
    }
    assert (
        d.handle({"operation": "search_systems", "query": "КЛИЕНТСКИЙ СПРАВОЧНИК"})[
            "items"
        ][0]["system_id"]
        == "crm"
    )


def test_cursors_are_bound_to_query_session_and_catalog():
    d = directory()
    cursor = d.handle({"operation": "list_tools", "system_id": "crm"})["next_cursor"]
    for other, args in [
        (d, {"operation": "list_systems"}),
        (d, {"operation": "search_tools", "query": "tool"}),
        (directory("session-b"), {"operation": "list_tools", "system_id": "crm"}),
        (
            BankDirectory({"crm": catalog()[:6]}, token="session-a"),
            {"operation": "list_tools", "system_id": "crm"},
        ),
    ]:
        with pytest.raises(DirectoryError, match="CURSOR"):
            other.handle({**args, "cursor": cursor})
    with pytest.raises(DirectoryError, match="CURSOR"):
        d.handle(
            {"operation": "list_tools", "system_id": "crm", "cursor": cursor + "x"}
        )


def test_open_schema_and_strict_arguments_without_mutating_input():
    d = directory()
    payload = {
        "system_id": "crm",
        "tool_name": "tool_00",
        "arguments": {"customer_id": "c1"},
    }
    with pytest.raises(DirectoryError, match="TOOL_SCHEMA_NOT_OPENED"):
        d.validate_call(payload)
    opened = d.handle(
        {"operation": "open_tool", "system_id": "crm", "tool_name": "tool_00"}
    )
    assert set(opened) == {"system_id", "tool_name", "description", "input_schema"}
    assert opened["input_schema"]["additionalProperties"] is False
    assert d.validate_call(payload) == ("crm", "tool_00", {"customer_id": "c1"})
    opened["input_schema"]["properties"].clear()
    assert d.handle(
        {"operation": "open_tool", "system_id": "crm", "tool_name": "tool_00"}
    )["input_schema"]["properties"]
    for args in ({"customer_id": "c1", "invented": 1}, {"customer_id": 7}, {}):
        bad = {**payload, "arguments": args}
        before = deepcopy(bad)
        with pytest.raises(DirectoryError, match="ARGUMENT"):
            d.validate_call(bad)
        assert bad == before


@pytest.mark.parametrize(
    "request_data",
    [
        {"operation": "list_systems", "limit": 99},
        {"operation": "list_tools"},
        {"operation": "list_tools", "system_id": "unknown"},
        {"operation": "search_tools", "query": " "},
        {"operation": "open_tool", "system_id": "crm", "tool_name": "absent"},
    ],
)
def test_malformed_directory_requests_are_rejected(request_data):
    with pytest.raises(DirectoryError):
        directory().handle(request_data)


def test_catalog_snapshot_and_nested_schema_validation(monkeypatch):
    from tau2.platform.bank_directory import SYSTEMS

    tools = catalog()
    tools[0].input_schema["properties"]["request"] = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "required": ["amount"],
    }
    d = BankDirectory({"crm": tools}, token="snapshot")
    page = d.handle({"operation": "list_systems"})
    monkeypatch.setitem(SYSTEMS, "crm", "Changed after session start")
    assert d.handle({"operation": "list_systems"}) == page
    d.handle({"operation": "open_tool", "system_id": "crm", "tool_name": "tool_00"})
    for request in ({"amount": "24"}, {"amount": True}, {"amount": 24, "extra": 1}):
        with pytest.raises(DirectoryError, match="INVALID_ARGUMENT"):
            d.validate_call(
                {
                    "system_id": "crm",
                    "tool_name": "tool_00",
                    "arguments": {"customer_id": "c1", "request": request},
                }
            )
    assert d.validate_call(
        {
            "system_id": "crm",
            "tool_name": "tool_00",
            "arguments": {"customer_id": "c1", "request": {"amount": 24.0}},
        }
    )


def test_public_session_token_cannot_sign_a_cursor_for_another_session():
    import hashlib
    import hmac

    d1, d2 = directory("public-a"), directory("public-b")
    cursor = d1.handle({"operation": "list_systems"})["next_cursor"]
    own_cursor = d2.handle({"operation": "list_systems"})["next_cursor"]
    for visible_cursor in (cursor, own_cursor):
        payload = visible_cursor.split(".")[0]
        forged = (
            payload
            + "."
            + hmac.new(b"public-b", payload.encode(), hashlib.sha256).hexdigest()
        )
        with pytest.raises(DirectoryError, match="CURSOR"):
            d2.handle({"operation": "list_systems", "cursor": forged})


@pytest.mark.parametrize(
    "names",
    [
        [],
        ["tool_00", "tool_00"],
        [f"tool_{i:02d}" for i in range(21)],
        "tool_00",
        [None],
    ],
)
def test_batch_limits_enforced_by_runtime_without_opening_tools(names):
    d = directory()
    with pytest.raises(DirectoryError, match="INVALID_ARGUMENT"):
        d.handle({"operation": "open_tools", "system_id": "crm", "tool_names": names})
    assert not d._opened
