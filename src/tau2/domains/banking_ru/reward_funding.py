"""Shared settlement reserves, scoped by owner, case and reward period."""

from tau2.domains.banking_ru.reward_workflows import cents, money


def reserve_key(offer):
    budget = offer.selection_budget
    if budget is not None and budget.shared:
        return offer.customer_id, offer.case_id, offer.period, budget.id
    return None


def reserve_members(db, offer):
    key = reserve_key(offer)
    return [
        row
        for row in db.reward_resolution_offers.values()
        if key is not None and reserve_key(row) == key
    ]


def reserve_snapshot(db, offer):
    key = reserve_key(offer)
    if key is None:
        return None
    budget = offer.selection_budget
    if any(row.selection_budget != budget for row in reserve_members(db, offer)):
        raise ValueError("ERR_INVALID_OFFER_DECISION: Несогласованный общий резерв")
    used = shared_spent(db, offer)
    held = shared_held(db, offer)
    return {
        "id": budget.id,
        "bank": budget.bank,
        "partner": budget.partner,
        "held_bank": money(held["bank"]),
        "held_partner": money(held["partner"]),
        "operations": reserve_operations(db, offer),
        "other_commitments": [dict(r) for r in other_commitments(db, offer)],
        **{
            f"remaining_{source}": money(
                cents(getattr(budget, source)) - used[source] - held[source]
            )
            for source in used
        },
    }


def shared_spent(db, offer):
    """Successful reserve charges in integer cents, independent of payment order."""
    key = reserve_key(offer)
    if key is None:
        return {"bank": 0, "partner": 0}
    used = {"bank": 0, "partner": 0}
    for operation in db.reward_resolution_executions.values():
        charge = operation.result.get("funding_charge", {})
        if (
            (operation.customer_id, operation.case_id, operation.period) == key[:3]
            and charge.get("reserve_id") == key[3]
            and operation.result.get("status") == "succeeded"
        ):
            for source in used:
                used[source] += cents(charge[source])
    return used


def reserve_operations(db, offer):
    """Current obligations, including zero obligations of final failed attempts."""
    key = reserve_key(offer)
    if key is None:
        return []
    rows = []
    for op in db.reward_resolution_executions.values():
        if (op.customer_id, op.case_id, op.period) != key[:3]:
            continue
        status = op.result.get("status")
        field = {
            "succeeded": "funding_charge",
            "execution_unknown": "funding_hold",
            "failed_final": "released_funding",
        }.get(status)
        amount = op.result.get(field, {}) if field else {}
        if amount.get("reserve_id") != key[3]:
            continue
        rows.append(
            {
                "id": op.id,
                "customer_id": op.customer_id,
                "case_id": op.case_id,
                "period": op.period,
                "offer_id": op.packet["offer_id"],
                "transaction_ids": list(op.transaction_ids),
                "status": status,
                **{
                    source: amount[source] if status != "failed_final" else 0
                    for source in ("bank", "partner")
                },
            }
        )
    return sorted(rows, key=lambda row: row["id"])


def shared_held(db, offer):
    """Pending obligations in integer cents; final outcomes never retain a hold."""
    rows = reserve_operations(db, offer)
    return {
        source: sum(
            cents(row[source]) for row in rows if row["status"] == "execution_unknown"
        )
        + sum(
            cents(row[source])
            for row in other_commitments(db, offer)
            if not row.get("released_" + source, False)
        )
        for source in ("bank", "partner")
    }


def other_commitments(db, offer):
    key = reserve_key(offer)
    if key is None:
        return []
    return [
        row
        for row in db.reward_funding_commitments.values()
        if (row["customer_id"], row["case_id"], row["period"], row["reserve_id"]) == key
    ]
