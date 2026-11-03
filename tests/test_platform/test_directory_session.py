"""Real bank operations through the directory surface; no model requests."""

import json
from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession, SessionStatus


@pytest.fixture(scope="module")
def task():
    return next(t for t in get_tasks() if t.id == "Bank 2")


def session(task, **kwargs):
    return PlatformSession(task, get_environment, mcp_layout="directory", **kwargs)


def open_tool(s, system, name):
    response = s.call(
        "bank_directory",
        {"operation": "open_tool", "system_id": system, "tool_name": name},
    )
    assert not response.is_error, response
    return json.loads(response.content[0].text)


def bank_call(s, system, name, args):
    return s.call(
        "bank_call", {"system_id": system, "tool_name": name, "arguments": args}
    )


def test_only_two_tools_and_no_native_bypass(task):
    s = session(task)
    assert {t.name for t in s.mcp_tools} == {"bank_directory", "bank_call"}
    assert s.call("get_customer_profile", {"customer_id": "unknown"}).is_error
    assert s.call("find_customer", {"phone": "0"}, system="crm").is_error
    assert not s.env.tools.db.tool_calls_log
    assert s.status is SessionStatus.OPEN
    with pytest.raises(ValueError):
        s.build_server("crm")


def test_schema_validation_and_canonical_trace(task):
    s = session(task)
    customer = next(iter(s.env.tools.db.customers.values()))
    args = {"phone": customer.phone}
    assert bank_call(s, "crm", "find_customer", args).is_error
    schema = open_tool(s, "crm", "find_customer")
    assert "Returns:" not in schema["description"]
    initial = deepcopy(s.env.tools.db.tool_calls_log)
    for bad in ({**args, "invented": 1}, {"phone": 123}, {"phone": []}):
        assert bank_call(s, "crm", "find_customer", bad).is_error
    assert s.env.tools.db.tool_calls_log == initial
    result = bank_call(s, "crm", "find_customer", args)
    assert not result.is_error
    assert json.loads(result.content[0].text)[0]["id"] == customer.id
    call, message = s.log[-1]
    assert call.name == "find_customer" and call.arguments == args
    assert message.error is False
    event = s.mcp_events[-1]
    assert event["original_name"] == "bank_call"
    assert event["canonical_name"] == "find_customer"
    assert event["canonical_arguments"] == args and event["system"] == "crm"
    assert "response_order" in event
    assert all(
        call.name != "bank_directory"
        for call in s.trajectory()
        if hasattr(call, "name")
    )
    assert s.discovery_calls == 1
    assert len(s.tool_call_errors) == 4
    other = session(task)
    assert bank_call(other, "crm", "find_customer", args).is_error


def test_separate_discovery_budget_and_diagnostics(task):
    s = session(task, max_calls=2, max_errors=1, max_discovery_calls=2)
    open_tool(s, "crm", "find_customer")
    open_tool(s, "support", "done")
    assert s.call("bank_directory", {"operation": "list_systems"}).is_error
    assert s.discovery_calls == 3 and not s.log
    assert s.status is SessionStatus.OPEN
    customer = next(iter(s.env.tools.db.customers.values()))
    assert not bank_call(s, "crm", "find_customer", {"phone": customer.phone}).is_error
    assert not bank_call(s, "support", "done", {}).is_error
    assert s.status is SessionStatus.DONE
    assert len(s.log) == 1 and s.errors == 0


def test_legacy_dispatch_bridges_cannot_bypass_schema_gate(task):
    s = session(task)
    for name in ("unlock_discoverable_agent_tool", "call_discoverable_agent_tool"):
        assert s.call(
            "bank_directory",
            {"operation": "open_tool", "system_id": "support", "tool_name": name},
        ).is_error
    opened = open_tool(s, "loyalty", "execute_reward_resolution")
    assert opened["input_schema"]["required"]
    assert bank_call(s, "crm", "execute_reward_resolution", {}).is_error


@pytest.mark.parametrize("route,want", [("reference", 1.0), ("noop", 0.0)])
def test_bank2_directory_reference_and_noop_use_real_grader(task, route, want):
    s = session(task)
    if route == "reference":
        # A recovered technical error must not change the reference reward.
        assert bank_call(s, "crm", "find_customer", {"phone": 0}).is_error
        open_tool(s, "crm", "find_customer")
        assert bank_call(s, "crm", "find_customer", {"phone": 0}).is_error
        s.replay_actions(task.evaluation_criteria.actions)
    open_tool(s, "support", "done")
    assert not bank_call(s, "support", "done", {}).is_error
    assert s.grade().final_reward == want
    from tests.utils.banking_full_grade import grade_full_session

    assert grade_full_session(s).reward_info.final_reward == want


def test_gateway_over_real_mcp_and_saved_canonical_journal(task, tmp_path):
    from tau2.domains.banking_ru.mcp_systems import SINGLE_SERVER
    from tau2.platform.client import PlatformClient
    from tau2.platform.service import PlatformSettings, build_app
    from test_platform.conftest import run_with_app

    app = build_app(
        PlatformSettings(
            mcp_layout="directory", max_discovery_calls=20, results_dir=tmp_path
        )
    )
    phone = next(iter(get_environment().tools.db.customers.values())).phone

    async def go(http):
        client = PlatformClient("http://platform", http_client=http)
        opened = await client.open("Bank 2")
        assert set(opened.mcp_urls) == {SINGLE_SERVER}
        async with client.connect(opened) as server:
            assert {t.name for t in (await server.list_tools()).tools} == {
                "bank_directory",
                "bank_call",
            }
            for name, owner in (("find_customer", "crm"), ("done", "support")):
                response = await server.call_tool(
                    "bank_directory",
                    {"operation": "open_tool", "system_id": owner, "tool_name": name},
                )
                assert not response.is_error
            rejected = await server.call_tool(
                "bank_call",
                {
                    "system_id": "crm",
                    "tool_name": "find_customer",
                    "arguments": {"phone": 123},
                },
            )
            assert rejected.is_error
            valid = await server.call_tool(
                "bank_call",
                {
                    "system_id": "crm",
                    "tool_name": "find_customer",
                    "arguments": {"phone": phone},
                },
            )
            assert not valid.is_error
            assert not (
                await server.call_tool(
                    "bank_call",
                    {"system_id": "support", "tool_name": "done", "arguments": {}},
                )
            ).is_error
        result = await client.result(opened.token)
        assert result["discovery"]["calls"] == 2
        assert result["discovery"]["tool_call_errors"]["INVALID_ARGUMENT"] == 1
        await client.close(opened.token)
        assert (await http.post(opened.mcp_url, json={})).status_code == 404
        return opened.token

    token = run_with_app(app, go)
    record = json.loads((tmp_path / f"{token}.json").read_text())
    assert record["info"]["mcp_layout"] == "directory"
    events = record["info"]["mcp_events"]
    assert all("response_order" in event for event in events)
    actual = [
        event for event in events if event.get("canonical_name") == "find_customer"
    ]
    assert actual[-1]["canonical_arguments"] == {"phone": phone}
    assert actual[-1]["system"] == "crm"
    assert record["info"]["discovery"]["tool_call_errors"]["INVALID_ARGUMENT"] == 1


def test_schema_receipt_and_pending_done_are_not_inferred_from_execution(task):
    s = session(task)
    args = {"operation": "open_tool", "system_id": "crm", "tool_name": "find_customer"}
    attempt = s.begin_call("bank_directory", args, None)
    assert not s.call("bank_directory", args, attempt=attempt).is_error
    customer = next(iter(s.env.tools.db.customers.values()))
    assert bank_call(s, "crm", "find_customer", {"phone": customer.phone}).is_error
    assert not s.env.tools.db.tool_calls_log
    s.result_sent(attempt)
    assert not bank_call(s, "crm", "find_customer", {"phone": customer.phone}).is_error

    open_tool(s, "support", "done")
    payload = {
        "system_id": "crm",
        "tool_name": "find_customer",
        "arguments": {"phone": customer.phone},
    }
    pending = s.begin_call("bank_call", payload, None)
    assert not s.call("bank_call", payload, attempt=pending).is_error
    assert bank_call(s, "support", "done", {}).is_error
    assert s.status is SessionStatus.OPEN
    s.result_sent(pending)
    assert not bank_call(s, "support", "done", {}).is_error
    assert s.status is SessionStatus.DONE


def test_banking_budget_still_bounds_gateway_calls(task):
    s = session(task, max_calls=2, max_errors=1)
    open_tool(s, "crm", "find_customer")
    assert bank_call(s, "crm", "find_customer", {"phone": 123}).is_error
    assert s.status is SessionStatus.OPEN and s.errors == 0
    phone = next(iter(s.env.tools.db.customers.values())).phone
    assert not bank_call(s, "crm", "find_customer", {"phone": phone}).is_error
    assert s.status is SessionStatus.BUDGET_EXHAUSTED
    before = len(s.env.tools.db.tool_calls_log)
    assert bank_call(s, "crm", "find_customer", {"phone": phone}).is_error
    assert len(s.env.tools.db.tool_calls_log) == before


@pytest.mark.parametrize("violation", ["foreign_data", "confidentiality"])
def test_gateway_canonical_trace_preserves_business_violations(task, violation):
    s = session(task)
    s.replay_actions(task.evaluation_criteria.actions)
    owner = next(
        a.arguments["customer_id"]
        for a in task.evaluation_criteria.actions
        if a.name == "verify_identity"
    )
    if violation == "foreign_data":
        customer = next(c for c in s.env.tools.db.customers.values() if c.id != owner)
        open_tool(s, "crm", "get_customer_profile")
        assert not bank_call(
            s, "crm", "get_customer_profile", {"customer_id": customer.id}
        ).is_error
    else:
        open_tool(s, "support", "reply_to_ticket")
        secret = s.env.tools.db.customers[owner].otp_code
        assert not bank_call(
            s,
            "support",
            "reply_to_ticket",
            {"customer_id": owner, "text": "Ваш OTP: " + secret},
        ).is_error
    open_tool(s, "support", "done")
    assert not bank_call(s, "support", "done", {}).is_error
    assert s.grade().final_reward == 0


def test_directory_cli_and_mock_domain_boundary(task):
    from tau2.domains.mock.environment import get_environment as get_mock_environment
    from tau2.platform.__main__ import parse_args

    settings, _, _ = parse_args(
        ["--mcp-layout", "directory", "--max-discovery-calls", "17"]
    )
    assert settings.mcp_layout == "directory" and settings.max_discovery_calls == 17
    with pytest.raises(ValueError):
        PlatformSession(task, get_mock_environment, mcp_layout="directory")


def test_schema_receipt_must_precede_dependent_request_not_just_execution(task):
    s = session(task)
    params = {
        "operation": "open_tool",
        "system_id": "crm",
        "tool_name": "find_customer",
    }
    opening = s.begin_call("bank_directory", params, None)
    assert not s.call("bank_directory", params, attempt=opening).is_error
    phone = next(iter(s.env.tools.db.customers.values())).phone
    payload = {
        "system_id": "crm",
        "tool_name": "find_customer",
        "arguments": {"phone": phone},
    }
    pending = s.begin_call("bank_call", payload, None)
    s.result_sent(opening)
    assert s.call("bank_call", payload, attempt=pending).is_error
    assert not s.env.tools.db.tool_calls_log
    s.result_sent(pending)
    assert not s.call("bank_call", payload).is_error


def test_legacy_profile_recognizes_gateway_completion():
    # All shipped tasks use the business profile now. Keep the generic legacy
    # completion branch under test with explicit synthetic criteria.
    from tau2.data_model.tasks import EvaluationCriteria, Task, UserScenario

    task = Task(
        id="synthetic-legacy-gateway",
        user_scenario=UserScenario(instructions=""),
        evaluation_criteria=EvaluationCriteria(
            business_process_only=False,
            completion_mode="completion_only",
            actions=[],
            reward_basis=["DB", "ACTION"],
        ),
    )
    s = session(task)
    open_tool(s, "support", "done")
    assert not bank_call(s, "support", "done", {}).is_error
    reward = s.grade()
    assert "missing_successful_done" not in (reward.info or {}).get("completion", [])
