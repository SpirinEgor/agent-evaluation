import pytest

pytest.importorskip("mcp")

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.mcp_bridge import DONE_TOOL_NAME, STOP_TOKEN
from tau2.platform.session import PlatformSession, SessionStatus

TASKS = {task.id: task for task in get_tasks("base")}


def make(task_id="Bank 46", **kw) -> PlatformSession:
    return PlatformSession(TASKS[task_id], get_environment, **kw)


def test_two_sessions_do_not_share_state():
    a, b = make("Bank 21"), make("Bank 21")
    answer = a.call(
        "ask_client",
        {
            "customer_id": "petrova_i_4821",
            "request": {"type": "identity", "method": "code_word"},
        },
    )
    import json

    receipt = json.loads(answer.content[0].text)
    a.call(
        "verify_identity",
        {
            "customer_id": "petrova_i_4821",
            "credential": receipt["data"]["credential"],
            "answer_id": receipt["answer_id"],
        },
    )
    a.call("block_card", {"card_id": "card_4418", "reason": "lost"})
    assert a.env.tools.db.cards["card_4418"].status == "blocked"
    assert b.env.tools.db.cards["card_4418"].status == "active"
    assert a.token != b.token and len(a.token) == 32


def test_calls_are_logged_in_order_with_sequential_ids_and_errors_counted():
    s = make()
    ok = s.call("find_customer", {"phone": "+7 903 555-11-22"})
    err = s.call("get_customer_profile", {"customer_id": "nobody"})
    assert ok.is_error is False and err.is_error is True
    assert [c.name for c, _ in s.log] == ["find_customer", "get_customer_profile"]
    assert [c.id for c, _ in s.log] == ["0", "1"]
    assert s.log[1][1].error is True and s.errors == 1
    assert s.status is SessionStatus.OPEN


def test_done_finishes_session_and_blocks_further_calls():
    s = make()
    res = s.call(DONE_TOOL_NAME, {})
    assert res.content[0].text == STOP_TOKEN and res.is_error is False
    assert s.status is SessionStatus.DONE and s.finished_at is not None
    after = s.call("find_customer", {"phone": "+7 903 555-11-22"})
    assert after.is_error is True and "завершена" in after.content[0].text
    assert s.log == [], "после done вызовы не исполняются и не журналируются"


def test_budgets_terminate_the_session():
    s = make(max_calls=2)
    s.call("find_customer", {"phone": "+7 903 555-11-22"})
    s.call("find_customer", {"phone": "+7 903 555-11-22"})
    assert s.status is SessionStatus.BUDGET_EXHAUSTED
    e = make(max_errors=2)
    e.call("get_customer_profile", {"customer_id": "x"})
    e.call("get_customer_profile", {"customer_id": "y"})
    assert e.status is SessionStatus.TOO_MANY_ERRORS


def test_unknown_tool_is_an_error_not_a_crash():
    s = make()
    res = s.call("drop_database", {})
    assert res.is_error is True and s.errors == 1
