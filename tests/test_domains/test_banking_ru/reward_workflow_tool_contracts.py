"""Real shared operation-status and investigation lifecycle API examples."""

from tests.test_domains.test_banking_ru.tool_contract_support import (
    identify,
    reject,
    succeed,
)

CUSTOMER = "egorova_n_3358"


def investigations(env):
    identify(env, CUSTOMER)
    rows = succeed(
        env,
        "get_cashback_investigations",
        customer_id=CUSTOMER,
        program="N40",
        period="2026-08",
    )
    assert any(c["id"] == "case-n40-current" and c["amount"] == 70 for c in rows)


def amendment(env):
    identify(env, CUSTOMER)
    result = succeed(
        env,
        "amend_cashback_investigation",
        case_id="case-n40-current",
        items=[{"transaction_id": "txn_077040", "amount": 108}],
    )
    assert result["amount"] == 178 and result["version"] == 2


def operation_status(env):
    identify(env, CUSTOMER)
    first = succeed(
        env,
        "deliver_case_document",
        document_id="case-n40-current:registration_confirmation",
        contact_id="doc-contact-" + CUSTOMER,
        idempotency_key="status-catalog",
    )
    assert first["status"] == "outcome_unknown"
    result = succeed(
        env,
        "get_bank_operation_status",
        customer_id=CUSTOMER,
        request_id=first["request_id"],
    )
    assert result["status"] == "delivered" and result["case_id"] == "case-n40-current"


def unknown_investigations(env):
    reject(env, "get_cashback_investigations", "ERR_NOT_FOUND", customer_id="unknown")


def unknown_amendment(env):
    reject(
        env,
        "amend_cashback_investigation",
        "ERR_NOT_FOUND",
        case_id="unknown",
        items=[{"transaction_id": "txn_077040", "amount": 108}],
    )


def unknown_operation(env):
    identify(env, CUSTOMER)
    reject(
        env,
        "get_bank_operation_status",
        "ERR_NOT_FOUND",
        customer_id=CUSTOMER,
        request_id="unknown",
    )


SUCCESS_SCENARIOS = {
    "get_cashback_investigations": investigations,
    "amend_cashback_investigation": amendment,
    "get_bank_operation_status": operation_status,
}
ERROR_SCENARIOS = {
    "get_cashback_investigations": unknown_investigations,
    "amend_cashback_investigation": unknown_amendment,
    "get_bank_operation_status": unknown_operation,
}
