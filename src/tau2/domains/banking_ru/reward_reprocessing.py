"""Repair a processing binding and replay authorized contractual accruals."""

from copy import deepcopy
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from tau2.domains.banking_ru.cashback_state import advance, revision
from tau2.domains.banking_ru.processing_notes import with_processing_notes
from tau2.domains.banking_ru.reward_workflows import (
    cents,
    linked_posted,
    money,
    purchase_total,
    reward_obligations,
    standard_cycle,
)
from tau2.environment.tool import ToolExecutionRejected


def owned_case(tools, reprocessing_id):
    record = tools.db.reward_reprocessing_cases.get(reprocessing_id)
    if record is None:
        raise ToolExecutionRejected("ERR_NOT_FOUND: Инцидент обработки не найден")
    tools._require_verified(record.customer_id)
    return record


def list_cases(tools, customer_id):
    tools._get_customer(customer_id)
    tools._require_verified(customer_id)
    return [
        with_processing_notes(
            tools.db, r.model_dump(), customer_id, "reprocessing", r.id
        )
        for r in tools.db.reward_reprocessing_cases.values()
        if r.customer_id == customer_id
    ]


def repair(tools, reprocessing_id, contract_id, replacement_card_id):
    record = owned_case(tools, reprocessing_id)
    if not record.repair_allowed:
        raise ToolExecutionRejected(
            "ERR_BINDING_RESTRICTED: Изменение привязки ограничено по этому инциденту"
        )
    contract = tools.db.reward_contracts.get(contract_id)
    card = tools.db.cards.get(replacement_card_id)
    if (
        not contract
        or not card
        or contract.customer_id != record.customer_id
        or card.customer_id != record.customer_id
    ):
        raise ToolExecutionRejected(
            "ERR_FOREIGN_OWNER: Нет полномочий на договор или карту"
        )
    if (
        contract.id != record.contract_id
        or contract.replacement_card_id != replacement_card_id
    ):
        raise ToolExecutionRejected(
            "ERR_BINDING_MISMATCH: Нет преемственности договора"
        )
    for tid in record.transaction_ids:
        tx = tools._get_transaction(tid)
        if (
            tx.customer_id != record.customer_id
            or tx.account_id != contract.account_id
            or tx.card_id != replacement_card_id
            or not contract.replacement_effective_from
            or tx.date < contract.replacement_effective_from
            or tx.date < contract.effective_from
            or (contract.effective_to is not None and tx.date > contract.effective_to)
            or tx.card_id not in contract.card_ids
        ):
            raise ToolExecutionRejected(
                "ERR_BINDING_MISMATCH: Договор не применим к покупке"
            )
    if contract.binding_history:
        for tid in record.transaction_ids:
            tx = tools._get_transaction(tid)
            applicable = [
                item
                for item in contract.binding_history
                if item.valid_from <= tx.date
                and (item.valid_to is None or tx.date < item.valid_to)
            ]
            if len(applicable) != 1 or applicable[0].card_id != replacement_card_id:
                raise ToolExecutionRejected(
                    "ERR_BINDING_MISMATCH: Нет однозначной привязки на дату покупки"
                )
    if record.status in {"open", "binding_mismatch"}:
        if record.bound_card_id == replacement_card_id:
            raise ToolExecutionRejected(
                "ERR_BINDING_UNCHANGED: Привязка уже соответствует выбранной карте"
            )
        record.bound_card_id = replacement_card_id
        record.status = "repaired"
        advance(tools.db, record.customer_id, record.period)
    return {
        **record.model_dump(),
        "state_revision": revision(tools.db, record.customer_id, record.period),
    }


def allocation_register(tools, record):
    """Only authoritative, same-case economic allocations can be revised."""
    from tau2.domains.banking_ru.reward_payment_register import (
        allocated_reward_payments,
    )

    allocated_reward_payments(tools.db, record.customer_id, tools.db.today)
    return {
        row.id: row
        for document in tools.db.documents.values()
        if document.customer_id == record.customer_id and document.origin == "bank"
        for row in document.reward_payment_allocations
    }


def recovery_reversals(tools, record, method, credits):
    pools = [p for p in record.funding_pools if p.id == method.pool_id]
    if len(pools) != 1:
        raise ToolExecutionRejected("ERR_RECOVERY_POOL: Неоднозначный лимит метода")
    pool = pools[0]
    if set(pool.protected_minimums) - set(pool.allocation_ids):
        raise ToolExecutionRejected(
            "ERR_RECOVERY_POOL: Минимум относится к другой записи"
        )
    register = allocation_register(tools, record)
    if len(pool.allocation_ids) != len(set(pool.allocation_ids)) or len(
        pool.reversal_order
    ) != len(set(pool.reversal_order)):
        raise ToolExecutionRejected("ERR_RECOVERY_POOL: Повтор записи распределения")
    total = 0
    for ident in pool.allocation_ids:
        row = register.get(ident)
        if row is None or row.purchase_id not in record.transaction_ids:
            raise ToolExecutionRejected("ERR_RECOVERY_SCOPE: Запись вне поручения")
        posting = tools.db.transactions[row.posting_id]
        if (
            posting.status != "posted"
            or posting.reward_posting_role == "accounting_mirror"
            or (posting.posted_at or posting.date)[:10] > tools.db.today
        ):
            raise ToolExecutionRejected("ERR_RECOVERY_POOL: Нет проведённой суммы")
        sign = -1 if posting.reward_posting_role == "reversal" else 1
        total += sign * cents(row.amount)
    if total < 0 or total > cents(pool.limit):
        raise ToolExecutionRejected("ERR_RECOVERY_POOL: Исходный лимит несогласован")
    excess = max(0, total + sum(cents(c.amount) for c in credits) - cents(pool.limit))
    reversals = []
    for ident in pool.reversal_order:
        if ident not in pool.allocation_ids:
            raise ToolExecutionRejected("ERR_RECOVERY_SCOPE: Сторно вне лимита")
        row = register[ident]
        if tools.db.transactions[row.posting_id].reward_posting_role != "credit":
            raise ToolExecutionRejected("ERR_RECOVERY_POOL: Сторно требует начисление")
        already = sum(
            cents(r.amount)
            for r in register.values()
            if r.reverses_allocation_id == ident
            and tools.db.transactions[r.posting_id].status == "posted"
            and (tools.db.transactions[r.posting_id].posted_at or tools.db.today)[:10]
            <= tools.db.today
        )
        available = (
            cents(row.amount) - already - cents(pool.protected_minimums.get(ident, 0))
        )
        if available < 0:
            raise ToolExecutionRejected(
                "ERR_RECOVERY_POOL: Защищённый минимум превышает остаток"
            )
        amount = min(excess, available)
        if amount:
            reversals.append({"allocation_id": ident, "amount": money(amount)})
            excess -= amount
    if excess:
        raise ToolExecutionRejected(
            "ERR_RECOVERY_POOL: Недостаточно суммы для замещения"
        )
    return reversals, register


def confirm(tools, customer_id, request):
    record = owned_case(tools, request.reprocessing_id)
    if customer_id != record.customer_id:
        raise ToolExecutionRejected("ERR_FOREIGN_OWNER: Чужое поручение")
    if not any(m.id == request.method_id for m in record.methods):
        raise ToolExecutionRejected("ERR_RECOVERY_METHOD: Метод не найден")
    packet = request.model_dump(exclude={"type"})
    for key, selector in (
        ("payouts", "transaction_id"),
        ("reversals", "allocation_id"),
    ):
        packet[key] = sorted(packet[key], key=lambda row: row[selector])
        if len({r[selector] for r in packet[key]}) != len(packet[key]):
            raise ToolExecutionRejected("ERR_RECOVERY_SCOPE: Повтор позиции")
    register = allocation_register(tools, record)
    for row in packet["payouts"]:
        if row["transaction_id"] not in record.transaction_ids:
            raise ToolExecutionRejected("ERR_RECOVERY_SCOPE: Выплата вне поручения")
    for row in packet["reversals"]:
        source = register.get(row["allocation_id"])
        if source is None or source.purchase_id not in record.transaction_ids:
            raise ToolExecutionRejected("ERR_RECOVERY_SCOPE: Сторно вне поручения")
    # Consent accepts the stated effects; it is not a hidden calculation oracle.
    cid = next(
        (k for k, v in tools._reprocessing_consents.items() if v == packet), None
    )
    if cid is None:
        cid = f"reprocessing-confirmation-{len(tools._reprocessing_consents) + 1}"
        tools._reprocessing_consents[cid] = deepcopy(packet)
    return {
        "status": "confirmed",
        "customer_id": customer_id,
        "confirmation_id": cid,
        "data": packet,
        "answer": "Согласен на указанные начисления и замещение перечисленных сумм. Права на остаток обращения сохраняются.",
    }


def reprocess(
    tools, reprocessing_id, transaction_ids, method_id=None, confirmation_id=None
):
    from tau2.domains.banking_ru.data_model import Transaction
    from tau2.domains.banking_ru.discovery_workflow import require_bank_acceptance
    from tau2.domains.banking_ru.reward_resolution import _post_credits

    record = owned_case(tools, reprocessing_id)
    method = None
    if record.methods:
        matches = [m for m in record.methods if m.id == method_id]
        if len(matches) != 1:
            raise ToolExecutionRejected(
                "ERR_RECOVERY_METHOD: Укажите один доступный метод"
            )
        method = matches[0]
    elif method_id is not None or confirmation_id is not None:
        raise ToolExecutionRejected(
            "ERR_RECOVERY_METHOD: У инцидента нет вариантов метода"
        )
    if len(set(transaction_ids)) != len(transaction_ids) or set(transaction_ids) != set(
        record.transaction_ids
    ):
        raise ToolExecutionRejected(
            "ERR_REPROCESS_SCOPE: Нужны все исходные покупки инцидента, без проводок-дублей"
        )
    if record.status == "reprocessed":
        if record.selected_method_id != method_id:
            raise ToolExecutionRejected("ERR_RECOVERY_COMMITTED: Метод уже исполнен")
        return {
            **record.model_dump(),
            "state_revision": revision(tools.db, record.customer_id, record.period),
        }
    if record.status != "repaired":
        raise ToolExecutionRejected("ERR_BINDING_MISMATCH: Привязка не восстановлена")
    # Accepted settlement closes operational recalculation, even before final delivery.
    if any(
        op.customer_id == record.customer_id
        and op.period == record.period
        and set(op.transaction_ids).intersection(transaction_ids)
        and op.result.get("status") != "failed_final"
        for op in tools.db.reward_resolution_executions.values()
    ):
        raise ToolExecutionRejected(
            "ERR_REPROCESS_CLOSED: По покупке уже принято урегулирование"
        )
    case = tools.db.cases.get(record.case_id)
    if (
        not case
        or case.customer_id != record.customer_id
        or case.reward_period != record.period
        or case.review_status == "registered"
    ):
        raise ToolExecutionRejected(
            "ERR_REVIEW_REGISTERED: Перерасчёт передан специалистам"
        )
    require_bank_acceptance(
        tools, record.customer_id, transaction_ids, case_id=record.case_id
    )
    cycle = standard_cycle(tools.db, record.customer_id, record.period)
    obligations = reward_obligations(tools.db, record.customer_id, record.period)
    if cycle is None or obligations is None:
        raise ToolExecutionRejected(
            "ERR_REPROCESS_SCOPE: Нет расчётного договора периода"
        )
    credits = []
    for tid in sorted(transaction_ids):
        tx = tools._get_transaction(tid)
        row = obligations.get(tid)
        rates = method.recovery_rates if method else record.recovery_rates
        if not row or row["program"] not in rates:
            raise ToolExecutionRejected("ERR_REPROCESS_SCOPE: Нет условий перерасчёта")
        base = max(
            0,
            purchase_total(tools.db, tx)
            - sum(
                cents(r.amount)
                for r in linked_posted(tools.db, tx, cycle)
                if r.kind == "refund"
            ),
        )
        amount = min(
            row["gap"],
            int(
                (Decimal(base) * Decimal(str(rates[row["program"]]))).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            ),
        )
        ident = f"reward-reprocessing:{record.id}:{tid}"
        if (
            ident in tools.db.transactions
            or tools._get_account(tx.account_id).status != "active"
        ):
            raise ToolExecutionRejected(
                "ERR_REPROCESS_STATE: Проводка уже существует или счёт закрыт"
            )
        if amount:
            credits.append(
                Transaction(
                    id=ident,
                    customer_id=record.customer_id,
                    account_id=tx.account_id,
                    card_id=tx.card_id,
                    original_transaction_id=tid,
                    date=tools.db.today,
                    posted_at=tools.db.today + "T12:00:00+03:00",
                    amount=money(amount),
                    merchant="Перерасчёт после восстановления привязки",
                    kind="cashback",
                    status="posted",
                    reward_program=row["program"],
                )
            )
    reversal_transactions = []
    reversals = []
    register_document = None
    if method:
        from tau2.domains.banking_ru.data_model import Document
        from tau2.domains.banking_ru.reward_payment_register import (
            RewardPaymentAllocation,
        )

        reversals, register = recovery_reversals(tools, record, method, credits)
        packet = {
            "reprocessing_id": record.id,
            "method_id": method.id,
            "payouts": sorted(
                [
                    {"transaction_id": c.original_transaction_id, "amount": c.amount}
                    for c in credits
                ],
                key=lambda row: row["transaction_id"],
            ),
            "reversals": sorted(reversals, key=lambda row: row["allocation_id"]),
            "state_revision": revision(tools.db, record.customer_id, record.period),
        }
        if tools._reprocessing_consents.get(confirmation_id) != packet:
            raise ToolExecutionRejected(
                "ERR_RECOVERY_CONSENT: Нет согласия на точный действующий пакет"
            )
        allocations = []
        for row in reversals:
            source = register[row["allocation_id"]]
            old = tools.db.transactions[source.posting_id]
            if datetime.fromisoformat(old.posted_at) > datetime.fromisoformat(
                tools.db.today + "T12:00:00+03:00"
            ):
                raise ToolExecutionRejected(
                    "ERR_REPROCESS_STATE: Время исходной проводки позже расчётного среза"
                )
            ident = f"reward-reprocessing-reversal:{record.id}:{source.id}"
            if (
                ident in tools.db.transactions
                or tools._get_account(old.account_id).status != "active"
            ):
                raise ToolExecutionRejected("ERR_REPROCESS_STATE: Сторно недоступно")
            reversal_transactions.append(
                Transaction(
                    id=ident,
                    customer_id=record.customer_id,
                    account_id=old.account_id,
                    date=tools.db.today,
                    posted_at=tools.db.today + "T12:00:00+03:00",
                    amount=row["amount"],
                    merchant="Перераспределение лимита восстановления",
                    kind="cashback",
                    status="posted",
                    reward_posting_role="reversal",
                )
            )
            allocations.append(
                RewardPaymentAllocation(
                    id=ident + ":allocation",
                    posting_id=ident,
                    purchase_id=source.purchase_id,
                    amount=row["amount"],
                    reverses_allocation_id=source.id,
                )
            )
        if allocations:
            register_document = Document(
                id=f"reward-reprocessing-register:{record.id}",
                customer_id=record.customer_id,
                title="Реестр перераспределения восстановительного лимита",
                created_at=tools.db.today,
                body="Проведённые замещения согласно выбранному методу и согласию клиента.",
                origin="bank",
                case_id=record.case_id,
                reward_payment_allocations=allocations,
            )
            if register_document.id in tools.db.documents:
                raise ToolExecutionRejected(
                    "ERR_REPROCESS_STATE: Реестр уже существует"
                )
        changes = {}
        for tx in credits:
            changes[tx.account_id] = changes.get(tx.account_id, 0) + cents(tx.amount)
        for tx in reversal_transactions:
            changes[tx.account_id] = changes.get(tx.account_id, 0) - cents(tx.amount)
        if any(
            cents(tools.db.accounts[aid].balance) + delta < 0
            for aid, delta in changes.items()
        ):
            raise ToolExecutionRejected(
                "ERR_REPROCESS_BALANCE: Недостаточно средств для замещения"
            )
    # All monetary effects and the exact consent have been checked before mutation.
    _post_credits(tools, credits)
    for tx in reversal_transactions:
        tools.db.transactions[tx.id] = tx
        account = tools.db.accounts[tx.account_id]
        account.balance = money(cents(account.balance) - cents(tx.amount))
    if register_document:
        tools.db.documents[register_document.id] = register_document
    record.status = "reprocessed"
    record.selected_method_id = method_id
    record.ledger_ids = [t.id for t in credits + reversal_transactions]
    return {
        **record.model_dump(),
        "state_revision": advance(tools.db, record.customer_id, record.period),
        "ledger": [t.model_dump() for t in credits + reversal_transactions],
        "reversals": reversals,
    }
