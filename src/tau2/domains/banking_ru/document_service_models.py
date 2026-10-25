"""Shared typed metadata for official case-document delivery."""

from typing import Literal

from pydantic import BaseModel, Field


class DocumentContact(BaseModel):
    id: str
    customer_id: str
    channel: Literal["verified_sms", "verified_email"]
    address: str
    verified: bool


class DocumentDelivery(BaseModel):
    id: str
    customer_id: str
    case_id: str
    document_id: str
    contact_id: str
    version: str
    channel: str
    status: Literal["delivered"] = "delivered"


class DocumentServiceProfile(BaseModel):
    """Scenario metadata, never a task-ID switch in the tool catalog."""

    confirmation_readiness: Literal["ready", "preparing"] = "ready"
    confirmation_version: str = "1"
    delivery_outcome_unknown: bool = False
    transient_delivery_failures: int = Field(default=0, ge=0)
