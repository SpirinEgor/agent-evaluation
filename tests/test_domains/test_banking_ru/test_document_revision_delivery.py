"""An archived confirmation must never be delivered, even with owner consent."""

import pytest

from tests.test_domains.test_banking_ru.test_case_document_service import registered
from tests.test_domains.test_banking_ru.tool_contract_support import reject, succeed


def test_archived_confirmation_rejected_before_delivery():
    env, case = registered()
    docs = succeed(env, "get_case_documents", case_id=case["id"])
    current = next(d for d in docs if d["purpose"] == "registration_confirmation")
    doc = env.tools.db.documents[current["id"]].model_copy(deep=True)
    doc.id = current["id"] + ":archived"
    doc.version = "0"
    doc.readiness = "superseded"
    env.tools.db.documents[doc.id] = doc
    reject(
        env,
        "deliver_case_document",
        "ERR_DOCUMENT_SUPERSEDED",
        document_id=doc.id,
        contact_id="doc-contact-egorova_n_3358",
        idempotency_key="archived",
    )
    assert not env.tools.db.document_deliveries


pytestmark = pytest.mark.usefixtures("fees_rewards_parent_scenarios")
