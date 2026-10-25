"""Real document delivery preconditions and retry boundaries."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment
from tests.test_domains.test_banking_ru.tool_contract_support import (
    identify,
    reject,
    succeed,
)

CUSTOMER = "egorova_n_3358"


def registered():
    env = get_environment()
    from tau2.domains.banking_ru.document_service_models import DocumentServiceProfile

    env.tools.db.document_service_profiles[CUSTOMER] = DocumentServiceProfile(
        delivery_outcome_unknown=False
    )
    identify(env, CUSTOMER)
    case = succeed(
        env,
        "create_cashback_investigation",
        customer_id=CUSTOMER,
        program="N40",
        amount=178,
        items=[
            {"transaction_id": "txn_077031", "amount": 70},
            {"transaction_id": "txn_077040", "amount": 108},
        ],
    )
    return env, case


def test_case_document_is_bound_to_registration_and_delivery_is_idempotent():
    env, case = registered()
    before = deepcopy(env.tools.db.accounts)
    docs = succeed(env, "get_case_documents", case_id=case["id"])
    receipt = next(d for d in docs if d["purpose"] == "registration_confirmation")
    assert receipt["customer_id"] == CUSTOMER
    assert receipt["case_id"] == case["id"]
    assert receipt["transmissible"] is True
    contacts = succeed(env, "get_document_delivery_contacts", customer_id=CUSTOMER)
    contact = next(c for c in contacts if c["verified"])
    result = succeed(
        env,
        "deliver_case_document",
        document_id=receipt["id"],
        contact_id=contact["id"],
        idempotency_key="confirmation-1",
    )
    repeat = succeed(
        env,
        "deliver_case_document",
        document_id=receipt["id"],
        contact_id=contact["id"],
        idempotency_key="confirmation-1",
    )
    assert repeat == result
    assert result["status"] == "delivered"
    assert len(env.tools.db.document_deliveries) == 1
    assert env.tools.db.accounts == before
    assert len(env.tools.db.cases) == 1


def test_diagnostic_document_cannot_be_delivered_or_shared():
    env, case = registered()
    docs = succeed(env, "get_case_documents", case_id=case["id"])
    internal = next(d for d in docs if d["audience"] == "internal")
    contact = succeed(env, "get_document_delivery_contacts", customer_id=CUSTOMER)[0]
    reject(
        env,
        "deliver_case_document",
        "ERR_DOCUMENT_FORBIDDEN",
        document_id=internal["id"],
        contact_id=contact["id"],
        idempotency_key="internal",
    )
    reject(env, "share_document", "ERR_DOCUMENT_FORBIDDEN", document_id=internal["id"])
    assert not env.tools.db.document_deliveries


def test_preparing_document_requires_explicit_status_and_no_duplicate_registration():
    env, case = registered()
    docs = succeed(env, "get_case_documents", case_id=case["id"])
    receipt = next(d for d in docs if d["purpose"] == "registration_confirmation")
    env.tools.db.documents[receipt["id"]].readiness = "preparing"
    contact = succeed(env, "get_document_delivery_contacts", customer_id=CUSTOMER)[0]
    reject(
        env,
        "deliver_case_document",
        "ERR_DOCUMENT_NOT_READY",
        document_id=receipt["id"],
        contact_id=contact["id"],
        idempotency_key="ready",
    )
    assert not env.tools.db.document_deliveries
    status = succeed(env, "get_document_status", document_id=receipt["id"])
    assert status["readiness"] == "ready"
    succeed(
        env,
        "deliver_case_document",
        document_id=receipt["id"],
        contact_id=contact["id"],
        idempotency_key="ready",
    )
    assert len(env.tools.db.cases) == len(env.tools.db.document_deliveries) == 1


pytestmark = pytest.mark.usefixtures("fees_rewards_parent_scenarios")
