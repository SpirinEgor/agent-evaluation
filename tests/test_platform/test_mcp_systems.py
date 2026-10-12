"""Representative checks of system boundaries over real MCP exchanges."""

import asyncio
import json

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.client import PlatformClient
from tau2.platform.service import PlatformSettings, build_app
from tau2.platform.session import PlatformSession, SessionStatus
from test_platform.conftest import run_with_app


def test_system_catalog_is_the_unchanged_bank1_catalog(tmp_path):
    app = build_app(PlatformSettings(results_dir=tmp_path))

    async def go(http):
        client = PlatformClient("http://platform", http_client=http)
        opened = await client.open("Bank 1")
        assert set(opened.mcp_urls) == {
            "crm",
            "identity",
            "support",
            "knowledge",
            "cards",
            "payments",
            "disputes",
            "core_banking",
            "servicing",
            "loyalty",
            "digital_access",
            "documents",
            "risk",
        }
        catalogs = {}
        async with client.connect_systems(opened) as servers:
            for name, server in servers.items():
                catalogs[name] = (await server.list_tools()).tools
            assert {t.name for t in catalogs["identity"]} == {
                "verify_identity",
                "send_otp",
                "check_otp",
            }
            assert {t.name for t in catalogs["support"]} == {
                "ask_client",
                "reply_to_ticket",
                "escalate_to_human",
                "done",
            }
            assert {t.name for t in catalogs["knowledge"]} == {
                "file_list",
                "file_find",
                "file_search",
                "file_read",
            }
            await servers["support"].call_tool("done", {})
        urls = opened.mcp_urls
        await client.close(opened.token)
        for url in urls.values():
            assert (await http.post(url, json={})).status_code == 404
        return catalogs

    catalogs = run_with_app(app, go)
    flattened = [t for tools in catalogs.values() for t in tools]
    expected = PlatformSession(get_tasks()[0], get_environment).mcp_tools
    assert len(flattened) == len({t.name for t in flattened}) == 63
    assert {t.name: t.model_dump() for t in flattened} == {
        t.name: t.model_dump() for t in expected
    }


def test_routing_rejection_does_not_execute_during_grading():
    s = PlatformSession(get_tasks()[0], get_environment)
    s.call(
        "verify_identity",
        {"customer_id": "belova_n_2201", "credential": "капучино"},
        system="identity",
    )
    blocked = s.call(
        "block_card", {"card_id": "card_4417", "reason": "fraud"}, system="crm"
    )
    assert blocked.is_error
    assert s.env.tools.db.cards["card_4417"].status == "active"
    assert s.log[-1][0].name == "block_card"
    assert s.log[-1][1].execution_rejected is True
    # Strict replay must preserve the rejection rather than execute the write.
    s.call("done", {}, system="support")
    assert s.grade().final_reward == 0.0


def test_identity_and_budgets_are_shared_across_systems():
    task = next(t for t in get_tasks() if t.id == "Bank 21")
    s = PlatformSession(task, get_environment, max_calls=4)
    answer = s.call(
        "ask_client",
        {
            "customer_id": "petrova_i_4821",
            "request": {"type": "identity", "method": "code_word"},
        },
        system="support",
    )
    receipt = json.loads(answer.content[0].text)
    s.call(
        "verify_identity",
        {
            "customer_id": "petrova_i_4821",
            "credential": receipt["data"]["credential"],
            "answer_id": receipt["answer_id"],
        },
        system="identity",
    )
    cards = s.call("get_cards", {"customer_id": "petrova_i_4821"}, system="cards")
    assert not cards.is_error
    result = s.call(
        "block_card", {"card_id": "card_4418", "reason": "lost"}, system="cards"
    )
    assert not result.is_error
    assert s.env.tools.db.cards["card_4418"].status == "blocked"
    assert s.status is SessionStatus.BUDGET_EXHAUSTED
    assert s.call("find_customer", {"phone": "+7 901 000-48-21"}, system="crm").is_error
    e = PlatformSession(task, get_environment, max_errors=2)
    assert e.call("done", {}, system="cards").is_error
    assert e.status is SessionStatus.OPEN
    assert e.call("get_cards", {}, system="identity").is_error
    assert e.status is SessionStatus.TOO_MANY_ERRORS


def test_wrong_system_forbidden_attempt_still_fails_action_grade():
    s = PlatformSession(get_tasks()[0], get_environment)
    for action in s.task.evaluation_criteria.actions:
        s.call(action.name, action.arguments)
    wrong = s.call(
        "open_dispute",
        {"transaction_id": "txn_unknown", "amount": 8400, "reason": "fraud"},
        system="cards",
    )
    assert wrong.is_error
    s.call("done", {}, system="support")
    assert s.grade().reward_breakdown["DB"] == 1.0
    assert s.grade().reward_breakdown["ACTION"] == 0.0
    event = s.to_simulation_run().info["mcp_events"][-2]
    assert event["system"] == "cards" and event["canonical_name"] == "open_dispute"
    assert event["arguments"]["transaction_id"] == "txn_unknown"


def test_catalog_rejects_unmapped_tools_and_duplicate_names():
    from tau2.domains.banking_ru.mcp_systems import partition_tools

    s = PlatformSession(get_tasks()[0], get_environment)
    with pytest.raises(ValueError, match="duplicates"):
        partition_tools(s.mcp_tools + [s.mcp_tools[0]])
    unknown = s.mcp_tools[0].model_copy(update={"name": "new_unmapped_tool"})
    with pytest.raises(ValueError, match="new_unmapped_tool"):
        partition_tools([unknown])


def test_budget_termination_persists_after_final_http_result(tmp_path):
    app = build_app(PlatformSettings(results_dir=tmp_path, max_calls=1))

    async def go(http):
        client = PlatformClient("http://platform", http_client=http)
        opened = await client.open("Bank 1")
        async with client.connect(opened, "crm") as mcp:
            assert not (
                await mcp.call_tool("find_customer", {"phone": "+7 916 340-11-25"})
            ).is_error
        result = await client.result(opened.token)
        await client.close(opened.token)
        return result

    result = run_with_app(app, go)
    saved = json.loads((tmp_path / f"{result['token']}.json").read_text())
    assert saved["termination_reason"] == "max_steps"
    assert [m["role"] for m in saved["messages"]] == ["assistant", "tool"]
    assert saved["info"]["mcp_events"][-1]["transmitted_at"]


def test_protocol_rejection_can_be_followed_by_a_valid_reference(tmp_path):
    app = build_app(PlatformSettings(results_dir=tmp_path))

    async def go(http):
        client = PlatformClient("http://platform", http_client=http)
        opened = await client.open("Bank 1")
        raw = await http.post(
            opened.mcp_urls["crm"],
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "find_customer",
                    "arguments": {"phone": "+7 916 340-11-25"},
                },
            },
        )
        assert raw.status_code == 400
        async with client.connect_systems(opened) as servers:
            for action in get_tasks()[0].evaluation_criteria.actions:
                assert not (
                    await servers[opened.server_for(action.name)].call_tool(
                        action.name, action.arguments
                    )
                ).is_error
            await servers["support"].call_tool("done", {})
        result = await client.result(opened.token)
        await client.close(opened.token)
        return result

    result = run_with_app(app, go)
    assert result["reward"] == 1.0


def test_write_formed_while_source_response_is_held_fails_grade(tmp_path):
    computed = asyncio.Event()
    release = asyncio.Event()
    app = build_app(PlatformSettings(results_dir=tmp_path))

    class HoldSource:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            body = bytearray()
            hold = False

            async def recv():
                nonlocal hold
                message = await receive()
                if message["type"] == "http.request":
                    body.extend(message.get("body", b""))
                    if not message.get("more_body"):
                        try:
                            request = json.loads(body)
                        except (ValueError, TypeError):
                            request = {}
                        hold = (
                            request.get("params", {}).get("arguments", {}).get("path")
                            == "/knowledge/kb_113.md"
                        )
                return message

            async def emit(message):
                if hold and message["type"] == "http.response.body":
                    computed.set()
                    await release.wait()
                await send(message)

            await self.app(scope, recv, emit)

    app.add_middleware(HoldSource)

    async def go(http):
        client = PlatformClient("http://platform", http_client=http)
        opened = await client.open("Bank 1")
        held = None
        try:
            async with client.connect_systems(opened) as servers:
                for action in get_tasks()[0].evaluation_criteria.actions:
                    server = servers[opened.server_for(action.name)]
                    if action.arguments.get("path") == "/knowledge/kb_113.md":
                        held = asyncio.create_task(
                            server.call_tool(action.name, action.arguments)
                        )
                        await asyncio.wait_for(computed.wait(), 10)
                        continue
                    result = await server.call_tool(action.name, action.arguments)
                    assert not result.is_error
                    if action.name == "block_card":
                        assert held is not None and not held.done()
                        release.set()
                        assert not (await held).is_error
                await servers["support"].call_tool("done", {})
            return await client.result(opened.token)
        finally:
            release.set()
            if held:
                await held
            await client.close(opened.token)

    result = run_with_app(app, go)
    assert result["reward_breakdown"]["DB"] == 1.0
    assert result["reward_breakdown"]["ENV_ASSERTION"] == 1.0
    assert result["reward_breakdown"]["ACTION"] == 0.0
    assert result["reward"] == 0.0


@pytest.mark.parametrize("layout", ["single", "systems"])
def test_bank1_reference_and_noop_keep_grades_over_mcp(tmp_path, layout):
    app = build_app(PlatformSettings(results_dir=tmp_path))
    task = get_tasks()[0]

    async def go(http):
        client = PlatformClient("http://platform", http_client=http)
        result = await client.replay_actions(
            "Bank 1", task.evaluation_criteria.actions, mcp_layout=layout
        )
        noop = await client.replay_actions("Bank 1", [], mcp_layout=layout)
        return result, noop

    result, noop = run_with_app(app, go)
    assert result["reward"] == 1.0
    assert result["reward_breakdown"] == {
        "DB": 1.0,
        "ENV_ASSERTION": 1.0,
        "ACTION": 1.0,
    }
    assert noop["reward"] == 0.0
    saved = json.loads((tmp_path / f"{result['token']}.json").read_text())
    events = saved["info"]["mcp_events"]
    assert saved["info"]["mcp_layout"] == layout
    assert len(events) == len(task.evaluation_criteria.actions) + 1
    assert events[-1]["canonical_name"] == "done"
    assert all(e["role"] == "assistant" and e["response"] for e in events)
    assert events[-1]["system"] == (
        "support" if layout == "systems" else "banking_ru_pilot"
    )
