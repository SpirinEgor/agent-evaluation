"""Shared multi-program semantics, independently supplied banking facts."""

import json
from pathlib import Path

import pytest

from tau2.domains.banking_ru import reward_workflows as rewards
from tau2.domains.banking_ru.data_model import BankingDB
from tau2.domains.banking_ru.tools import BankingTools

C = "gromov_a_1187"


@pytest.fixture
def db():
    raw = json.loads(Path("data/tau2/domains/banking_ru/db.json").read_text())
    account = raw["cards"]["card_1187"]["account_id"]
    raw["transactions"] = {
        k: v for k, v in raw["transactions"].items() if v["customer_id"] != C
    }
    raw["reward_contracts"] = {
        "rc_test": dict(
            id="rc_test",
            customer_id=C,
            account_id=account,
            product_code="cafe",
            card_ids=["card_1187", "card_test"],
            effective_from="2026-06-01",
            effective_to=None,
            rules_revision="multi-program-v1",
        )
    }
    cycle = dict(
        period="2026-07",
        program="standard_cashback",
        rules_revision="multi-program-v1",
        contract_id="rc_test",
        posting_from="2026-07-01",
        posting_to="2026-07-31",
        settlement_as_of="2026-08-28",
        limit=110,
    )
    raw["cashback_rules"][C]["cycles"] = [cycle]
    raw["cashback_rules"][C]["categories_history"] = [
        dict(
            effective_from="2026-06-01",
            effective_to="2026-06-30",
            categories=[dict(rate=0.1, mcc_codes=["5812"])],
        ),
        dict(
            effective_from="2026-07-01",
            effective_to="2026-07-31",
            categories=[dict(rate=0.05, mcc_codes=["5812"])],
        ),
    ]
    raw["promotions"] = {
        "promo_test": dict(
            id="promo_test",
            customer_id=C,
            code="CAFE",
            name="Cafe",
            reward=0,
            required_purchases=0,
            min_purchase_amount=0,
            valid_from="2026-07-01",
            valid_until="2026-07-31",
            product_code="cafe",
            program="partner_cashback",
            merchant="Кофемания",
            mcc_codes=["5812"],
            rate=0.1,
            per_purchase_cap=100,
            activation_required=True,
            replaces_standard=True,
        )
    }
    raw["promotion_activations"] = {
        "activation_test": dict(
            id="activation_test",
            customer_id=C,
            promotion_id="promo_test",
            activated_at="2026-07-26T11:00:00+03:00",
            revoked_at=None,
        )
    }
    rows = [
        ("C", "2026-06-30", "2026-07-01", 300, "18:00", 30, 0, 0),
        ("E", "2026-07-02", "2026-07-02", 200, "12:00", 3, 0, 0),
        ("A", "2026-07-25", "2026-07-25", 1200, "12:00", 20, 200, 5),
        ("B", "2026-07-26", "2026-07-26", 1400, "12:00", 47, 200, 0),
        ("D", "2026-07-26", "2026-07-27", 1400, "10:00", 5, 0, 0),
    ]
    for id, date, posted, amount, time, paid, refund, compensation in rows:
        tx = dict(
            id=id,
            customer_id=C,
            account_id=account,
            card_id="card_1187",
            date=date,
            posted_at=posted + "T15:00:00+03:00",
            purchased_at=date + "T" + time + ":00+03:00",
            amount=amount,
            merchant="Кофемания" if id in "ABD" else "Кафе",
            mcc="5812",
            kind="purchase",
            status="posted",
            reward_program="standard_cashback",
        )
        raw["transactions"][id] = tx
        for suffix, value, kind in [
            ("p", paid, "cashback"),
            ("r", refund, "refund"),
            ("c", compensation, "cashback"),
        ]:
            if value:
                raw["transactions"][id + suffix] = dict(
                    **{
                        **tx,
                        "id": id + suffix,
                        "kind": kind,
                        "amount": value,
                        "original_transaction_id": id,
                        "compensates_cashback": suffix == "c",
                    }
                )
    return BankingDB.model_validate(raw)


def test_multi_program_obligations(db):
    assert hasattr(rewards, "reward_obligations"), "multi-program calculator absent"
    result = rewards.reward_obligations(db, C, "2026-07")
    assert {
        k: (r["program"], r["entitlement"], r["paid"], r["gap"])
        for k, r in result.items()
    } == {
        "C": ("standard_cashback", 3000, 3000, 0),
        "E": ("standard_cashback", 1000, 300, 700),
        "A": ("standard_cashback", 5000, 2500, 2500),
        "B": ("partner_cashback", 10000, 4700, 5300),
        "D": ("standard_cashback", 2000, 500, 1500),
    }
    assert rewards.standard_gaps(db, C, "2026-07")["A"] == 2500


def test_raw_facts_and_posting_intersection(db):
    tools = BankingTools(db)
    db.session.verified_customers.append(C)
    assert hasattr(tools, "get_reward_contracts")
    assert len(tools.get_reward_contracts(C)) == 1
    assert len(tools.get_promotion_activations(C, "promo_test")) == 1
    page = tools.get_transactions(
        C,
        date_from="2026-07-01",
        posted_from="2026-07-01",
        posted_to="2026-07-31",
        limit=50,
    )
    assert "C" not in {x["id"] for x in page["items"]}
    page = tools.get_transactions(C, original_transaction_id="A", limit=50)
    assert {x["id"] for x in page["items"]} == {"Ap", "Ar", "Ac"}
    rules = rewards.rules_with_register(db, C).model_dump()
    assert "transaction_ids" not in rules["cycles"][0]
    assert "related_posted_ids" not in rules["cycles"][0]


def test_foreign_promotion_and_link_selector(db):
    tools = BankingTools(db)
    db.session.verified_customers.append(C)
    foreign = next(x for x in db.transactions.values() if x.customer_id != C)
    with pytest.raises(ValueError, match="FOREIGN"):
        tools.get_transactions(C, original_transaction_id=foreign.id)
    promo = db.promotions["promo_test"].model_copy(
        update={"id": "foreign", "customer_id": foreign.customer_id}
    )
    db.promotions["foreign"] = promo
    with pytest.raises(ValueError, match="FOREIGN"):
        tools.get_promotion_activations(C, "foreign")
