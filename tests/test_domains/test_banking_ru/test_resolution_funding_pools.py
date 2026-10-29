"""Named funding budgets are independent limits, not extra payments."""

from types import SimpleNamespace as NS

import pytest


def offer():
    rows = [
        NS(
            id="x",
            exclusive_group="x",
            amount=0.12,
            bank_cost=0.06,
            partner_cost=0.06,
            allocation_first_amount=0.03,
            incompatible_with=[],
            pool_costs={"bank_a": 0.06, "partner_a": 0.06},
        ),
        NS(
            id="y",
            exclusive_group="y",
            amount=0.12,
            bank_cost=0.06,
            partner_cost=0.06,
            allocation_first_amount=0.03,
            incompatible_with=[],
            pool_costs={"bank_b": 0.06, "partner_b": 0.06},
        ),
    ]
    pools = [
        NS(id=k, source=k.split("_")[0], amount=0.06)
        for k in ["bank_a", "bank_b", "partner_a", "partner_b"]
    ]
    return NS(
        components=rows,
        selection_budget=NS(bank=0.12, partner=0.12, pools=pools),
        amount=0.24,
        transaction_ids=["a", "b"],
        allocation_first_transaction_id="a",
    )


def test_independent_pools_accept_full_and_smaller_legal_selections():
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    assert selected_amounts(offer(), ["x", "y"]) == (24, {"a": 6, "b": 18})
    assert selected_amounts(offer(), ["x"]) == (12, {"a": 3, "b": 9})


@pytest.mark.parametrize(
    "defect",
    ["bank_over", "partner_over", "unknown", "negative", "missing", "duplicate_pool"],
)
def test_invalid_pool_funding_rejected_despite_valid_global_budgets(defect):
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    o = offer()
    if defect == "bank_over":
        o.components[1].pool_costs = {"bank_a": 0.06, "partner_b": 0.06}
    if defect == "partner_over":
        o.components[1].pool_costs = {"bank_b": 0.06, "partner_a": 0.06}
    if defect == "unknown":
        o.components[1].pool_costs = {"unknown": 0.06, "partner_b": 0.06}
    if defect == "negative":
        o.components[1].pool_costs = {"bank_b": -0.06, "partner_b": 0.18}
    if defect == "missing":
        o.components[1].pool_costs = {}
    if defect == "duplicate_pool":
        o.selection_budget.pools.append(o.selection_budget.pools[0])
    with pytest.raises(ValueError):
        selected_amounts(o, ["x", "y"])


def shared_ready():
    from tau2.domains.banking_ru.reward_resolution_models import RewardResolutionOffer
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import (
        RA,
        RB,
        resolution_claim_ready,
    )

    e, _ = resolution_claim_ready()
    db = e.tools.db
    template = db.reward_resolution_offers.pop("claim-offer").model_dump()
    for name, tid, bank, partner in [
        ("X", RA, 9, 9),
        ("Z", RA, 10, 14),
        ("Y", RB, 13, 9),
        ("W", RB, 9, 5),
    ]:
        amount = bank + partner
        db.reward_resolution_offers[name] = RewardResolutionOffer.model_validate(
            {
                **template,
                "id": name,
                "transaction_ids": [tid],
                "amount": amount,
                "allocation_first_transaction_id": tid,
                "allocation_first_amount": amount,
                "selection_budget": {
                    "id": "shared-reserve",
                    "bank": 23,
                    "partner": 20,
                    "shared": True,
                },
                "components": [
                    {
                        "id": name,
                        "exclusive_group": name,
                        "amount": amount,
                        "bank_cost": bank,
                        "partner_cost": partner,
                        "allocation_first_amount": amount,
                    }
                ],
            }
        )
    return e


def shared_pay(e, name, *, key=None, revision=None):
    from tau2.domains.banking_ru.cashback_state import revision as current_revision
    from tau2.domains.banking_ru.reward_resolution import execute
    from tau2.domains.banking_ru.reward_workflows import money, standard_gaps
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import (
        PERIOD,
        RCASE,
        C,
    )

    db = e.tools.db
    o = db.reward_resolution_offers[name]
    tid = o.transaction_ids[0]
    gaps = standard_gaps(db, C, PERIOD)
    claims = {x.transaction_id: x.amount for x in db.cases[RCASE].reward_items}
    # The complete reserve scope includes the second purchase before CRM amendment.
    for row in db.reward_resolution_offers.values():
        if row.selection_budget and row.selection_budget.shared:
            for scoped_tid in row.transaction_ids:
                claims[scoped_tid] = money(gaps[scoped_tid])
    for op in db.reward_resolution_executions.values():
        for paid_tid in op.transaction_ids:
            claims[paid_tid] = money(gaps[paid_tid])
    claims[tid] = money(gaps[tid] - round(o.amount * 100))
    args = dict(
        offer_id=name,
        offer_version=o.version,
        payouts=[dict(transaction_id=tid, amount=o.amount)],
        total_amount=o.amount,
        case_id=RCASE,
        component_ids=[name],
    )
    rev = current_revision(db, C, PERIOD) if revision is None else revision
    cid = "consent-" + (key or name)
    e.tools._resolution_consents[cid] = {
        **args,
        "resulting_claims": [
            dict(transaction_id=t, amount=a) for t, a in claims.items()
        ],
        "state_revision": rev,
    }
    return execute(
        e.tools,
        **args,
        confirmation_id=cid,
        expected_revision=rev,
        idempotency_key=key or name,
    )


@pytest.mark.parametrize("order", [("X", "Y"), ("Y", "X")])
def test_shared_reserve_keeps_both_payments_and_actual_unamended_claims(order):
    from tau2.domains.banking_ru.reward_resolution import list_offers
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import (
        PERIOD,
        RA,
        RB,
        RCASE,
        C,
    )

    e = shared_ready()
    initial_claims = {
        x.transaction_id: x.amount for x in e.tools.db.cases[RCASE].reward_items
    }
    assert RB not in initial_claims
    assert initial_claims["txn_804205"] == 7
    results = [shared_pay(e, name) for name in order]
    assert sum(r["total_amount"] for r in results) == 40
    assert {r["id"] for r in results} == {
        f"resolution:{RCASE}:X",
        f"resolution:{RCASE}:Y",
    }
    assert len(e.tools.db.reward_resolution_executions) == 2
    assert (
        sum(
            x["amount"]
            for x in e.tools._resolution_consents["consent-" + order[1]][
                "resulting_claims"
            ]
        )
        == 45
    )
    assert {
        x.transaction_id: x.amount for x in e.tools.db.cases[RCASE].reward_items
    } == initial_claims
    assert results[0]["funding_charge"]["reserve_id"] == "shared-reserve"
    assert next(row for row in list_offers(e.tools, C, RCASE) if row["id"] == "X")[
        "funding_reserve"
    ] == {
        "id": "shared-reserve",
        "bank": 23,
        "partner": 20,
        "remaining_bank": 1,
        "remaining_partner": 2,
        "held_bank": 0,
        "held_partner": 0,
        "operations": [
            {
                "id": f"resolution:{RCASE}:X",
                "customer_id": C,
                "case_id": RCASE,
                "period": PERIOD,
                "offer_id": "X",
                "transaction_ids": [RA],
                "status": "succeeded",
                "bank": 9,
                "partner": 9,
            },
            {
                "id": f"resolution:{RCASE}:Y",
                "customer_id": C,
                "case_id": RCASE,
                "period": PERIOD,
                "offer_id": "Y",
                "transaction_ids": [RB],
                "status": "succeeded",
                "bank": 13,
                "partner": 9,
            },
        ],
    }


def test_shared_overdraw_is_atomic_recoverable_and_replay_does_not_recharge():
    from copy import deepcopy

    from tau2.environment.tool import ToolExecutionRejected

    e = shared_ready()
    first = shared_pay(e, "Z")
    before = e.tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected, match="ERR_SHARED_FUNDING_EXHAUSTED"):
        shared_pay(e, "Y")
    assert e.tools.db.model_dump() == before
    assert not e.tools._resolution_violations
    assert shared_pay(e, "W")["total_amount"] == 14
    before = deepcopy(e.tools.db.model_dump())
    # Replay the exact saved packet, even though the period revision advanced.
    from tau2.domains.banking_ru.reward_resolution import execute

    op = next(
        op
        for op in e.tools.db.reward_resolution_executions.values()
        if op.idempotency_key == "Z"
    )
    assert (
        execute(e.tools, **op.packet, expected_revision=2, idempotency_key="Z") == first
    )
    assert e.tools.db.model_dump() == before


def test_shared_stale_attempt_does_not_charge():
    e = shared_ready()
    shared_pay(e, "X")
    before = e.tools.db.model_dump()
    assert shared_pay(e, "Y", revision=2)["status"] == "state_changed"
    assert e.tools.db.model_dump() == before
    assert shared_pay(e, "Y", key="fresh")["status"] == "succeeded"


def test_shared_funding_pending_and_refresh_propagate_to_siblings_atomically():
    from tau2.domains.banking_ru.cashback_state import pending_funding
    from tau2.domains.banking_ru.reward_resolution import list_offers
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import (
        CASE,
        A,
        C,
        funding_ready,
        refresh,
    )

    e = funding_ready(late=False)
    db = e.tools.db
    source = db.reward_resolution_offers["funding-offer"]
    source.selection_budget = source.selection_budget.model_copy(
        update={"shared": True}
    )
    db.reward_resolution_offer_updates["funding-event"]["new_selection_budget"][
        "shared"
    ] = True
    sibling = source.model_copy(
        deep=True,
        update={
            "id": "sibling",
            "transaction_ids": [A],
            "allocation_first_transaction_id": A,
            "amount": 18,
            "allocation_first_amount": 18,
        },
    )
    db.reward_resolution_offers[sibling.id] = sibling
    assert pending_funding(db, sibling)
    assert all(x["funding_status"] == "pending" for x in list_offers(e.tools, C, CASE))
    result = refresh(e)
    assert set(result["changed_offer_ids"]) == {source.id, sibling.id}
    assert db.reward_resolution_offers[sibling.id].version == 2
    assert db.reward_resolution_offers[sibling.id].amount == 18
    assert db.reward_resolution_offers[sibling.id].selection_budget.bank == 23
    assert not pending_funding(db, db.reward_resolution_offers[sibling.id])


def test_shared_repeated_offer_with_new_key_cannot_overwrite_or_charge():
    e = shared_ready()
    shared_pay(e, "X")
    before = e.tools.db.model_dump()
    with pytest.raises(ValueError, match="Уже выбран способ"):
        shared_pay(e, "X", key="another-key")
    assert e.tools.db.model_dump() == before


@pytest.mark.parametrize("defect", ["version", "budget", "spent"])
def test_shared_refresh_rejects_inconsistent_group_before_posting_or_decision(defect):
    from copy import deepcopy

    from tau2.domains.banking_ru.reward_resolution_models import (
        RewardResolutionExecution,
    )
    from tests.test_domains.test_banking_ru.test_cashback_settlement_state import (
        A,
        funding_ready,
    )

    e = funding_ready()
    db = e.tools.db
    source = db.reward_resolution_offers["funding-offer"]
    source.selection_budget.shared = True
    db.reward_resolution_offer_updates["funding-event"]["new_selection_budget"][
        "shared"
    ] = True
    sibling = source.model_copy(
        deep=True, update={"id": "sibling", "transaction_ids": [A]}
    )
    db.reward_resolution_offers[sibling.id] = sibling
    if defect == "version":
        sibling.version += 1
    elif defect == "budget":
        sibling.selection_budget.bank += 1
    else:
        db.reward_resolution_executions["prior"] = RewardResolutionExecution(
            id="prior",
            customer_id=source.customer_id,
            case_id=source.case_id,
            period=source.period,
            transaction_ids=[A],
            idempotency_key="prior",
            packet={"offer_id": sibling.id},
            result={
                "status": "succeeded",
                "funding_charge": {
                    "reserve_id": source.selection_budget.id,
                    "bank": 1,
                    "partner": 0,
                },
            },
        )
    before = deepcopy(db.model_dump())
    from tau2.domains.banking_ru.cashback_state import refresh as refresh_direct

    with pytest.raises(ValueError, match="ERR_INVALID_OFFER_DECISION"):
        refresh_direct(e.tools, source.customer_id, source.period)
    assert db.model_dump() == before


def test_shared_usage_and_pending_are_scoped_to_owner_case_period_and_reserve():
    from tau2.domains.banking_ru.cashback_state import pending_funding
    from tau2.domains.banking_ru.reward_funding import reserve_snapshot

    e = shared_ready()
    shared_pay(e, "X")
    db = e.tools.db
    paid = db.reward_resolution_offers["X"]
    for field, value in [
        ("customer_id", "other-owner"),
        ("case_id", "other-case"),
        ("period", "2026-08"),
    ]:
        other = paid.model_copy(deep=True, update={"id": "other", field: value})
        db.reward_resolution_offers[other.id] = other
        db.reward_resolution_offer_updates["other"] = {
            "offer_id": other.id,
            "customer_id": other.customer_id,
            "period": other.period,
            "previous_version": 1,
            "status": "pending",
        }
        assert not pending_funding(db, paid)
        assert reserve_snapshot(db, other)["remaining_bank"] == 23
    other = paid.model_copy(deep=True, update={"id": "other"})
    other.selection_budget.id = "other-reserve"
    db.reward_resolution_offers[other.id] = other
    assert reserve_snapshot(db, other)["remaining_bank"] == 23
