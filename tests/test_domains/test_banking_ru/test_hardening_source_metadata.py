"""Real read tools preserve source facts needed for temporal/scope decisions."""

import json

from tau2.data_model.message import ToolCall
from tau2.domains.banking_ru.environment import get_environment


def read(env, name, args):
    result = env.get_response(ToolCall(id="source", name=name, arguments=args))
    assert not result.error, result.content
    return json.loads(result.content)


def test_executed_payment_metadata_survives_detail_and_list():
    env = get_environment()
    original = next(iter(env.tools.db.transactions.values()))
    facts = {
        "execution_payee": "Энергосбыт",
        "execution_account": "208-OLD",
        "template_id": "utility_test",
        "template_revision": "v1",
        "template_effective_at": "2026-08-01",
        "template_history": [
            {"revision": "v2", "effective_at": "2026-08-21", "account": "208-NEW"}
        ],
    }
    env.tools.db.transactions[original.id] = type(original).model_validate(
        {**original.model_dump(), **facts}
    )
    detail = read(env, "get_transaction_details", {"transaction_id": original.id})
    page = read(
        env, "get_transactions", {"customer_id": original.customer_id, "limit": 50}
    )
    row = next(x for x in page["items"] if x["id"] == original.id)
    for field, value in facts.items():
        assert detail.get(field) == value
        assert row.get(field) == value


def test_fee_period_and_contract_history_are_received():
    env = get_environment()
    txn = next(iter(env.tools.db.transactions.values()))
    period = {"start": "2026-08-28", "end": "2026-09-27"}
    env.tools.db.transactions[txn.id] = type(txn).model_validate(
        {**txn.model_dump(), "billing_period": period, "tariff_revision": "v2"}
    )
    customer = env.tools.db.customers[txn.customer_id]
    history = [
        {"revision_id": "v2", "effective_from": "2026-08-28", "monthly_fee": 249}
    ]
    env.tools.db.customers[customer.id] = type(customer).model_validate(
        {**customer.model_dump(), "servicing_contract_history": history}
    )
    detail = read(env, "get_transaction_details", {"transaction_id": txn.id})
    profile = read(env, "get_customer_profile", {"customer_id": customer.id})
    assert detail.get("billing_period") == period
    assert detail.get("tariff_revision") == "v2"
    assert profile.get("servicing_contract_history") == history


def test_subscription_and_autopayment_contract_links_are_received():
    env = get_environment()
    sub = next(iter(env.tools.db.subscriptions.values()))
    auto = next(iter(env.tools.db.autopayments.values()))
    fields = {
        "merchant_id": "merchant_test",
        "contract_reference": "contract_test",
        "profile_label": "Личный основной",
    }
    env.tools.db.subscriptions[sub.id] = type(sub).model_validate(
        {**sub.model_dump(), **fields}
    )
    env.tools.db.autopayments[auto.id] = type(auto).model_validate(
        {**auto.model_dump(), "customer_id": sub.customer_id, **fields}
    )
    result = read(env, "get_subscriptions", {"customer_id": sub.customer_id})
    for group, target in [("subscriptions", sub.id), ("autopayments", auto.id)]:
        row = next(x for x in result[group] if x["id"] == target)
        for field, value in fields.items():
            assert row.get(field) == value
