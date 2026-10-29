"""Typed bank offer and execution records."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class EligibilityRegistry(RootModel[dict]):
    """Preserve JSON scalar types of complete read-only registry responses."""


class ComponentEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["service_incident", "partner_confirmation"]
    transaction_id: str = Field(min_length=1)
    incident_kind: str = "activation_sync_failure"


class EligibilityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)


class ServiceIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    contract_id: str
    kind: str
    starts_at: str
    ends_at: str


class PartnerConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    contract_id: str
    receipt_id: str
    enrollment_card_id: str
    status: Literal["registered", "accepted", "rejected"]


class RewardResolutionComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    exclusive_group: str
    amount: float = Field(gt=0, allow_inf_nan=False)
    settlement_deduction: float = Field(
        default=0, ge=0, multiple_of=0.01, allow_inf_nan=False
    )
    settlement_deduction_rate: float = Field(default=0, ge=0, lt=1, allow_inf_nan=False)
    bank_cost: float = Field(ge=0, allow_inf_nan=False)
    partner_cost: float = Field(ge=0, allow_inf_nan=False)
    allocation_first_amount: float = Field(gt=0, allow_inf_nan=False)
    incompatible_with: list[str] = Field(default_factory=list)
    required_companions: list[list[str]] = Field(default_factory=list)
    pool_costs: dict[str, float] = Field(default_factory=dict)
    eligibility: ComponentEligibility | None = None


class RewardFundingPool(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source: Literal["bank", "partner"]
    amount: float = Field(ge=0, allow_inf_nan=False)


class RewardResolutionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    bank: float = Field(ge=0, allow_inf_nan=False)
    partner: float = Field(ge=0, allow_inf_nan=False)
    pools: list[RewardFundingPool] = Field(default_factory=list)
    shared: bool = False


class RewardResolutionOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    case_id: str
    period: str
    transaction_ids: list[str]
    version: int
    amount: float = Field(gt=0, allow_inf_nan=False)
    amount_basis: Literal["cumulative", "incremental"]
    status: Literal["approved", "under_review", "rejected"]
    rights: Literal["preserve", "full_settlement"]
    terms_id: str
    allocation_terms_id: str
    allocation_first_transaction_id: str
    allocation_first_amount: float = Field(gt=0, allow_inf_nan=False)
    valid_from: str
    valid_to: str
    components: list[RewardResolutionComponent] = Field(default_factory=list)
    selection_budget: RewardResolutionBudget | None = None


class RewardResolutionPreferences(BaseModel):
    id: str
    customer_id: str
    objective: Literal["maximize_additional_guaranteed_cash"]
    currency: Literal["RUB"] = "RUB"
    preserve_unpaid_claims: bool = True
    preserve_other_case_items: bool = True
    allow_new_products: bool = False
    customer_reported_offer_id: str | None = None
    customer_reported_status: str | None = None
    text: str


class RewardResolutionExecution(BaseModel):
    id: str
    customer_id: str
    case_id: str
    period: str
    transaction_ids: list[str]
    idempotency_key: str
    packet: dict
    result: dict
