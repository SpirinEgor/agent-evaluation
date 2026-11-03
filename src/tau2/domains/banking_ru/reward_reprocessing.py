"""Repair a processing binding and replay authorized contractual accruals."""

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
    if record.status == "binding_mismatch":
        record.bound_card_id = replacement_card_id
        record.status = "repaired"
        advance(tools.db, record.customer_id, record.period)
    return {
        **record.model_dump(),
        "state_revision": revision(tools.db, record.customer_id, record.period),
    }


def reprocess(tools, reprocessing_id, transaction_ids):
    from tau2.domains.banking_ru.data_model import Transaction
    from tau2.domains.banking_ru.discovery_workflow import require_bank_acceptance
    from tau2.domains.banking_ru.reward_resolution import _post_credits

    record = owned_case(tools, reprocessing_id)
    if len(set(transaction_ids)) != len(transaction_ids) or set(transaction_ids) != set(
        record.transaction_ids
    ):
        raise ToolExecutionRejected(
            "ERR_REPROCESS_SCOPE: Нужны все исходные покупки инцидента, без проводок-дублей"
        )
    if record.status == "reprocessed":
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
        if not row or row["program"] not in record.recovery_rates:
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
                (
                    Decimal(base) * Decimal(str(record.recovery_rates[row["program"]]))
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
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
    _post_credits(tools, credits)
    record.status = "reprocessed"
    record.ledger_ids = [t.id for t in credits]
    return {
        **record.model_dump(),
        "state_revision": advance(tools.db, record.customer_id, record.period),
        "ledger": [t.model_dump() for t in credits],
    }
