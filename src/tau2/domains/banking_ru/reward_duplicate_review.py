"""Scoped bank review of a third-party duplicate-payment allegation.

Correspondence supplies evidence identifiers, never authority to release money.
The event is published after partial execution; reads themselves do not release it.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tau2.environment.tool import ToolExecutionRejected


class RewardDuplicateReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    customer_id: str
    case_id: str
    period: str
    trigger_offer_id: str
    transaction_id: str
    reward_program: str
    external_reference: str
    source_document_id: str
    import_document_id: str
    revision: int = Field(default=1, ge=1)
    status: Literal["queued", "pending", "cleared", "duplicate_confirmed"] = "queued"
    renew_basis_id: str | None = None
    decision_basis_id: str | None = None
    decision_basis_revision: int | None = None


def validate_trigger(db, offer):
    """Malformed scenario events must fail before any money is posted."""
    for review in db.reward_duplicate_reviews.values():
        if review.trigger_offer_id != offer.id:
            continue
        if review.renew_basis_id is not None:
            basis = db.payment_bases.get(review.renew_basis_id)
            if (
                basis is None
                or basis.customer_id != offer.customer_id
                or basis.period != offer.period
            ):
                raise ToolExecutionRejected(
                    "ERR_REVIEW_SCOPE: Основание обновления не принадлежит проверке"
                )
        if (
            review.customer_id != offer.customer_id
            or review.case_id != offer.case_id
            or review.period != offer.period
            or review.transaction_id not in offer.transaction_ids
            or any(
                ident not in db.documents
                or db.documents[ident].customer_id != offer.customer_id
                or db.documents[ident].case_id != offer.case_id
                for ident in (review.source_document_id, review.import_document_id)
            )
        ):
            raise ToolExecutionRejected(
                "ERR_REVIEW_SCOPE: Несогласованная область события"
            )


def activate(db, offer):
    """Publish a scheduled bank review in the same transaction as partial execution."""
    for review in db.reward_duplicate_reviews.values():
        if (
            review.status == "queued"
            and review.trigger_offer_id == offer.id
            and review.customer_id == offer.customer_id
            and review.case_id == offer.case_id
            and review.period == offer.period
        ):
            if review.renew_basis_id is not None:
                basis = db.payment_bases[review.renew_basis_id]
                basis.revision += 1
            review.status = "pending"
            review.revision += 1


def current_status(db, review):
    if review.status not in {"cleared", "duplicate_confirmed"}:
        return review.status
    basis = db.payment_bases.get(review.decision_basis_id)
    if basis is None or basis.revision != review.decision_basis_revision:
        return "pending"
    return review.status


def public_reviews(db, customer_id, case_id):
    return [
        {**r.model_dump(exclude={"trigger_offer_id"}), "status": current_status(db, r)}
        for r in db.reward_duplicate_reviews.values()
        if r.customer_id == customer_id
        and r.case_id == case_id
        and r.status != "queued"
    ]


def require_unrestricted(db, customer_id, transaction_ids):
    blocked = [
        r.id
        for r in db.reward_duplicate_reviews.values()
        if r.customer_id == customer_id
        and r.transaction_id in transaction_ids
        and current_status(db, r) in {"pending", "duplicate_confirmed"}
    ]
    if blocked:
        raise ToolExecutionRejected(
            "ERR_REWARD_REVIEW_PENDING: Выплата по затронутой покупке ограничена; проверки "
            "оснований разрешены. Проверки: " + ", ".join(sorted(blocked))
        )


def resolve(tools, review_id, basis_id, expected_revision, basis_revision):
    from tau2.domains.banking_ru.cashback_state import advance, revision
    from tau2.domains.banking_ru.reward_payment_register import validate_payment_basis

    db = tools.db
    review = db.reward_duplicate_reviews.get(review_id)
    if review is None:
        raise ToolExecutionRejected("ERR_NOT_FOUND: Проверка не найдена")
    tools._require_verified(review.customer_id)
    basis = db.payment_bases.get(basis_id)
    if basis is None:
        raise ToolExecutionRejected("ERR_PAYMENT_BASIS: Основание не найдено")
    tools._require_verified(basis.customer_id)
    if (
        basis.customer_id != review.customer_id
        or basis.period != review.period
        or basis.external_reference != review.external_reference
        or basis.revision != basis_revision
    ):
        raise ToolExecutionRejected(
            "ERR_REVIEW_EVIDENCE: Другая область или версия основания"
        )
    payment = validate_payment_basis(db, basis)
    if payment.status != "posted":
        raise ToolExecutionRejected("ERR_REVIEW_EVIDENCE: Нет проведённого платежа")
    if current_status(db, review) in {"cleared", "duplicate_confirmed"}:
        if (
            review.decision_basis_id == basis_id
            and review.decision_basis_revision == basis_revision
            and expected_revision in {review.revision, review.revision - 1}
        ):
            return {
                **review.model_dump(),
                "state_revision": revision(db, review.customer_id, review.period),
            }
        raise ToolExecutionRejected(
            "ERR_VERSION_CONFLICT: Решение относится к другой версии"
        )
    if current_status(db, review) != "pending" or review.revision != expected_revision:
        raise ToolExecutionRejected(
            "ERR_VERSION_CONFLICT: Проверка ещё не опубликована или версия устарела"
        )
    duplicate = any(
        line.purpose == "cashback"
        and line.purchase_id == review.transaction_id
        and line.reward_program == review.reward_program
        for line in basis.lines
    )
    review.status = "duplicate_confirmed" if duplicate else "cleared"
    review.decision_basis_id = basis_id
    review.decision_basis_revision = basis_revision
    review.revision += 1
    return {
        **review.model_dump(),
        "state_revision": advance(db, review.customer_id, review.period),
    }
