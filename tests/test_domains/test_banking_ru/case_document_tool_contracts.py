"""Real shared document-service success and failure examples."""

from tests.test_domains.test_banking_ru.tool_contract_support import (
    identify,
    reject,
    succeed,
)

CUSTOMER = "egorova_n_3358"


def _registered(env):
    identify(env, CUSTOMER)
    return succeed(
        env,
        "create_cashback_investigation",
        customer_id=CUSTOMER,
        program="N40",
        amount=178,
        items=[
            dict(transaction_id="txn_077031", amount=70),
            dict(transaction_id="txn_077040", amount=108),
        ],
    )


def case_documents(env):
    case = _registered(env)
    documents = succeed(env, "get_case_documents", case_id=case["id"])
    assert len(documents) == 3
    assert {
        (d["version"], d["readiness"])
        for d in documents
        if d["purpose"] == "registration_confirmation"
    } == {("1", "ready"), ("0", "superseded")}
    assert {d["audience"] for d in documents} == {"customer", "internal"}
    assert all(d["case_id"] == case["id"] for d in documents)


def document_status(env):
    case = _registered(env)
    document_id = case["id"] + ":registration_confirmation"
    env.tools.db.documents[document_id].readiness = "preparing"
    status = succeed(env, "get_document_status", document_id=document_id)
    assert status["readiness"] == "ready"
    assert not env.tools.db.document_deliveries


def document_contacts(env):
    identify(env, CUSTOMER)
    contacts = succeed(env, "get_document_delivery_contacts", customer_id=CUSTOMER)
    assert any(c["verified"] and c["channel"] == "verified_sms" for c in contacts)
    assert any(
        not c["verified"] and c["address"] == "natalia.new@example.org"
        for c in contacts
    )


def document_delivery(env):
    case = _registered(env)
    result = succeed(
        env,
        "deliver_case_document",
        document_id=case["id"] + ":registration_confirmation",
        contact_id="doc-contact-" + CUSTOMER,
        idempotency_key="catalog-safe",
    )
    if result["status"] == "outcome_unknown":
        result = succeed(
            env,
            "get_bank_operation_status",
            customer_id=CUSTOMER,
            request_id=result["request_id"],
        )
    assert result["status"] == "delivered"
    assert len(env.tools.db.document_deliveries) == 1


def unknown_case(env):
    reject(env, "get_case_documents", "ERR_NOT_FOUND", case_id="unknown_case")


def unknown_document(env):
    reject(env, "get_document_status", "ERR_NOT_FOUND", document_id="unknown_doc")


def unknown_contact_customer(env):
    reject(
        env,
        "get_document_delivery_contacts",
        "ERR_NOT_FOUND",
        customer_id="unknown_customer",
    )


def invalid_document_delivery(env):
    case = _registered(env)
    reject(
        env,
        "deliver_case_document",
        "ERR_CONTACT_UNVERIFIED",
        document_id=case["id"] + ":registration_confirmation",
        contact_id="doc-contact-unconfirmed-" + CUSTOMER,
        idempotency_key="catalog-blocked",
    )
    assert not env.tools.db.document_deliveries


SUCCESS_SCENARIOS = {
    "get_case_documents": case_documents,
    "get_document_status": document_status,
    "get_document_delivery_contacts": document_contacts,
    "deliver_case_document": document_delivery,
}
ERROR_SCENARIOS = {
    "get_case_documents": unknown_case,
    "get_document_status": unknown_document,
    "get_document_delivery_contacts": unknown_contact_customer,
    "deliver_case_document": invalid_document_delivery,
}
