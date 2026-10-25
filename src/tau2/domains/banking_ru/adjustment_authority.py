"""Explicit bank authority and atomic, replayable compensation ledger."""

from copy import deepcopy

from tau2.domains.banking_ru.reward_workflows import (
    cashback_period,
    cents,
    money,
    reward_obligations,
    standard_gaps,
)


def list_permissions(tools, customer_id: str) -> list[dict]:
    tools._get_customer(customer_id)
    tools._require_verified(customer_id)
    from tau2.domains.banking_ru.cashback_state import revision

    return [
        {
            **deepcopy(p),
            "state_revision": revision(tools.db, customer_id, p.get("period")),
        }
        for p in tools.db.cashback_adjustment_permissions.values()
        if p["customer_id"] == customer_id
    ]


def execute_adjustment(
    tools,
    permission_id: str,
    transaction_id: str,
    amount: float,
    idempotency_key=None,
    expected_revision=None,
) -> dict:
    """Validate all dependencies before atomically consuming permission and crediting."""
    from tau2.domains.banking_ru.data_model import Transaction

    tx = tools._get_transaction(transaction_id)
    tools._require_verified(tx.customer_id)
    amount_cents = cents(amount)
    signature = (permission_id, transaction_id, amount_cents)
    from tau2.domains.banking_ru.cashback_state import (
        adjustment_status,
        advance,
        close_reservations,
        expected_state,
        revision,
        state_guard,
    )

    # Resolve original execution before checking consumed permission or stale revision.
    for op in tools.db.cashback_adjustment_operations.values():
        if (
            idempotency_key is not None
            and op["customer_id"] == tx.customer_id
            and op["idempotency_key"] == idempotency_key
        ):
            if op["signature"] != list(signature):
                raise ValueError("ERR_INVALID_ARGUMENT: Ключ другого запроса")
            return adjustment_status(tools, op["id"])
    if idempotency_key is not None:
        if not idempotency_key.strip():
            raise ValueError("ERR_INVALID_ARGUMENT: Пустой ключ")
        if idempotency_key in tools._adjustment_keys:
            previous, result = tools._adjustment_keys[idempotency_key]
            if previous != signature:
                raise ValueError("ERR_INVALID_ARGUMENT: Ключ другого запроса")
            return deepcopy(result)
    if any(
        c.review_status == "registered"
        and c.customer_id == tx.customer_id
        and any(x.transaction_id == transaction_id for x in c.review_items)
        for c in tools.db.cases.values()
    ):
        raise ValueError("ERR_REVIEW_REGISTERED")
    p = tools.db.cashback_adjustment_permissions.get(permission_id)
    if p is None:
        raise ValueError("ERR_PERMISSION_NOT_FOUND: Разрешение не найдено")
    if any(
        op.customer_id == tx.customer_id
        and tx.id in op.transaction_ids
        and op.period == p.get("period")
        for op in tools.db.reward_resolution_executions.values()
    ):
        tools._resolution_violations.add(tx.customer_id)
        raise ValueError(
            "ERR_ADJUSTMENT_NOT_AUTHORIZED: Уже выбран другой способ урегулирования"
        )
    if p["used"]:
        tools._excess_adjustment_attempts.add(permission_id)
    remaining = cents(p.get("remaining_amount", 0 if p["used"] else p["max_amount"]))
    if (
        p["customer_id"] != tx.customer_id
        or p["transaction_id"] != tx.id
        or p["program"] not in {"standard_cashback", "partner_cashback"}
        or p["status"] != "approved"
        or p["used"]
        or not p["valid_from"] <= tools.db.today <= p["valid_to"]
        or amount_cents <= 0
        or amount_cents > remaining
        or amount_cents > cents(p["max_amount"])
    ):
        raise ValueError("ERR_ADJUSTMENT_NOT_AUTHORIZED: Нет действующего полномочия")
    period = p.get("period")
    if period and not cashback_period(tools.db, tx, p["program"], period):
        raise ValueError("ERR_ADJUSTMENT_NOT_AUTHORIZED: Покупка другого периода")
    obligations = (
        reward_obligations(tools.db, tx.customer_id, period) if period else None
    )
    if obligations is not None and p["program"] != obligations.get(tx.id, {}).get(
        "program"
    ):
        raise ValueError("ERR_ADJUSTMENT_NOT_AUTHORIZED: Полномочие другой программы")
    gap = standard_gaps(tools.db, tx.customer_id, period) if period else None
    historical = expected_state(tools.db, tx.customer_id, period, expected_revision)
    if historical:
        gap = historical["gaps"]
    if gap is not None and amount_cents > gap.get(tx.id, 0):
        raise ValueError("ERR_DOUBLE_COMPENSATION: Превышена существующая недоплата")
    for case in tools.db.cases.values():
        if (
            case.customer_id != tx.customer_id
            or case.reward_program not in {p["program"], "cashback_reconciliation"}
            or case.status != "open"
        ):
            continue
        claimed = sum(
            cents(x.amount) for x in case.reward_items if x.transaction_id == tx.id
        )
        if historical:
            claimed = historical["claims"].get(case.id, {}).get(tx.id, 0)
        if gap is not None and claimed + amount_cents > gap.get(tx.id, 0):
            raise ValueError("ERR_DOUBLE_COMPENSATION: Уже заявлено в расследовании")
    blocked = state_guard(tools.db, tx.customer_id, period, expected_revision)
    if blocked:
        return blocked
    account = tools._get_account(tx.account_id)
    if account.status != "active":
        raise ValueError("ERR_ACCOUNT_CLOSED: Счёт закрыт")
    credit_id = "cashback-adjustment:" + permission_id
    # The first execution keeps its historical ID; later partial executions are distinct.
    if cents(p.get("used_amount", 0)):
        credit_id += ":used:" + str(cents(p["used_amount"]))
    if credit_id in tools.db.transactions:
        raise ValueError("ERR_ADJUSTMENT_NOT_AUTHORIZED: Выплата уже проведена")
    credit = Transaction(
        id=credit_id,
        customer_id=tx.customer_id,
        account_id=tx.account_id,
        card_id=tx.card_id,
        original_transaction_id=tx.id,
        date=tools.db.today,
        posted_at=tools.db.today + "T12:00:00+03:00"
        if obligations is not None
        else None,
        reward_program=p["program"] if obligations is not None else None,
        amount=money(amount_cents),
        merchant="Корректировка по разрешению " + permission_id,
        kind="cashback",
        status="posted",
        channel="online",
    )
    new_balance = money(cents(account.balance) + amount_cents)
    new_used_amount = money(cents(p.get("used_amount", 0)) + amount_cents)
    new_remaining = money(remaining - amount_cents)
    # No later validation or fallible service call after these writes.
    tools.db.transactions[credit_id] = credit
    account.balance = new_balance
    tx.cashback_granted = True
    p["used_amount"] = new_used_amount
    p["remaining_amount"] = new_remaining
    p["used"] = p["remaining_amount"] == 0
    close_reservations(tools.db, permission_id, amount_cents)
    current = advance(tools.db, tx.customer_id, period)
    result = {
        "id": permission_id,
        "transaction_id": tx.id,
        "ledger_transaction_id": credit_id,
        "program": p["program"],
        "granted": money(amount_cents),
        "account_balance": account.balance,
        "status": "posted",
    }
    if idempotency_key is not None:
        tools._adjustment_keys[idempotency_key] = (signature, deepcopy(result))
    if revision(tools.db, tx.customer_id, period) is not None:
        operation_id = "adjustment-operation:" + credit_id
        unknown = bool(
            tools.db.cashback_execution_profiles.get(tx.customer_id, {}).get(
                "first_adjustment_unknown"
            )
        ) and not any(
            op["customer_id"] == tx.customer_id
            for op in tools.db.cashback_adjustment_operations.values()
        )
        result.update(
            id=operation_id,
            status="succeeded",
            operation_id=operation_id,
            permission_id=permission_id,
            idempotency_key=idempotency_key,
            customer_id=tx.customer_id,
            period=period,
            state_revision=current,
        )
        tools.db.cashback_adjustment_operations[operation_id] = {
            "id": operation_id,
            "customer_id": tx.customer_id,
            "period": period,
            "idempotency_key": idempotency_key,
            "signature": list(signature),
            "result": deepcopy(result),
        }
        if unknown:
            return {
                "status": "execution_unknown",
                "operation_id": operation_id,
                "idempotency_key": idempotency_key,
                "id": operation_id,
                "customer_id": tx.customer_id,
            }
    return result
