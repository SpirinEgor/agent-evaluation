"""Reconcile posted refunds and use the confirmation issued after amendment."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

CASES = [
    (40, "reference", 1, 1),
    (40, "refund_state", 1, 1),
    (40, "noop", 0, 0),
    (40, "late_refund_evidence", 1, 0),
    (41, "reference", 1, 1),
    (41, "document_status", 1, 1),
    (41, "noop", 0, 0),
    (41, "stale_document_evidence", 1, 0),
    (41, "july_confirmation", 0, 0),
]


def _call(session, name, arguments):
    result = session.call(name, arguments)
    assert not result.is_error, result.content[0].text
    return result.content[0].text


def _replay_actions(number, route, task, session):
    for action in deepcopy(task.evaluation_criteria.actions):
        name, args = action.name, action.arguments
        if number == 40 and name == "get_transactions" and route != "reference":
            # Read the original commission and the existing evidence individually.
            for transaction_id in (
                "088214",
                "088215",
                "088217",
                "088218",
                "088219",
                "088220",
                "088221",
            ):
                _call(
                    session,
                    "get_transaction_details",
                    {"transaction_id": f"txn_{transaction_id}"},
                )
            if route == "refund_state":
                _call(session, "get_fee_refund_state", {"transaction_id": "txn_088219"})
            continue
        if route == "late_refund_evidence" and action.action_id == "bank40:consent":
            _call(session, name, args)
            _call(session, "get_transaction_details", {"transaction_id": "txn_088222"})
            continue
        if route == "stale_document_evidence":
            if name == "amend_cashback_investigation":
                _call(session, "get_case_documents", {"case_id": args["case_id"]})
            if name == "get_case_documents":
                continue
        if route == "document_status" and name == "amend_cashback_investigation":
            _call(session, "get_documents", {"customer_id": "egorova_n_3358"})
        if route == "document_status" and name == "get_case_documents":
            _call(
                session,
                "get_document_status",
                {"document_id": "case-n40-current:registration_confirmation"},
            )
            continue
        if route == "july_confirmation":
            if name == "deliver_case_document":
                args["document_id"] = "case-n40-other-period:registration_confirmation"
                _call(session, "get_documents", {"customer_id": "egorova_n_3358"})
                _call(session, name, args)
                continue
            if action.action_id == "bank41:delivery-receipt":
                continue
        _call(session, name, args)


@pytest.mark.parametrize("number,route,db,action", CASES)
def test_current_evidence(number, route, db, action):
    task = next(t for t in get_tasks() if t.id == f"Bank {number}")
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)
    if route != "noop":
        _replay_actions(number, route, task, session)
    _call(session, "done", {})

    for grade in (session.grade(), grade_full_session(session).reward_info):
        assert grade.reward_breakdown["DB"] == db, grade.info
        assert grade.reward_breakdown["ACTION"] == action, grade.info
        assert grade.reward == db * action
        if route == "late_refund_evidence":
            assert "bank40:posted-second-part-before-consent" in str(grade.info)
        if route == "stale_document_evidence":
            assert "bank41:current-document" in str(grade.info)
