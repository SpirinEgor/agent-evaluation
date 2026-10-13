"""Shared facts and predicates for the approved three-case hardening."""

import json

import pytest

from tau2.data_model.message import ToolCall
from tau2.domains.banking_ru.environment import get_environment


def read(env, name, args):
    result = env.get_response(ToolCall(id="facts", name=name, arguments=args))
    assert not result.error, result.content
    return json.loads(result.content)


def test_recipient_id_survives_real_detail_and_list():
    env = get_environment()
    txn = next(iter(env.tools.db.transactions.values()))
    env.tools.db.transactions[txn.id] = type(txn).model_validate(
        {**txn.model_dump(), "recipient_id": "recipient-distinct"}
    )
    detail = read(env, "get_transaction_details", {"transaction_id": txn.id})
    page = read(env, "get_transactions", {"customer_id": txn.customer_id, "limit": 50})
    row = next(x for x in page["items"] if x["id"] == txn.id)
    assert detail.get("recipient_id") == "recipient-distinct"
    assert row.get("recipient_id") == "recipient-distinct"


def test_tariff_revision_survives_real_read():
    env = get_environment()
    customer = next(iter(env.tools.db.customers.values()))
    tariff = env.tools.db.tariffs[customer.tariff_id]
    facts = {
        "revision_id": "current",
        "effective_from": "2026-08-01",
        "effective_to": "2026-08-31",
        "scheduled_revisions": [
            {
                "revision_id": "future",
                "effective_from": "2026-09-01",
                "max_sbp_limit": 300000,
            }
        ],
    }
    env.tools.db.tariffs[tariff.id] = type(tariff).model_validate(
        {**tariff.model_dump(), **facts}
    )
    actual = read(env, "get_tariff", {"customer_id": customer.id})
    for field, value in facts.items():
        assert actual.get(field) == value


def test_exactly_one_claim_checks_real_case_and_dispute():
    env = get_environment()
    txn = next(
        t
        for t in env.tools.db.transactions.values()
        if t.kind == "purchase"
        and t.status == "posted"
        and t.dispute_id is None
        and t.date >= "2026-08-01"
    )
    env.tools.db.session.verified_customers.append(txn.customer_id)
    assert not env.tools.assert_exactly_one_claim(txn.customer_id, txn.id, 100)
    env.tools.open_dispute(txn.id, "other", 100)
    assert env.tools.assert_exactly_one_claim(txn.customer_id, txn.id, 100)
    assert not env.tools.assert_exactly_one_claim(txn.customer_id, txn.id, 99)
    env.tools.create_case(txn.customer_id, "merchant_investigation", txn.id, 100)
    assert not env.tools.assert_exactly_one_claim(txn.customer_id, txn.id, 100)


@pytest.mark.parametrize("matching", [True, False])
def test_pending_scope_records_failed_unverified_attempt(matching):
    env = get_environment()
    card = next(iter(env.tools.db.cards.values()))
    from tau2.domains.banking_ru.data_model import LimitRequest

    env.tools.db.limit_requests["scope-test"] = LimitRequest(
        id="scope-test",
        customer_id=card.customer_id,
        request_type="spending_limit",
        object_type="card",
        object_id=card.id,
        channel="sbp" if matching else "cash",
        period="per_operation",
        requires_decision=True,
        requested_limit=400000,
        status="pending",
        decided_at=env.tools.db.today,
        public_reason="pending",
    )
    result = env.get_response(
        ToolCall(
            id="attempt",
            name="set_limit",
            arguments={"card_id": card.id, "limit_type": "sbp", "amount": 200000},
        )
    )
    assert result.error
    assert env.tools.assert_no_pending_limit_change_attempts(card.id, "sbp") is (
        not matching
    )
