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


def test_bank_budget_keeps_session_open_but_error_limit_terminates():
    s = make(max_calls=2)
    s.call("find_customer", {"phone": "+7 903 555-11-22"})
    s.call("find_customer", {"phone": "+7 903 555-11-22"})
    assert s.status is SessionStatus.OPEN
    e = make(max_errors=2)
    e.call("get_customer_profile", {"customer_id": "x"})
    e.call("get_customer_profile", {"customer_id": "y"})
    assert e.status is SessionStatus.TOO_MANY_ERRORS


def test_unknown_tool_is_an_error_not_a_crash():
    s = make()
    res = s.call("drop_database", {})
    assert res.is_error is True and s.errors == 1


def test_banking_default_allows_six_errors_and_done_at_100_calls():
    s = make()
    for _ in range(6):
        s.call("unknown_tool", {})
    assert s.status is SessionStatus.OPEN
    for _ in range(93):
        s.call("find_customer", {"phone": "+7 903 555-11-22"})
    assert s.status is SessionStatus.OPEN
    s.call("find_customer", {"phone": "+7 903 555-11-22"})
    assert s.status is SessionStatus.OPEN
    assert len(s.log) == 100
    assert s.call("find_customer", {"phone": "+7 903 555-11-22"}).is_error
    assert len(s.log) == 100
    assert not s.call("done", {}).is_error
    assert s.status is SessionStatus.DONE


def test_other_domain_keeps_default_error_stop_and_explicit_override():
    from tau2.domains.mock.environment import get_environment as mock_environment
    from tau2.domains.mock.environment import get_tasks as mock_tasks

    task = mock_tasks()[0]
    s = PlatformSession(task, mock_environment)
    assert s.max_calls == 60
    for _ in range(4):
        s.call("unknown_tool", {})
    assert s.status is SessionStatus.OPEN
    s.call("unknown_tool", {})
    assert s.status is SessionStatus.TOO_MANY_ERRORS
    overridden = PlatformSession(task, mock_environment, max_calls=2, max_errors=0)
    for _ in range(2):
        overridden.call("unknown_tool", {})
    assert overridden.status is SessionStatus.BUDGET_EXHAUSTED


def test_business_success_requires_agent_done():
    s = make()
    s.task = s.task.model_copy(deep=True)
    s.task.evaluation_criteria.business_process_only = True
    s.replay_actions(s.task.evaluation_criteria.actions)
    assert s.status is SessionStatus.OPEN
    assert s.errors == 0
    assert s.grade().final_reward == 0.0
    assert {k.value: v for k, v in s.grade().reward_breakdown.items()} == {
        "DB": 0.0,
        "ACTION": 0.0,
    }
    s.call("done", {})
    assert s.grade().final_reward == 1.0


@pytest.mark.parametrize("configured,expected", [(None, 100), (200, 100), (20, 20)])
def test_scenario_call_ceiling_is_effective_and_cannot_be_raised(configured, expected):
    task = TASKS["Bank 2"].model_copy(update={"bank_budget_mode": "hard"})
    session = PlatformSession(
        task, get_environment, max_calls=configured, mcp_layout="directory"
    )
    assert session.max_calls == expected
    session.call("bank_directory", {"operation": "list_systems"})
    assert session.discovery_calls == 1
    assert len(session.log) == 0
    for _ in range(expected):
        session.call(
            "bank_directory",
            {
                "operation": "open_tool",
                "system_id": "crm",
                "tool_name": "find_customer",
            },
        )
        session.call(
            "bank_call",
            {
                "system_id": "crm",
                "tool_name": "find_customer",
                "arguments": {"phone": "+7 926 118-77-30"},
            },
        )
    assert len(session.log) == expected
    assert session.status is SessionStatus.OPEN
    assert session.call(
        "bank_call",
        {
            "system_id": "crm",
            "tool_name": "find_customer",
            "arguments": {"phone": "+7 926 118-77-30"},
        },
    ).is_error


@pytest.mark.parametrize("layout", ["flat", "directory"])
def test_diagnostic_budget_executes_after_100_and_keeps_accounting(layout):
    session = make("Bank 2", mcp_layout=layout)
    assert session.task.bank_budget_mode == "diagnostic"
    if layout == "directory":
        session.call(
            "bank_directory",
            {
                "operation": "open_tool",
                "system_id": "crm",
                "tool_name": "find_customer",
            },
        )
    for _ in range(102):
        if layout == "directory":
            result = session.call(
                "bank_call",
                {
                    "system_id": "crm",
                    "tool_name": "find_customer",
                    "arguments": {"phone": "+7 926 118-77-30"},
                },
            )
        else:
            result = session.call("find_customer", {"phone": "+7 926 118-77-30"})
        assert not result.is_error
    assert len(session.log) == 102
    assert session.status is SessionStatus.OPEN
    assert "102/100" in session.budget_status_text()
    assert "превышение 2" in session.budget_status_text()
    if layout == "directory":
        from tests.test_domains.test_banking_ru.test_bank2_linked_goals import invoke

        assert not invoke(session, "done", {}).is_error
    else:
        assert not session.call("done", {}).is_error
    assert len(session.log) == 102
    assert session.status is SessionStatus.DONE


@pytest.mark.parametrize("invalid", [0, -1, True, "50", 1.5])
def test_scenario_call_ceiling_rejects_invalid_values(invalid):
    from pydantic import ValidationError

    from tau2.data_model.tasks import Task

    raw = TASKS["Bank 2"].model_dump()
    raw["max_bank_calls"] = invalid
    with pytest.raises(ValidationError):
        Task.model_validate(raw)
