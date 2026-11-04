"""Provenanced case correspondence exposed alongside authorized business reads.

Notes are observations, never executable rules or inputs to payment calculations.
Availability follows persisted business events, not the agent or task identity.
"""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProcessingNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    case_id: str
    subject_type: Literal[
        "case", "payment", "reprocessing", "offer", "resolution", "reply"
    ]
    subject_id: str
    available_after: Literal["opened", "statement_accepted", "partial_execution"]
    author: str
    source_ref: str
    created_at: str
    text: str
    authority: Literal["correspondence"] = "correspondence"
    revision: int = 1
    supersedes: str | None = None
    recommended_evidence_id: str | None = None


def with_processing_notes(db, row, customer_id, subject_type, subject_id):
    """Called only after the containing resource's normal ownership checks."""
    notes = []
    for note in db.processing_notes.values():
        if (note.customer_id, note.subject_type, note.subject_id) != (
            customer_id,
            subject_type,
            subject_id,
        ):
            continue
        case = db.cases.get(note.case_id)
        if case is None or case.customer_id != customer_id:
            continue
        if note.available_after == "statement_accepted" and not any(
            r.get("customer_id") == customer_id
            and r.get("case_id") == note.case_id
            and r.get("status") == "accepted"
            for r in db.reward_statement_registrations.values()
        ):
            continue
        if note.available_after == "partial_execution" and not any(
            op.customer_id == customer_id
            and op.case_id == note.case_id
            and (
                note.subject_type != "resolution"
                or op.packet.get("offer_id") == note.subject_id
            )
            and op.result.get("retry_required")
            and op.result.get("ledger")
            for op in db.reward_resolution_executions.values()
        ):
            continue
        notes.append(note.model_dump(exclude={"available_after"}))
    result = deepcopy(row)
    if notes:
        result["processing_notes"] = notes
    return result
