"""Typed bank offer and execution records."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class EligibilityRegistry(RootModel[dict]):
    """Preserve JSON scalar types of complete read-only registry responses."""


class ComponentEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["service_incident", "partner_confirmation", "client_receipt"]
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
    status: Literal["preliminary", "confirmed"] = "confirmed"
    revision: int = Field(default=1, ge=1)
    superseded_by: str | None = None


class ServiceIncidentUpdate(BaseModel):
    """A final scope clarification published with a statement-scoped decision."""

    model_config = ConfigDict(extra="forbid")
    previous_id: str = Field(min_length=1)
    replacement: ServiceIncident


class PartnerConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    contract_id: str
    receipt_id: str
    enrollment_card_id: str
    status: Literal["registered", "accepted", "rejected"]
    revision: int = Field(default=1, ge=1)
    superseded_by: str | None = None


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
    fallback_for_offer_id: str | None = None


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
    minimum_payouts: dict[
        str, Annotated[float, Field(ge=0, multiple_of=0.01, allow_inf_nan=False)]
    ] = Field(default_factory=dict)
    minimum_payout_reduction_limits: dict[
        str, Annotated[float, Field(ge=0, multiple_of=0.01, allow_inf_nan=False)]
    ] = Field(default_factory=dict)
    text: str


class RewardResolutionSettlement(BaseModel):
    """Already validated posting intent; resolving never revalidates a new payment."""

    final_status: Literal["failed_final", "succeeded"]
    ledger: list[dict]
    total_amount: float
    funding_charge: dict | None = None
    retry_required: bool = False


class RewardResolutionExecution(BaseModel):
    id: str
    customer_id: str
    case_id: str
    period: str
    transaction_ids: list[str]
    idempotency_key: str
    packet: dict
    result: dict
    pending_settlement: RewardResolutionSettlement | None = None


class RewardProcessingEvent(BaseModel):
    """Immutable observation of the processor selection, not a diagnosis."""

    model_config = ConfigDict(extra="forbid")
    id: str
    transaction_id: str
    processed_at: str
    selected_card_id: str
    posted_reward: float = Field(ge=0, allow_inf_nan=False)


class RewardReprocessingPool(BaseModel):
    """A contractual recovery limit and its existing allocation register."""

    model_config = ConfigDict(extra="forbid")
    id: str
    limit: float = Field(ge=0, multiple_of=0.01, allow_inf_nan=False)
    allocation_ids: list[str] = Field(default_factory=list)
    reversal_order: list[str] = Field(default_factory=list)


class RewardReprocessingMethod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    pool_id: str
    recovery_rates: dict[str, Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]]


class RewardReprocessingCase(BaseModel):
    """Processing binding incident; correction rates are bank-authorized terms."""

    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    case_id: str
    contract_id: str
    period: str
    bound_card_id: str
    transaction_ids: list[str]
    recovery_rates: dict[str, Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]]
    status: Literal["open", "binding_mismatch", "repaired", "reprocessed"] = "open"
    processing_events: list[RewardProcessingEvent] = Field(default_factory=list)
    ledger_ids: list[str] = Field(default_factory=list)
    methods: list[RewardReprocessingMethod] = Field(default_factory=list)
    funding_pools: list[RewardReprocessingPool] = Field(default_factory=list)
    selected_method_id: str | None = None


class RewardResolutionCorrection(BaseModel):
    """A bank posting released by the first partial execution of an agreement."""

    model_config = ConfigDict(extra="forbid")
    transaction_id: str
    ledger_id: str
    amount: float = Field(gt=0, multiple_of=0.01, allow_inf_nan=False)
    allow_reallocation: bool = True
