"""Allocation registers connect batch postings to individual reward claims."""

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


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
