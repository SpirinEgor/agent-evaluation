"""Compact Bank 2: actual decisions, receipt delivery and independent grading."""

from copy import deepcopy

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.test_bank2_linked_goals import (
    data,
    invoke,
    task2,
)
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route",
    [
        "reference",
        "alternative",
        "noop",
        "missing_payment",
        "missing_exchange",
        "missing_handover",
        "missing_explanation",
        "wrong_decision",
        "wrong_allocation_recover",
        "wrong_handover_recover",
        "disclosure",
        "rejected_then_recover",
        "idempotent_old_allocation",
    ],
)
def test_compact_whole_grade(mode, route):
    session = PlatformSession(
        task2(), get_environment, max_errors=0, mcp_layout="directory"
    )
    actions = deepcopy(session.task.evaluation_criteria.actions)
    delivered = {}
    if route == "noop":
        actions = []
    for action in actions:
        args = deepcopy(action.arguments)
        if (
            route == "missing_payment"
            and action.action_id == "bank2:v37:reconcile-correction"
        ):
            continue
        if route == "missing_exchange" and action.name == "send_case_message":
            continue
        if route == "missing_handover" and action.action_id == "bank2:v37:handover":
            continue
        if (
            route == "missing_explanation"
            and action.action_id == "bank2:v37:explanation"
        ):
            continue
        if route == "alternative":
            if action.name == "execute_reward_resolution":
                args["component_ids"].reverse()
            if action.name == "reconcile_payment_allocation":
                args["lines"].reverse()
            if action.name == "record_case_handover":
                args["items"].reverse()
                args["source_ids"] = []
        if (
            route == "wrong_decision"
            and action.name == "resolve_reward_duplicate_review"
        ):
            args["decision"] = "duplicate_confirmed"
            result = invoke(session, action.name, args)
            assert not result.is_error
            break
        if (
            route == "idempotent_old_allocation"
            and action.action_id == "bank2:v37:reconcile-correction"
        ):
            original = next(a for a in actions if a.action_id == "bank2:v18:reconcile")
            assert not invoke(
                session, original.name, deepcopy(original.arguments)
            ).is_error
        if (
            route == "wrong_allocation_recover"
            and action.action_id == "bank2:v37:reconcile-correction"
        ):
            bad = deepcopy(args)
            bad["lines"][1]["related_purchase_id"] = "txn_804205"
            result = invoke(session, action.name, bad)
            assert not result.is_error
            args["expected_revision"] += 1
        if (
            route == "wrong_handover_recover"
            and action.action_id == "bank2:v37:handover"
        ):
            bad = deepcopy(args)
            bad["assignments"] = {}
            assert not invoke(session, action.name, bad).is_error
        if route == "disclosure" and action.action_id == "bank2:v36:send":
            args["customer_info_fields"] = ["phone", "birth_date"]
        if route == "rejected_then_recover" and action.name == "record_case_handover":
            bad = deepcopy(args)
            bad["source_ids"] = ["does-not-exist"]
            assert invoke(session, action.name, bad).is_error
        result = invoke(session, action.name, args)
        assert not result.is_error, (route, action.action_id)
        if action.name == "send_case_message":
            delivered[args["exchange_id"]] = {
                r["id"] for r in data(result).get("processing_notes", [])
            }
    invoke(session, "done", {})
    grade = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    actual = (
        grade.reward_breakdown[RewardType.DB],
        grade.reward_breakdown[RewardType.ACTION],
    )
    if route in {
        "reference",
        "alternative",
        "rejected_then_recover",
        "idempotent_old_allocation",
    }:
        assert actual == (1, 1)
        assert "handoff-late-v34" in delivered["exchange_610903_purchase"]
        assert "reply-late-v26" in delivered["exchange_610903_scope"]
        assert "reply-late-v26" not in delivered["exchange_610903_purchase"]
    elif route == "noop":
        assert actual == (0, 0)
    else:
        assert actual[1] == 0, (route, actual)
        reasons = str(grade.info)
        expected = {
            "missing_payment": "submission_payment_allocation",
            "missing_exchange": "submission_exchange",
            "missing_handover": "submission_handover",
            "missing_explanation": "submission_explanation",
            "wrong_decision": "duplicate_review_evidence",
            "wrong_allocation_recover": "allocation_economic_lines",
            "wrong_handover_recover": "case_output_assignments",
            "disclosure": "customer",
        }[route]
        assert expected in reasons, route


def test_batch_schemas_atomic_and_received():
    session = PlatformSession(
        task2(), get_environment, max_errors=0, mcp_layout="directory"
    )
    opened = session.call(
        "bank_directory",
        {
            "operation": "open_tools",
            "system_id": "crm",
            "tool_names": ["find_customer", "get_cashback_investigations"],
        },
    )
    assert not opened.is_error
    assert len(data(opened)["tools"]) == 2
    result = session.call(
        "bank_call",
        {
            "system_id": "crm",
            "tool_name": "get_cashback_investigations",
            "arguments": {"customer_id": "gromov_a_1187"},
        },
    )
    assert not result.is_error
    other = PlatformSession(
        task2(), get_environment, max_errors=0, mcp_layout="directory"
    )
    failed = other.call(
        "bank_directory",
        {
            "operation": "open_tools",
            "system_id": "crm",
            "tool_names": ["find_customer", "does_not_exist"],
        },
    )
    assert failed.is_error
    assert other.call(
        "bank_call",
        {
            "system_id": "crm",
            "tool_name": "find_customer",
            "arguments": {"phone": "+7 926 118-77-30"},
        },
    ).is_error


def test_handbooks_discoverable_without_duplicate_body():
    session = PlatformSession(
        task2(), get_environment, max_errors=0, mcp_layout="directory"
    )
    result = session.knowledge.search_documents("reward_handbook")
    assert len(result["items"]) == 5
    for item in result["items"]:
        doc = session.knowledge.read_document(item["document_id"], item["revision_id"])
        assert doc["content"]
        receipt = data(invoke(session, "file_read", {"path": item["path"]}))
        assert receipt["content"] == doc["content"]
        assert receipt["included_documents"]
        assert all("content" not in row for row in receipt["included_documents"])


def test_initial_authorization_is_not_a_ticket_claim():
    task = task2()
    task.initial_state.initialization_data.agent_data["session"][
        "verified_customers"
    ] = []
    session = PlatformSession(
        task, get_environment, max_errors=0, mcp_layout="directory"
    )
    assert session.task.ticket_context["identity_status"] == "verified"
    assert invoke(
        session, "get_cashback_investigations", {"customer_id": "gromov_a_1187"}
    ).is_error
