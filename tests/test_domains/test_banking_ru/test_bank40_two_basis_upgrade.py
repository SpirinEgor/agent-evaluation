"""Retained Bank40 history boundaries; sequential whole-grade routes are separate."""

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession


def test_bank40_threshold_is_inclusive_and_changes_solution():
    """Focused premise test: 9999.99 fails balance-only eligibility, 10000 passes."""
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    s = PlatformSession(task, get_environment)
    rows = s.env.tools.db.customers["solovyova_a_7734"].servicing_contract_history
    first = rows[0]
    assert first["monthly_turnover"] < first["free_min_turnover"]
    assert first["avg_monthly_balance"] == first["free_min_balance"] == 10000

    def free(row):
        return (
            row["avg_monthly_balance"] >= row["free_min_balance"]
            or row["monthly_turnover"] >= row["free_min_turnover"]
        )

    assert free(first)
    mutated = {**first, "avg_monthly_balance": 9999.99}
    assert not free(mutated)
    assert free({**mutated, "monthly_turnover": 30000})


def test_bank40_semantic_links_and_future_conditions_have_no_measurements():
    """Source-facing scenario records must satisfy the same rules as calculation."""
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    s = PlatformSession(task, get_environment)
    db = s.env.tools.db
    owner = "solovyova_a_7734"
    card = db.cards["card_7735"]
    assert card.customer_id == owner
    assert card.account_id == "acc_7734"
    assert db.accounts[card.account_id].customer_id == owner
    for identity in ["txn_088214", "txn_088215", "txn_088219"]:
        fee = db.transactions[identity]
        assert (fee.customer_id, fee.account_id, fee.card_id) == (
            owner,
            card.account_id,
            card.id,
        )
    duplicate = db.transactions["txn_088219"]
    for identity, amount, status in [
        ("txn_088217", 80, "posted"),
        ("txn_088218", 30, "processing"),
    ]:
        refund = db.transactions[identity]
        assert refund.kind == "fee_refund"
        assert refund.original_transaction_id == duplicate.id
        assert (refund.customer_id, refund.account_id, refund.card_id) == (
            duplicate.customer_id,
            duplicate.account_id,
            duplicate.card_id,
        )
        assert (refund.amount, refund.status) == (amount, status)
    history = db.customers[owner].servicing_contract_history
    first, second, future = history
    assert first["avg_monthly_balance"] == first["free_min_balance"] == 10000
    assert second["avg_monthly_balance"] < second["free_min_balance"]
    assert second["monthly_turnover"] < second["free_min_turnover"]
    for row, identity in [
        (first, "txn_088214"),
        (second, "txn_088215"),
        (second, "txn_088219"),
    ]:
        fee = db.transactions[identity]
        assert row["billing_period"] == fee.billing_period
        assert row["revision_id"] == fee.tariff_revision
        assert row["qualifying_period"]["end"] < row["billing_period"]["start"]
    assert future["effective_from"] > db.today
    assert future["qualifying_period"]["end"] >= db.today
    assert "avg_monthly_balance" not in future
    assert "monthly_turnover" not in future
    assert (
        future["monthly_fee"],
        future["free_min_balance"],
        future["free_min_turnover"],
    ) == (299, 15000, 40000)
    consent = next(
        a.fee_refund_confirmation
        for a in db.client_answers[owner]
        if a.fee_refund_confirmation
    )
    assert consent["card_id"] == card.id and consent["confirmed"]
    assert sorted(
        (item["transaction_id"], item["amount"]) for item in consent["refunds"]
    ) == [("txn_088214", 199), ("txn_088219", 169)]
    assert (
        consent["total_amount"]
        == sum(item["amount"] for item in consent["refunds"])
        == 368
    )


# Explicitly retain the historical scenario version; composite tests live separately.
pytestmark = pytest.mark.usefixtures("fees_rewards_parent_scenarios")
