"""Allocation registers connect batch postings to individual reward claims."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RewardPaymentAllocation(BaseModel):
    """One positive allocation; its posting determines the economic direction."""

    model_config = ConfigDict(extra="forbid")

    id: str
    posting_id: str
    purchase_id: str
    amount: float = Field(gt=0)
    reverses_allocation_id: str | None = None


RewardPostingRole = Literal["credit", "reversal", "accounting_mirror"]


def allocated_reward_payments(db, customer_id, cutoff):
    """Net settled amounts by purchase, in kopecks; not exposed as an MCP answer.

    Only bank-origin registers are authoritative. All rows for one posting form
    a complete allocation, including unrelated purchases. A mirror is a record
    of another economic event, never an additional receipt. Broken authoritative
    input raises instead of silently producing a plausible balance.
    """
    from tau2.domains.banking_ru.reward_workflows import cents

    allocations = {}
    groups = {}
    for document in db.documents.values():
        if document.customer_id != customer_id or document.origin != "bank":
            continue
        for row in document.reward_payment_allocations:
            if row.id in allocations:
                raise ValueError("Duplicate reward allocation ID")
            allocations[row.id] = row
            groups.setdefault(row.posting_id, []).append(row)

    postings = {}
    expected_postings = {
        tx.id
        for tx in db.transactions.values()
        if tx.customer_id == customer_id and tx.reward_posting_role is not None
    }
    if expected_postings != set(groups):
        raise ValueError("Missing authoritative allocation register for batch posting")
    for posting_id, rows in groups.items():
        posting = db.transactions.get(posting_id)
        if (
            posting is None
            or posting.customer_id != customer_id
            or posting.reward_posting_role is None
            or posting.kind != "cashback"
            or posting.original_transaction_id is not None
        ):
            raise ValueError("Invalid batch reward posting")
        if sum(cents(row.amount) for row in rows) != cents(posting.amount):
            raise ValueError("Incomplete reward payment allocation")
        for row in rows:
            purchase = db.transactions.get(row.purchase_id)
            if (
                purchase is None
                or purchase.kind != "purchase"
                or purchase.customer_id != customer_id
                or purchase.account_id != posting.account_id
            ):
                raise ValueError("Invalid reward allocation owner or purchase")
        postings[posting_id] = posting

    def posting_time(posting):
        if not posting.posted_at:
            raise ValueError("Batch posting requires an exact posting timestamp")
        value = datetime.fromisoformat(posting.posted_at)
        if value.tzinfo is None:
            raise ValueError("Batch posting timestamp requires a timezone")
        return value

    def settled(posting):
        return posting.status == "posted" and (
            posting_time(posting)
            .astimezone(ZoneInfo("Europe/Moscow"))
            .date()
            .isoformat()
            <= cutoff
        )

    reversed_amounts = {}
    totals = {}
    for row in allocations.values():
        posting = postings[row.posting_id]
        if posting.reward_posting_role == "accounting_mirror":
            if row.reverses_allocation_id is not None:
                raise ValueError("Accounting mirror cannot reverse an allocation")
            continue
        if posting.reward_posting_role == "reversal":
            original = allocations.get(row.reverses_allocation_id)
            if (
                original is None
                or original.purchase_id != row.purchase_id
                or postings[original.posting_id].reward_posting_role != "credit"
            ):
                raise ValueError("Invalid reward reversal relationship")
            original_posting = postings[original.posting_id]
            if settled(posting):
                if not settled(original_posting) or posting_time(
                    original_posting
                ) > posting_time(posting):
                    raise ValueError("Reward reversal precedes its settled credit")
                reversed_amounts[original.id] = reversed_amounts.get(
                    original.id, 0
                ) + cents(row.amount)
                if reversed_amounts[original.id] > cents(original.amount):
                    raise ValueError("Reward reversals exceed original allocation")
            sign = -1
        else:
            if row.reverses_allocation_id is not None:
                raise ValueError("Credit cannot reverse an allocation")
            sign = 1
        if settled(posting):
            totals[row.purchase_id] = totals.get(row.purchase_id, 0) + sign * cents(
                row.amount
            )
    return totals


class PaymentBasisLine(BaseModel):
    """A bank decision's economic purpose, distinct from a payment's caption."""

    model_config = ConfigDict(extra="forbid")
    id: str
    purpose: Literal["cashback", "service_compensation"]
    amount: float = Field(gt=0, allow_inf_nan=False)
    purchase_id: str | None = None
    incident_id: str | None = None
    related_purchase_id: str | None = None
    reward_program: Literal["standard_cashback", "partner_cashback"] | None = None

    @field_validator("amount")
    @classmethod
    def whole_kopecks(cls, value):
        if Decimal(str(value)) * 100 != (Decimal(str(value)) * 100).to_integral_value():
            raise ValueError("Payment basis amounts require whole kopecks")
        return value


class PaymentBasis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_reference: str | None = None
    revision: int = Field(default=1, ge=1)
    id: str
    customer_id: str
    payment_id: str
    period: str
    status: Literal["final"] = "final"
    lines: list[PaymentBasisLine] = Field(min_length=1)


class PaymentAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    payment_id: str
    period: str
    revision: int = Field(default=1, ge=1)
    status: Literal["provisional", "reconciled"] = "provisional"
    basis_id: str | None = None
    lines: list[PaymentBasisLine] = Field(min_length=1)


def validate_payment_basis(db, basis):
    """Reject inconsistent authoritative data; provisional CRM rows never decide money."""
    from tau2.domains.banking_ru.reward_workflows import cents

    try:
        datetime.strptime(basis.period, "%Y-%m")
    except ValueError as exc:
        raise ValueError("ERR_PAYMENT_BASIS: Неверный расчётный период") from exc
    payment = db.transactions.get(basis.payment_id)
    if (
        payment is None
        or payment.customer_id != basis.customer_id
        or payment.kind != "compensation"
        or payment.original_transaction_id is not None
        or payment.reward_posting_role is not None
        or not payment.posted_at
        or sum(cents(x.amount) for x in basis.lines) != cents(payment.amount)
        or len({x.id for x in basis.lines}) != len(basis.lines)
    ):
        raise ValueError("ERR_PAYMENT_BASIS: Несогласованное основание выплаты")
    for line in basis.lines:
        if line.related_purchase_id is not None:
            related = db.transactions.get(line.related_purchase_id)
            if (
                related is None
                or related.kind != "purchase"
                or related.customer_id != basis.customer_id
            ):
                raise ValueError("ERR_PAYMENT_BASIS: Чужая связанная покупка")
        if line.purpose == "cashback":
            purchase = db.transactions.get(line.purchase_id)
            if (
                purchase is None
                or purchase.kind != "purchase"
                or purchase.customer_id != basis.customer_id
                or purchase.account_id != payment.account_id
                or purchase.date[:7] != basis.period
                or line.incident_id is not None
                or line.reward_program is None
            ):
                raise ValueError("ERR_PAYMENT_BASIS: Неверная покупка основания")
        else:
            incident = db.service_incidents.get(line.incident_id)
            if (
                line.reward_program is not None
                or line.purchase_id is not None
                or incident is None
                or incident.customer_id != basis.customer_id
            ):
                raise ValueError("ERR_PAYMENT_BASIS: Неверный инцидент основания")
    return payment


def basis_reward_payments(db, customer_id, cutoff):
    """Use only final bank purposes; cash and claim settlement are separate facts."""
    from tau2.domains.banking_ru.reward_workflows import cents

    totals, seen = {}, set()
    for basis in db.payment_bases.values():
        if basis.customer_id != customer_id:
            continue
        if basis.payment_id in seen:
            raise ValueError("ERR_PAYMENT_BASIS: Несколько оснований одной выплаты")
        seen.add(basis.payment_id)
        payment = validate_payment_basis(db, basis)
        when = datetime.fromisoformat(payment.posted_at)
        if when.tzinfo is None:
            raise ValueError("ERR_PAYMENT_BASIS: Не задан часовой пояс выплаты")
        if (
            payment.status == "posted"
            and when.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat() <= cutoff
        ):
            for line in basis.lines:
                if line.purpose == "cashback":
                    key = (line.purchase_id, line.reward_program)
                    totals[key] = totals.get(key, 0) + cents(line.amount)
    return totals


def require_reconciled_payments(db, customer_id, period, transaction_ids=None):
    """Known provisional allocations must be corrected before committing settlement."""
    from tau2.environment.tool import ToolExecutionRejected

    for allocation in db.payment_allocations.values():
        if allocation.customer_id != customer_id or allocation.period != period:
            continue
        candidates = [
            b
            for b in db.payment_bases.values()
            if b.payment_id == allocation.payment_id
            and b.customer_id == customer_id
            and b.period == period
        ]
        purchases = {
            line.purchase_id
            for b in candidates
            for line in b.lines
            if line.purpose == "cashback"
        }
        purchases.update(
            line.purchase_id for line in allocation.lines if line.purpose == "cashback"
        )
        if transaction_ids is not None and not purchases.intersection(transaction_ids):
            continue
        basis = db.payment_bases.get(allocation.basis_id)
        if (
            allocation.status != "reconciled"
            or basis is None
            or basis.customer_id != customer_id
            or basis.period != period
            or basis.payment_id != allocation.payment_id
            or allocation.lines != basis.lines
        ):
            raise ToolExecutionRejected(
                "ERR_PAYMENT_ALLOCATION_PENDING: Распределение выплаты требует сверки"
            )
        validate_payment_basis(db, basis)


def reconcile_payment(tools, allocation_id, basis_id, expected_revision, lines):
    from tau2.domains.banking_ru.cashback_state import revision
    from tau2.domains.banking_ru.reward_workflows import cents
    from tau2.environment.tool import ToolExecutionRejected

    allocation = tools.db.payment_allocations.get(allocation_id)
    if allocation is None:
        raise ValueError("ERR_NOT_FOUND: Распределение не найдено")
    tools._require_verified(allocation.customer_id)
    basis = tools.db.payment_bases.get(basis_id)
    if basis is None:
        raise ToolExecutionRejected("ERR_PAYMENT_BASIS: Основание не найдено")
    tools._require_verified(basis.customer_id)
    if (
        basis.customer_id != allocation.customer_id
        or basis.payment_id != allocation.payment_id
        or basis.period != allocation.period
    ):
        raise ToolExecutionRejected("ERR_PAYMENT_BASIS: Основание другой выплаты")
    lines = sorted(
        (PaymentBasisLine.model_validate(line) for line in lines),
        key=lambda line: line.id,
    )
    payment = tools.db.transactions.get(allocation.payment_id)
    if not lines or len({line.id for line in lines}) != len(lines):
        raise ToolExecutionRejected("ERR_INVALID_ARGUMENT")
    if payment is None or sum(cents(line.amount) for line in lines) != cents(
        payment.amount
    ):
        raise ToolExecutionRejected("ERR_INVALID_ARGUMENT")
    for line in lines:
        for purchase_id in (line.purchase_id, line.related_purchase_id):
            if purchase_id is not None:
                tx = tools.db.transactions.get(purchase_id)
                if tx is None or tx.customer_id != allocation.customer_id:
                    raise ToolExecutionRejected("ERR_FOREIGN_RESOURCE")
    for line in lines:
        if line.incident_id is not None:
            incident = tools.db.service_incidents.get(line.incident_id)
            if incident is None or incident.customer_id != allocation.customer_id:
                raise ToolExecutionRejected("ERR_FOREIGN_RESOURCE")
    validate_payment_basis(tools.db, basis)
    if (
        allocation.status == "reconciled"
        and allocation.basis_id == basis_id
        and allocation.lines == lines
        and expected_revision in {allocation.revision, allocation.revision - 1}
    ):
        return {
            **allocation.model_dump(),
            "state_revision": revision(
                tools.db, allocation.customer_id, allocation.period
            ),
        }
    if allocation.revision != expected_revision:
        raise ToolExecutionRejected(
            "ERR_VERSION_CONFLICT: Изменилась версия распределения"
        )

    def economic_lines(lines):
        return sorted(
            (
                x.id,
                x.purpose,
                x.amount,
                x.purchase_id or "",
                x.reward_program or "",
                x.incident_id or "",
            )
            for x in lines
        )

    changes_money = economic_lines(allocation.lines) != economic_lines(lines)
    allocation.lines = deepcopy(lines)
    allocation.status = "reconciled"
    allocation.basis_id = basis.id
    allocation.revision += 1
    from tau2.domains.banking_ru.cashback_state import advance

    current = (advance if changes_money else revision)(
        tools.db, allocation.customer_id, allocation.period
    )
    return {**allocation.model_dump(), "state_revision": current}
