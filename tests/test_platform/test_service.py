import json

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.mcp_systems import startup_policy
from test_platform.conftest import run_with_app


async def open_session(http, task_id="Bank 46", participant=None):
    body = {"task_id": task_id}
    if participant:
        body["participant"] = participant
    r = await http.post("/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_health_and_task_list_expose_only_inputs(app):
    async def go(http):
        assert (await http.get("/health")).json() == {"status": "ok"}
        tasks = (await http.get("/tasks")).json()
        return tasks

    tasks = run_with_app(app, go)
    assert len(tasks) == len(get_tasks("base"))
    assert set(tasks[0]) == {"task_id", "ticket"}
    assert "evaluation_criteria" not in json.dumps(tasks)


def test_create_session_returns_inputs_and_never_criteria(app):
    async def go(http):
        return await open_session(http, participant="team-x")

    info = run_with_app(app, go)
    assert set(info) == {
        "token",
        "task_id",
        "ticket",
        "policy",
        "mcp_path",
        "mcp_paths",
        "mcp_layout",
        "max_calls",
        "max_errors",
    }
    assert info["mcp_path"] == f"/s/{info['token']}/mcp"
    canonical = next(task for task in get_tasks("base") if task.id == "Bank 46")
    environment = get_environment(task_id=canonical.id, solo_mode=True)
    assert info["ticket"] == canonical.ticket
    assert info["policy"] == startup_policy()
    assert len(environment.policy.splitlines()) <= 7
    assert "assert_" not in json.dumps(info)


def test_unknown_task_is_404(app):
    async def go(http):
        return (await http.post("/sessions", json={"task_id": "bank_999"})).status_code

    assert run_with_app(app, go) == 404


def test_incomplete_flow_over_mcp_scores_zero_and_persists(app, settings):
    async def go(http):
        info = await open_session(http)
        url = f"http://platform{info['mcp_path']}"
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as mcp:
                await mcp.initialize()
                names = [t.name for t in (await mcp.list_tools()).tools]
                cid = "kovalev_n_2217"
                await mcp.call_tool("find_customer", {"phone": "+7 903 555-11-22"})
                received = await mcp.call_tool(
                    "ask_client",
                    {
                        "customer_id": cid,
                        "request": {"type": "identity", "method": "code_word"},
                    },
                )
                assert not received.is_error, received
                answer = json.loads(received.content[0].text)
                assert answer["customer_id"] == cid
                assert answer["request_type"] == "identity"
                assert answer["status"] == "answered"
                assert answer["data"]["method"] == "code_word"
                verified = await mcp.call_tool(
                    "verify_identity",
                    {
                        "customer_id": cid,
                        "credential": answer["data"]["credential"],
                        "answer_id": answer["answer_id"],
                    },
                )
                assert not verified.is_error, verified
                await mcp.call_tool("get_customer_profile", {"customer_id": cid})
                await mcp.call_tool(
                    "reply_to_ticket",
                    {
                        "customer_id": cid,
                        "text": "Ответ по обращению. чат; отделени.",
                    },
                )
                stop = await mcp.call_tool("done", {})
        state = (await http.get(f"/sessions/{info['token']}")).json()
        result = (await http.get(f"/sessions/{info['token']}/result")).json()
        return names, stop, state, result, info["token"]

    names, stop, state, result, token = run_with_app(app, go)
    assert (
        len(names)
        == len(get_environment(task_id="Bank 46", solo_mode=True).get_tools()) + 1
    )
    assert names[-1] == "done"
    assert stop.content[0].text == "###STOP###"
    assert state == {
        "token": token,
        "task_id": "Bank 46",
        "status": "done",
        "calls": 5,
        "errors": 0,
    }
    assert result["reward"] == 0.0
    assert result["reward_breakdown"]["ACTION"] == 0.0
    # This completion-only task declares no explicit env_assertions.
    assert result["env_assertions"] == []
    saved = json.loads((settings.results_dir / f"{token}.json").read_text())
    assert saved["task_id"] == "Bank 46" and saved["reward_info"]["reward"] == 0.0


def test_result_on_open_session_is_a_conflict_not_a_reward_oracle(app):
    """/result не должен давать сигнал по ещё открытой сессии: участник
    иначе мог бы дёргать его после каждого вызова и подбирать действия."""

    async def go(http):
        info = await open_session(http)
        r = await http.get(f"/sessions/{info['token']}/result")
        state = (await http.get(f"/sessions/{info['token']}")).json()
        return r.status_code, r.json(), state

    status, body, state = run_with_app(app, go)
    assert state["status"] == "open"
    assert status == 409
    assert "reward" not in body


def test_delete_unmounts_mcp_and_forgets_session(app):
    async def go(http):
        info = await open_session(http)
        closed = (await http.delete(f"/sessions/{info['token']}")).json()
        gone = (await http.post(info["mcp_path"], json={})).status_code
        state = (await http.get(f"/sessions/{info['token']}")).status_code
        return closed, gone, state

    assert run_with_app(app, go) == ({"closed": True}, 404, 404)


def test_unresolved_result_does_not_publish_numeric_final_label(app, monkeypatch):
    from tau2.data_model.simulation import RewardInfo
    from tau2.platform.session import PlatformSession

    monkeypatch.setattr(
        PlatformSession,
        "grade",
        lambda self: RewardInfo(reward=1, info={"grading_status": "needs_review"}),
    )

    async def go(http):
        info = await open_session(http)
        url = f"http://platform{info['mcp_path']}"
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as mcp:
                await mcp.initialize()
                await mcp.call_tool("done", {})
        response = await http.get(f"/sessions/{info['token']}/result")
        assert response.status_code == 200, response.text
        return response.json()

    row = run_with_app(app, go)
    assert row["reward"] is None
    assert row["grading_status"] == "needs_review"
