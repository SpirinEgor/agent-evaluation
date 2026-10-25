"""Period-scoped banking settlement and optimistic state; no scenario branches."""

from copy import deepcopy

from tau2.domains.banking_ru.reward_workflows import (
    cashback_period,
    cents,
    money,
    standard_gaps,
)


def revision(db, customer_id, period):
    return db.cashback_state_revisions.get(f"{customer_id}:{period}")


def advance(db, customer_id, period):
    key = f"{customer_id}:{period}"
    if key in db.cashback_state_revisions:
        db.cashback_state_revisions[key] += 1
        capture_revision(db, customer_id, period)
    return revision(db, customer_id, period)


def capture_revision(db, customer_id, period):
    """Retain business constraints for deciding whether stale attempts were lawful."""
    current = revision(db, customer_id, period)
    if current is None:
        return
    db.cashback_revision_states.setdefault(f"{customer_id}:{period}", {}).setdefault(
        str(current),
        {
            "gaps": standard_gaps(db, customer_id, period),
            "permissions": {
                k: deepcopy(p)
                for k, p in db.cashback_adjustment_permissions.items()
                if p["customer_id"] == customer_id and p.get("period") == period
            },
            "claims": {
                c.id: {x.transaction_id: cents(x.amount) for x in c.reward_items}
                for c in db.cases.values()
                if c.customer_id == customer_id and c.reward_period == period
            },
        },
    )


def expected_state(db, customer_id, period, expected_revision):
    capture_revision(db, customer_id, period)
    return db.cashback_revision_states.get(f"{customer_id}:{period}", {}).get(
        str(expected_revision)
    )


def settlements(db, customer_id, period):
    """Pending metadata deliberately contains neither future amount nor purchase."""
    return [
        {
            k: deepcopy(s[k])
            for k in ["id", "customer_id", "period", "status", "ledger_ids"]
        }
        for s in db.cashback_settlements.values()
        if s["customer_id"] == customer_id and s["period"] == period
    ]


def state_guard(db, customer_id, period, expected_revision):
    if any(s["status"] == "pending" for s in settlements(db, customer_id, period)):
        return {
            "status": "settlement_pending",
            "customer_id": customer_id,
            "period": period,
            "current_revision": revision(db, customer_id, period),
        }
    current = revision(db, customer_id, period)
    if current is not None and expected_revision != current:
        return {
            "status": "state_changed",
            "customer_id": customer_id,
            "period": period,
            "expected_revision": expected_revision,
            "current_revision": current,
        }
    return None


def refresh(tools, customer_id, period):
    from tau2.domains.banking_ru.data_model import Transaction

    tools._get_customer(customer_id)
    tools._require_verified(customer_id)
    from tau2.domains.banking_ru.discovery_workflow import require_bank_acceptance

    require_bank_acceptance(
        tools,
        customer_id,
        [
            t.id
            for t in tools.db.transactions.values()
            if t.customer_id == customer_id
            and t.reward_discrepancy_type
            and cashback_period(tools.db, t, t.reward_program, period)
        ],
    )
    capture_revision(tools.db, customer_id, period)
    previous = revision(tools.db, customer_id, period)
    batches = [
        s
        for s in tools.db.cashback_settlements.values()
        if s["customer_id"] == customer_id
        and s["period"] == period
        and s["status"] == "pending"
    ]
    credits = [Transaction.model_validate(row) for s in batches for row in s["credits"]]
    if len({t.id for t in credits}) != len(credits):
        raise ValueError("ERR_INVALID_ARGUMENT: Повторное начисление")
    for t in credits:
        tx = tools._get_transaction(t.original_transaction_id)
        if (
            (t.customer_id, t.account_id, t.card_id)
            != (customer_id, tx.account_id, tx.card_id)
            or t.kind != "cashback"
            or t.status != "posted"
            or t.id in tools.db.transactions
            or cents(t.amount) <= 0
        ):
            raise ValueError("ERR_INVALID_ARGUMENT: Некорректное начисление")
        tools._get_account(t.account_id)
    updates = [
        u
        for u in tools.db.reward_resolution_offer_updates.values()
        if u["customer_id"] == customer_id
        and u["period"] == period
        and u["status"] == "pending"
    ]
    for update in updates:
        offer = tools.db.reward_resolution_offers.get(update["offer_id"])
        if (
            not offer
            or offer.customer_id != customer_id
            or offer.period != period
            or offer.status != "under_review"
        ):
            raise ValueError("ERR_INVALID_OFFER_DECISION")
    for t in credits:
        tools.db.transactions[t.id] = t
        account = tools.db.accounts[t.account_id]
        account.balance = money(cents(account.balance) + cents(t.amount))
    for s in batches:
        s.update(status="settled", ledger_ids=[row["id"] for row in s["credits"]])
    for update in updates:
        tools.db.reward_resolution_offers[update["offer_id"]].status = "approved"
        update["status"] = "applied"
    if batches or updates:
        advance(tools.db, customer_id, period)
    return {
        "status": "settled",
        "customer_id": customer_id,
        "period": period,
        "previous_revision": previous,
        "current_revision": revision(tools.db, customer_id, period),
        "changed_offer_ids": [u["offer_id"] for u in updates],
        "changed_transaction_ids": [t.id for t in credits],
        "changed_records": [
            {"id": t.id, "customer_id": t.customer_id} for t in credits
        ],
    }


def adjustment_status(tools, operation_id):
    op = tools.db.cashback_adjustment_operations.get(operation_id)
    if not op:
        raise ValueError("ERR_NOT_FOUND: Операция не найдена")
    tools._require_verified(op["customer_id"])
    return {
        **deepcopy(op["result"]),
        "state_revision": revision(tools.db, op["customer_id"], op["period"]),
    }


def close_reservations(db, permission_id, amount):
    from tau2.domains.banking_ru.data_model import CashbackAdjustmentReservation

    for case in db.cases.values():
        rows = []
        for row in case.adjustment_reservations:
            left = (
                cents(row.amount) - amount
                if row.permission_id == permission_id
                else cents(row.amount)
            )
            if left > 0:
                rows.append(
                    CashbackAdjustmentReservation(
                        **{**row.model_dump(), "amount": money(left)}
                    )
                )
        case.adjustment_reservations = rows
