"""Domain-neutral facts used to justify claims at their observation time."""

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class OperationFact(BaseModel):
    """One result-backed fact; it attests knowledge, not policy compliance.

    Receipt controls availability; optional execution_index controls snapshot
    freshness when an earlier executed read is delivered after a newer write.
    For invalidation markers, resources constrain matching resource selectors;
    empty resources mean every resource of this family. invalidation_scope adds
    stable attribute constraints (e.g. owner). Unknown attributes conservatively
    match a fence; a known different attribute excludes the resource. Markers
    never attest a positive value or invent an affected resource.
    """

    family: str
    kind: Literal["current_state", "completed_action", "accepted_request", "historical"]
    resources: dict[str, str]
    values: dict[str, str]
    amount: Decimal | None = None
    call_id: str
    requestor: str
    received_position: int
    execution_index: int | None = None
    invalidated_fields: list[str] = Field(default_factory=list)
    invalidation_scope: dict[str, str] = Field(default_factory=dict)

    @field_validator("amount", mode="before")
    @classmethod
    def finite_money(cls, value):
        """Normalize decimal money without accepting booleans or non-finite data."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise ValueError("Amount must be finite decimal money")
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("Amount must be finite decimal money") from exc
        if not result.is_finite():
            raise ValueError("Amount must be finite decimal money")
        return result


class ClientClaim(BaseModel):
    """A finite extracted assertion at the predicate's actual emission time."""

    family: str
    kind: str
    resources: dict[str, str]
    values: dict[str, str]
    emitted_position: int
    channel: str
    text: str
    amount: Decimal | None = None
    unresolved_reason: str | None = None

    _finite_amount = field_validator("amount", mode="before")(
        OperationFact.finite_money.__func__
    )


class ClaimFinding(BaseModel):
    """Machine-readable violation or explicitly unresolved interpretation."""

    reason: str
    family: str
    channel: str
    emitted_position: int
    witness_call_ids: list[str]
    disposition: Literal["violation", "needs_review"]


def current_state_facts_at(
    facts: list[OperationFact], position: int
) -> list[OperationFact]:
    """Project scalar current state available by a position, preserving history.

    Each family/resource/field is independent. Completed/accepted/historical
    observations stay in the input history and never become current implicitly.
    Global/attribute-scoped family-field fences also apply to old snapshots first
    delivered after the effect. Known other owners are excluded; unknown owners
    are conservative matches. Freshly executed reads recover only their fields.
    """
    latest = {}
    fences = []
    for fact in sorted(facts, key=lambda item: item.received_position):
        if fact.kind != "current_state" or fact.received_position > position:
            continue
        freshness = (
            fact.execution_index
            if fact.execution_index is not None
            else fact.received_position
        )
        if fact.invalidated_fields:
            fences.append((freshness, fact))
        for field in fact.values:
            key = (fact.family, tuple(sorted(fact.resources.items())), field)
            previous = latest.get(key)
            if previous is None or freshness >= previous[0]:
                latest[key] = (freshness, fact)
    # Stable attributes are sibling scalar observations of the same resource.
    # Metadata first delivered after an effect may exclude a known other owner;
    # it cannot make an old affected balance a positive witness.
    attributes = {}
    for (family, resources, field), (_, fact) in latest.items():
        attributes.setdefault((family, resources), {})[field] = fact.values[field]
    result = []
    for (family, resources, field), (freshness, fact) in latest.items():
        context = {**attributes[(family, resources)], **dict(resources)}
        invalidated = any(
            fence.family == family
            and field in fence.invalidated_fields
            and fence_freshness >= freshness
            and all(
                context.get(key) in (None, expected)
                for key, expected in {
                    **fence.resources,
                    **fence.invalidation_scope,
                }.items()
            )
            for fence_freshness, fence in fences
        )
        if not invalidated:
            result.append(
                fact.model_copy(update={"values": {field: fact.values[field]}})
            )
    return result
