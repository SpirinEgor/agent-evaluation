"""Declarative final-state obligations supplement semantic DB comparison."""

from typing import Any

from tau2.data_model.tasks import DBStateCheck


def state_check_failures(
    db_snapshot: dict[str, Any], checks: list[DBStateCheck], *, banking_contract=None
) -> list[str]:
    """Check fields in every row selected by an explicit collection and scope.

    Missing data fails closed. Empty scoped collections pass universally;
    existence and the complete expected outcome remain part of DB comparison.
    This reads final state only, never tool history or environment assertions.
    """
    failures = []
    missing = object()
    for check in checks:
        collection = db_snapshot.get(check.collection)
        if not isinstance(collection, dict):
            failures.append(check.check_id)
            continue
        for row in collection.values():
            if not isinstance(row, dict):
                failures.append(check.check_id)
                break
            if not all(
                row.get(key, missing) == value for key, value in check.where.items()
            ):
                continue
            if row.get(check.field, missing) != check.equals:
                failures.append(check.check_id)
                break
    policy = (
        banking_contract.model_dump(exclude_none=True)
        if hasattr(banking_contract, "model_dump")
        else banking_contract
    ) or {}
    referral = policy.get("case_referral")
    if referral and not _referral_state_matches(db_snapshot, referral):
        failures.append("case_referral_state")
    return failures


def _referral_state_matches(db, policy):
    """Terminal referral relation; case IDs themselves are omitted from DB hash."""
    cases = [
        c
        for c in db.get("cases", {}).values()
        if c.get("customer_id") == policy["customer_id"]
    ]
    scoped = [
        c
        for c in cases
        if c.get("category") in {"merchant_investigation", "other", "escalation"}
        and c.get("transaction_id") == policy["transaction_id"]
        and c.get("amount") == policy["amount"]
        and c.get("status") == "open"
        and c.get("created_at") == db.get("today")
    ]
    handoffs = [
        c
        for c in cases
        if c.get("category") == "escalation"
        and (c.get("escalation_request") or {}).get("type") == "card_claim_referral"
        and c.get("transaction_id") == policy["transaction_id"]
        and any(c.get("case_id") == statement.get("id") for statement in scoped)
        and c.get("amount") is None
        and c.get("status") == "open"
        and c.get("created_at") == db.get("today")
    ]
    return (
        len(scoped) == 1
        and len(handoffs) <= 1
        and len(cases) == len(scoped) + len(handoffs)
    )
