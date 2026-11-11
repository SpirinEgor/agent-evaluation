"""Public synthetic correspondence: schedule, provenance and isolation."""

import json

import pytest
from pydantic import ValidationError

from tau2.data_model.tasks import ScheduledClientMessage, Task
from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.test_bank2_linked_goals import invoke, task2


def messages(result):
    rows = []
    for block in result.content:
        try:
            value = json.loads(block.text)
        except (ValueError, AttributeError):
            continue
        if isinstance(value, dict) and value.get("type") == "client_message":
            rows.append(value)
    return rows


def setup():
    task = task2().model_copy(deep=True)
    task.scheduled_client_messages = [
        ScheduledClientMessage(
            id="test-1",
            after_bank_calls=2,
            customer_id="gromov_a_1187",
            case_id="case_610903",
            text="Synthetic customer follow-up.",
        )
    ]
    return PlatformSession(task, get_environment, max_errors=0, mcp_layout="directory")


def test_delivery_once_after_threshold_and_discovery_does_not_count():
    session = setup()
    assert not messages(session.call("bank_directory", {"operation": "list_systems"}))
    args = {"customer_id": "gromov_a_1187"}
    assert not messages(invoke(session, "get_cashback_rules", args))
    response = invoke(session, "get_cashback_rules", args)
    assert not response.is_error
    delivered = messages(response)
    assert [r["id"] for r in delivered] == ["test-1"]
    assert delivered[0]["source"] == "customer_correspondence"
    assert session.mcp_events[-1]["client_message_deliveries"][0]["bank_call"] == 2
    assert not messages(invoke(session, "get_cashback_rules", args))
    assert not messages(invoke(session, "done", {}))


def test_no_delivery_to_foreign_case_or_unverified_customer():
    for mode in ["foreign", "unverified"]:
        session = setup()
        session.task.scheduled_client_messages[0].after_bank_calls = 1
        if mode == "foreign":
            session.task.scheduled_client_messages[0].case_id = "missing-case"
        else:
            session.env.tools.db.session.verified_customers = []
        response = invoke(
            session, "get_cashback_rules", {"customer_id": "gromov_a_1187"}
        )
        assert not messages(response)


def test_error_defers_delivery_and_fresh_session_resets_it():
    session = setup()
    session.task.scheduled_client_messages[0].after_bank_calls = 1
    assert not messages(
        invoke(session, "get_cashback_rules", {"customer_id": "unknown"})
    )
    response = invoke(session, "get_cashback_rules", {"customer_id": "gromov_a_1187"})
    assert len(messages(response)) == 1
    assert not setup()._delivered_client_messages


def test_schedule_rejects_duplicate_ids_and_nonpositive_threshold():
    task = setup().task.model_dump()
    task["scheduled_client_messages"] *= 2
    with pytest.raises(ValidationError):
        Task.model_validate(task)
    with pytest.raises(ValidationError):
        ScheduledClientMessage(
            id="x", after_bank_calls=0, customer_id="c", case_id="k", text="x"
        )


def test_sdk_delivers_current_schedule_and_replays_without_score_penalty():
    from tests.test_platform import test_platform_sdk as sdk_fixture

    task = next(t for t in sdk_fixture.collect() if t.id == "Bank 2")
    bench = sdk_fixture.benchmark(task)
    journal = sdk_fixture.execute(bench, sdk_fixture.path(task))
    deliveries = [
        d
        for e in bench.session.mcp_events
        for d in e.get("client_message_deliveries", [])
    ]
    assert [(d["id"], d["bank_call"]) for d in deliveries] == [
        ("R01", 8),
        ("R02", 18),
        ("R03", 28),
    ]
    assert all(
        any('"id": "' + d["id"] + '"' in row.content for row in journal)
        for d in deliveries
    )
    result, _, _ = sdk_fixture.grade(task, bench, journal)
    assert result["reward"] == 1
