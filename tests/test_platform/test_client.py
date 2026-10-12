import pytest

pytest.importorskip("mcp")

from tau2.data_model.tasks import Action
from tau2.domains.banking_ru.environment import get_tasks
from tau2.platform.client import OpenedSession, PlatformClient
from test_platform.conftest import run_with_app


class _Unserializable:
    """Значение, которое pydantic/MCP не может сериализовать в JSON.

    Используется, чтобы вызвать реальную ошибку внутри `async with
    client.connect(...)` (аналог сбоя транспорта посреди реплея), не
    трогая приватные детали протокола и не подменяя вызовы моками.
    """


def test_client_round_trip(app):
    async def go(http):
        client = PlatformClient("http://platform", http_client=http)
        tasks = await client.list_tasks()
        opened = await client.open("Bank 46", participant="ref-client")
        assert isinstance(opened, OpenedSession)
        async with client.connect(opened) as mcp:
            names = [t.name for t in (await mcp.list_tools()).tools]
            await mcp.call_tool("find_customer", {"phone": "+7 903 555-11-22"})
            before_done = await client.state(opened.token)
            await mcp.call_tool("done", {})
        result = await client.result(opened.token)
        await client.close(opened.token)
        return len(tasks), names, before_done, result

    n, names, before_done, result = run_with_app(app, go)
    assert n == len(get_tasks("base")) and "done" in names
    assert before_done["status"] == "open" and before_done["calls"] == 1
    assert result["status"] == "done" and result["reward"] == 0.0


def test_replay_actions_closes_session_on_mid_replay_error(app):
    """Ошибка внутри `async with client.connect(...)` не должна оставлять
    серверную сессию висеть в `live`: `replay_actions` обязан закрыть её
    даже когда пробрасывает исключение наружу."""
    bad_action = Action(
        action_id="bad-1",
        name="find_customer",
        arguments={"phone": _Unserializable()},
    )
    opened_token: dict = {}

    async def go(http):
        client = PlatformClient("http://platform", http_client=http)
        original_open = client.open

        async def recording_open(task_id, participant=None):
            opened = await original_open(task_id, participant)
            opened_token["token"] = opened.token
            return opened

        client.open = recording_open

        with pytest.raises(Exception):
            await client.replay_actions(
                "Bank 46", [bad_action], participant="ref-client"
            )

        # Сессия должна быть закрыта на сервере: GET по её токену — 404.
        return await http.get(f"/sessions/{opened_token['token']}")

    response = run_with_app(app, go)
    assert response.status_code == 404
