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
    return {
        "id": budget.id,
        "bank": budget.bank,
        "partner": budget.partner,
        **{
            f"remaining_{source}": money(cents(getattr(budget, source)) - used[source])
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
