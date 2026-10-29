"""Period-scoped banking settlement and optimistic state; no scenario branches."""

from copy import deepcopy

from tau2.domains.banking_ru.reward_funding import (
    reserve_key,
    reserve_members,
    shared_spent,
)
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


def pending_funding(db, offer):
    """Only metadata is public until the accepted statement is reconciled."""
    member_ids = {row.id for row in reserve_members(db, offer)} or {offer.id}
    return any(
        update["offer_id"] in member_ids
        and update["customer_id"] == offer.customer_id
        and update["period"] == offer.period
        and update["status"] == "pending"
        and "previous_version" in update
        for update in db.reward_resolution_offer_updates.values()
    )


def funding_replacement(tools, offer, update):
    """Validate a statement-scoped decision completely before changing any state."""
    from tau2.domains.banking_ru.discovery_workflow import statement_needed
    from tau2.domains.banking_ru.reward_resolution_models import RewardResolutionOffer

    statement = tools.db.reward_statement_registrations.get(update.get("statement_id"))
    case = tools.db.cases.get(offer.case_id)
    needed = {
        tid
        for tid in offer.transaction_ids
        if statement_needed(tools._get_transaction(tid))
    }
    if (
        type(update.get("previous_version")) is not int
        or type(update.get("new_version")) is not int
        or update["previous_version"] != offer.version
        or update["new_version"] != offer.version + 1
        or offer.status != "approved"
        or not needed
        or not statement
        or statement.get("id") != update["statement_id"]
        or statement.get("status") != "accepted"
        or statement.get("customer_id") != offer.customer_id
        or statement.get("case_id") != offer.case_id
        or not needed
        <= set(statement.get("transaction_ids", []))
        <= set(offer.transaction_ids)
        or not case
        or case.customer_id != offer.customer_id
        or case.reward_period != offer.period
        or any(
            tools._get_transaction(tid).customer_id != offer.customer_id
            for tid in offer.transaction_ids
        )
        or any(
            operation.packet.get("offer_id") == offer.id
            for operation in tools.db.reward_resolution_executions.values()
        )
    ):
        raise ValueError("ERR_INVALID_OFFER_DECISION: Неверная версия или заявление")
    try:
        replacement = RewardResolutionOffer.model_validate(
            {
                **offer.model_dump(),
                "version": update["new_version"],
                "amount": update["new_amount"],
                "selection_budget": update["new_selection_budget"],
            }
        )
        if replacement.selection_budget is None:
            raise ValueError("Отсутствует бюджет финансирования")
        if reserve_key(offer) is not None:
            members = reserve_members(tools.db, offer)
            if (
                reserve_key(replacement) != reserve_key(offer)
                or any(shared_spent(tools.db, offer).values())
                or any(
                    row.version != offer.version
                    or row.selection_budget != offer.selection_budget
                    for row in members
                )
            ):
                raise ValueError("Неверный общий резерв финансирования")
        elif replacement.selection_budget.shared:
            raise ValueError("Решение не может менять область резерва")
        return replacement
    except (KeyError, ValueError) as exc:
        raise ValueError(
            "ERR_INVALID_OFFER_DECISION: Неверные условия финансирования"
        ) from exc


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
    if len({u["offer_id"] for u in updates}) != len(updates):
        raise ValueError("ERR_INVALID_OFFER_DECISION: Повтор решения для предложения")
    funding = [u for u in updates if "previous_version" in u]
    if any(
        not isinstance(u.get("id"), str) or not u["id"].strip() for u in funding
    ) or len({u["id"] for u in funding}) != len(funding):
        raise ValueError("ERR_INVALID_OFFER_DECISION: Неверный идентификатор события")
    replacements = {}
    for update in updates:
        offer = tools.db.reward_resolution_offers.get(update["offer_id"])
        if not offer or offer.customer_id != customer_id or offer.period != period:
            raise ValueError("ERR_INVALID_OFFER_DECISION")
        if "previous_version" in update:
            replacement = funding_replacement(tools, offer, update)
            group = reserve_members(tools.db, offer) or [offer]
            for member in group:
                if member.id in replacements:
                    raise ValueError(
                        "ERR_INVALID_OFFER_DECISION: Повтор решения для резерва"
                    )
                replacements[member.id] = (
                    replacement
                    if member.id == offer.id
                    else member.model_copy(
                        deep=True,
                        update={
                            "version": replacement.version,
                            "selection_budget": replacement.selection_budget.model_copy(
                                deep=True
                            ),
                        },
                    )
                )
        elif offer.status != "under_review":
            raise ValueError("ERR_INVALID_OFFER_DECISION")
    capture_revision(tools.db, customer_id, period)
    for t in credits:
        tools.db.transactions[t.id] = t
        account = tools.db.accounts[t.account_id]
        account.balance = money(cents(account.balance) + cents(t.amount))
    for s in batches:
        s.update(status="settled", ledger_ids=[row["id"] for row in s["credits"]])
    tools.db.reward_resolution_offers.update(replacements)
    for update in updates:
        if update["offer_id"] in replacements:
            tools.db.reward_resolution_offers[update["offer_id"]] = replacements[
                update["offer_id"]
            ]
        else:
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
        "changed_offer_ids": list(
            dict.fromkeys([u["offer_id"] for u in updates] + list(replacements))
        ),
        **(
            {
                "funding_decisions": [
                    {
                        "event_id": u["id"],
                        "offer_id": u["offer_id"],
                        "statement_id": u["statement_id"],
                        "previous_version": u["previous_version"],
                        "new_version": u["new_version"],
                    }
                    for u in funding
                ]
            }
            if funding
            else {}
        ),
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
