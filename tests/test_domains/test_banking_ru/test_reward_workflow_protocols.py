"""Shared protocol tests: replay cannot credit twice or lose document/channel state."""

import json

import pytest

from tau2.data_model.tasks import Task, UserScenario
from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession


def identified(task_id):
    customers = {
        "Bank 2": ("gromov_a_1187", "+7 926 118-77-30"),
        "Bank 40": ("solovyova_a_7734", "+7 916 773-40-56"),
        "Bank 41": ("egorova_n_3358", "+7 916 335-84-20"),
    }
    customer, phone = customers[task_id]
    task = Task(id="api-fixture", user_scenario=UserScenario(instructions="API test"))
    s = PlatformSession(task, get_environment, max_calls=150, max_errors=50)
    decode(s.call("find_customer", {"phone": phone}))
    answer = decode(
        s.call(
            "ask_client",
            {
                "customer_id": customer,
                "request": {"type": "identity", "method": "code_word"},
            },
        )
    )
    result = s.call(
        "verify_identity",
        {
            "customer_id": customer,
            "credential": answer["data"]["credential"],
            "answer_id": answer["answer_id"],
        },
    )
    assert not result.is_error
    if task_id == "Bank 2":
        s.env.tools.db.cashback_adjustment_permissions["adjustment-A"] = {
            "id": "adjustment-A",
            "customer_id": "gromov_a_1187",
            "transaction_id": "txn_552290",
            "program": "standard_cashback",
            "status": "approved",
            "valid_from": "2026-08-01",
            "valid_to": "2026-08-31",
            "max_amount": 20,
            "remaining_amount": 20,
            "used_amount": 0,
            "used": False,
        }
    return s


def decode(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_adjustment_is_idempotent_without_spending_accrual_cap_twice():
    s = identified("Bank 2")
    db = s.env.tools.db
    db.cashback_adjustment_permissions["test-authority"] = {
        "id": "test-authority",
        "customer_id": "gromov_a_1187",
        "transaction_id": "txn_552290",
        "program": "standard_cashback",
        "status": "approved",
        "valid_from": "2026-08-01",
        "valid_to": "2026-08-31",
        "max_amount": 20,
        "remaining_amount": 20,
        "used_amount": 0,
        "used": False,
    }
    initial = db.accounts["acc_1187"].balance
    accrued = db.cashback_rules["gromov_a_1187"].accrued_current_period
    args = {
        "permission_id": "test-authority",
        "transaction_id": "txn_552290",
        "amount": 20,
        "idempotency_key": "adjust-once",
    }
    first = decode(s.call("execute_cashback_adjustment", args))
    again = decode(s.call("execute_cashback_adjustment", args))
    assert first == again
    assert db.accounts["acc_1187"].balance == initial + 20
    assert db.cashback_rules["gromov_a_1187"].accrued_current_period == accrued
    assert db.cashback_adjustment_permissions["test-authority"]["remaining_amount"] == 0
    assert s.call(
        "execute_cashback_adjustment", {**args, "idempotency_key": "new-key"}
    ).is_error
    assert db.accounts["acc_1187"].balance == initial + 20


def test_amendment_keeps_one_case_and_supersedes_document():
    s = identified("Bank 41")
    c = "egorova_n_3358"
    first = decode(
        s.call(
            "create_cashback_investigation",
            {
                "customer_id": c,
                "program": "N40",
                "period": "2026-08",
                "amount": 70,
                "items": [{"transaction_id": "txn_077031", "amount": 70}],
            },
        )
    )
    before = decode(s.call("get_case_documents", {"case_id": first["id"]}))
    args = {
        "case_id": first["id"],
        "items": [{"transaction_id": "txn_077040", "amount": 108}],
    }
    updated = decode(s.call("amend_cashback_investigation", args))
    again = decode(s.call("amend_cashback_investigation", args))
    assert updated == again and updated["amount"] == 178 and updated["version"] == 2
    docs = decode(s.call("get_case_documents", {"case_id": first["id"]}))
    assert any(d["version"] == "1" and d["readiness"] == "superseded" for d in docs)
    current = [
        d for d in docs if d["audience"] == "customer" and d["readiness"] == "ready"
    ]
    assert (
        len(current) == 1
        and current[0]["version"] == "2"
        and "178" in current[0]["body"]
    )
    assert len([v for v in s.env.tools.db.cases.values() if v.id == first["id"]]) == 1
    assert any(d["version"] == "1" for d in before)


def test_channel_confirmation_changes_bank_state_not_only_client_text():
    s = identified("Bank 41")
    c = "egorova_n_3358"
    contact = "doc-contact-unconfirmed-" + c
    from tau2.domains.banking_ru.data_model import ClientAnswer

    s.env.tools.db.client_answers[c] = [
        a
        for a in s.env.tools.db.client_answers[c]
        if not a.document_channel_confirmation
    ]
    s.env.tools.db.client_answers[c].append(
        ClientAnswer(
            keywords=[],
            answer="Подтвердил",
            document_channel_confirmation={"contact_id": contact, "confirmed": True},
        )
    )
    assert not s.env.tools.db.document_contacts[contact].verified
    decode(
        s.call(
            "ask_client",
            {
                "customer_id": c,
                "request": {
                    "type": "document_channel_confirmation",
                    "contact_id": contact,
                },
            },
        )
    )
    contacts = decode(s.call("get_document_delivery_contacts", {"customer_id": c}))
    assert next(x for x in contacts if x["id"] == contact)["verified"] is True


def test_delivery_unknown_is_recovered_by_status_and_same_key_without_second_delivery():
    s = identified("Bank 41")
    c = "egorova_n_3358"
    from tau2.domains.banking_ru.document_service_models import DocumentServiceProfile

    s.env.tools.db.document_service_profiles[c] = DocumentServiceProfile(
        delivery_outcome_unknown=True
    )
    case = decode(
        s.call(
            "create_cashback_investigation",
            {
                "customer_id": c,
                "program": "N40",
                "amount": 178,
                "items": [
                    {"transaction_id": "txn_077031", "amount": 70},
                    {"transaction_id": "txn_077040", "amount": 108},
                ],
            },
        )
    )
    args = {
        "document_id": case["id"] + ":registration_confirmation",
        "contact_id": "doc-contact-" + c,
        "idempotency_key": "delivery-once",
    }
    first = decode(s.call("deliver_case_document", args))
    assert first["status"] == "outcome_unknown"
    status = decode(
        s.call(
            "get_bank_operation_status",
            {"customer_id": c, "request_id": first["request_id"]},
        )
    )
    assert status["status"] == "delivered"
    repeated = decode(s.call("deliver_case_document", args))
    assert repeated["status"] == "delivered"
    assert len(s.env.tools.db.document_deliveries) == 1


def test_unobserved_document_version_change_returns_recoverable_conflict():
    s = identified("Bank 41")
    c = "egorova_n_3358"
    from tau2.domains.banking_ru.document_service_models import DocumentServiceProfile

    s.env.tools.db.document_service_profiles[c] = DocumentServiceProfile()
    case = decode(
        s.call(
            "create_cashback_investigation",
            {
                "customer_id": c,
                "program": "N40",
                "amount": 178,
                "items": [
                    {"transaction_id": "txn_077031", "amount": 70},
                    {"transaction_id": "txn_077040", "amount": 108},
                ],
            },
        )
    )
    docid = case["id"] + ":registration_confirmation"
    decode(s.call("get_case_documents", {"case_id": case["id"]}))
    # An independent bank-side revision unseen by the caller, not a secret task rule.
    s.env.tools.db.documents[docid].version = "2"
    args = {
        "document_id": docid,
        "contact_id": "doc-contact-" + c,
        "idempotency_key": "conflict-recovery",
        "expected_version": "1",
    }
    result = s.call("deliver_case_document", args)
    assert result.is_error and "ERR_DOCUMENT_CONFLICT" in result.content[0].text
    decode(s.call("get_case_documents", {"case_id": case["id"]}))
    assert (
        decode(s.call("deliver_case_document", {**args, "expected_version": "2"}))[
            "status"
        ]
        == "delivered"
    )
    assert s.env.tools.assert_case_document_delivery_attempts(c, "N40")


def test_distinct_partial_fee_refunds_keep_all_ledger_rows_and_remaining_budget():
    s = identified("Bank 40")
    db = s.env.tools.db
    before = db.accounts["acc_7734"].balance
    args = {"transaction_id": "txn_088214", "request": {"type": "fee_correction"}}
    decode(
        s.call("refund_fee", {**args, "amount": 50, "idempotency_key": "partial-50"})
    )
    decode(
        s.call("refund_fee", {**args, "amount": 20, "idempotency_key": "partial-20"})
    )
    linked = [
        t
        for t in db.transactions.values()
        if t.original_transaction_id == "txn_088214" and t.kind == "fee_refund"
    ]
    assert len(linked) == 2 and sum(t.amount for t in linked) == 70
    assert db.accounts["acc_7734"].balance == before + 70
    state = decode(s.call("get_fee_refund_state", {"transaction_id": "txn_088214"}))
    assert state["remaining"] == 129


@pytest.mark.parametrize("bad_field", ["used_amount", "balance"])
def test_adjustment_validates_every_amount_before_first_write(bad_field):
    from copy import deepcopy

    s = identified("Bank 2")
    db = s.env.tools.db
    if bad_field == "used_amount":
        db.cashback_adjustment_permissions["adjustment-A"]["used_amount"] = "invalid"
    else:
        db.accounts["acc_1187"].balance = 0.001
    before = deepcopy(db.model_dump())
    result = s.call(
        "execute_cashback_adjustment",
        {
            "permission_id": "adjustment-A",
            "transaction_id": "txn_552290",
            "amount": 20,
            "idempotency_key": "atomic",
        },
    )
    assert result.is_error
    assert db.model_dump() == before


pytestmark = pytest.mark.usefixtures("operation_api_scenarios")


@pytest.mark.parametrize(
    "variant", ["settled", "unpaid", "other_case", "missing_credit"]
)
def test_zero_claim_removal_requires_real_full_settlement_in_same_case(variant):
    from tau2.domains.banking_ru.data_model import Case, Transaction
    from tau2.domains.banking_ru.reward_resolution_models import (
        RewardResolutionExecution,
    )

    session = identified("Bank 2")
    db = session.env.tools.db
    customer, tid = "gromov_a_1187", "txn_552290"
    db.cashback_rules[customer].cycles = [
        {"period": "2026-07", "limit": 1000, "transaction_ids": [tid]}
    ]
    db.transactions = {
        k: v for k, v in db.transactions.items() if v.original_transaction_id != tid
    }
    db.cases["settled-case"] = Case(
        id="settled-case",
        customer_id=customer,
        category="cashback_investigation",
        reward_program="standard_cashback",
        reward_period="2026-07",
        reward_items=[
            {"transaction_id": tid, "amount": 60},
            {"transaction_id": "txn_552291", "amount": 7},
        ],
        transaction_ids=[tid, "txn_552291"],
        amount=67,
        created_at="2026-08-01",
    )
    credit = Transaction(
        id="settled-credit",
        customer_id=customer,
        account_id="acc_1187",
        card_id="card_1187",
        date="2026-08-01",
        amount=59 if variant == "unpaid" else 60,
        merchant="Выплата по соглашению",
        kind="cashback",
        status="posted",
        original_transaction_id=tid,
    )
    if variant != "missing_credit":
        db.transactions[credit.id] = credit
    db.reward_resolution_executions["settled-operation"] = RewardResolutionExecution(
        id="settled-operation",
        customer_id=customer,
        case_id="other-case" if variant == "other_case" else "settled-case",
        period="2026-07",
        transaction_ids=[tid],
        idempotency_key="payment",
        packet={"offer_id": "fixture-offer"},
        result={"status": "succeeded", "ledger": [credit.model_dump()]},
    )
    before = session.env.tools.get_db_hash()
    result = session.call(
        "amend_cashback_investigation",
        {
            "case_id": "settled-case",
            "items": [{"transaction_id": tid, "amount": 0}],
        },
    )
    if variant != "settled":
        assert result.is_error
        assert session.env.tools.get_db_hash() == before
    else:
        value = decode(result)
        assert value["reward_items"] == [{"transaction_id": "txn_552291", "amount": 7}]
        assert value["transaction_ids"] == ["txn_552291"]
        assert value["amount"] == 7
        repeated = decode(
            session.call(
                "amend_cashback_investigation",
                {
                    "case_id": "settled-case",
                    "items": [{"transaction_id": tid, "amount": 0}],
                },
            )
        )
        assert repeated == value
