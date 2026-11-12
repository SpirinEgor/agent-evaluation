"""Explicit identity selection for the all 61 fixed bank cases, without an LLM."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from tau2.domains.banking_ru.closure_signature import closure_document, document_hash
from tau2.domains.banking_ru.data_model import CashbackInvestigationItem, Customer
from tau2.domains.banking_ru.file_pilot import FileBankingTools, FileResponse
from tau2.domains.banking_ru.reward_resolution_models import EligibilityEvidence
from tau2.environment.tool import ToolExecutionRejected
from tau2.environment.toolkit import ToolType, is_tool


class IdentityRequest(BaseModel):
    """Choose the identity fact the client is asked to supply."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["identity"]
    method: Literal["code_word", "birth_date"] = Field(
        description="Кодовое слово или дата рождения, названные самим клиентом"
    )


class RestructuringDocumentsRequest(BaseModel):
    """Ask about income documents for an explicit loan, without text routing."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["restructuring_documents"]


class TariffChangeConfirmationRequest(BaseModel):
    """Confirm the actual bank price and next-period start for a selected tariff."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["tariff_change_confirmation"]
    new_tariff_id: str = Field(min_length=1)


class InternetOperationsConfirmationRequest(BaseModel):
    """Confirm the requested card setting; never route by free question text."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["internet_operations_confirmation"]
    card_last4: str = Field(pattern=r"^\d{4}$")
    enabled: bool


class SubscriptionSelectionRequest(BaseModel):
    """Ask which service profile the client's cancellation instruction covers."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["subscription_selection"]
    merchant_id: str = Field(min_length=1)


class OperationsRequest(BaseModel):
    """Ask the active scenario's client which incident operations they recognise."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["confirm_operations"]


class DeviceRecognitionRequest(BaseModel):
    """Ask the current client which of their listed devices they recognise."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["device_recognition"]


class UtilityPaymentAccountsRequest(BaseModel):
    """Ask for the erroneous and intended supplier personal accounts."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["utility_payment_accounts"]


class OperationConfirmationRequest(BaseModel):
    """Request a protected payment confirmation, without handling a payment OTP."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["operation_confirmation"]
    amount: float = Field(gt=0, allow_inf_nan=False)


class TransactionSelectionRequest(BaseModel):
    """Ask the client for the date or receipt of an ambiguous purchase."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["transaction_selection"]
    merchant: str = Field(min_length=1)
    amount: float = Field(gt=0, allow_inf_nan=False)
    date: str | None = None
    receipt: str | None = None


class CaseClarificationRequest(BaseModel):
    """Select a declared client fact or consent by topic, never by prose matching."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["case_clarification"]
    topic: Literal[
        "partial_execution",
        "third_party_message",
        "existing_case",
        "third_party_code",
        "promotion_activation",
        "request_scope",
        "resolution_preferences",
        "receipt_scope",
    ]


class ClientActionRequest(BaseModel):
    """Execute a previously given deterministic customer action."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["client_action"]
    tool_name: str
    arguments: dict


class RewardResolutionConfirmationRequest(BaseModel):
    """Present a concrete settlement package for consent, not bank approval."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["reward_resolution_confirmation"]
    payout_tolerance: float = Field(
        default=0, ge=0, multiple_of=0.01, allow_inf_nan=False
    )
    fallback_operation_id: str | None = None
    resolution_operation_id: str | None = None
    offer_id: str
    offer_version: int
    payouts: list[CashbackInvestigationItem] = Field(min_length=1)
    total_amount: float = Field(gt=0, allow_inf_nan=False)
    case_id: str
    resulting_claims: list[CashbackInvestigationItem] = Field(min_length=1)
    state_revision: int
    component_ids: list[str] = Field(default_factory=list)
    eligibility_evidence: dict[str, EligibilityEvidence] = Field(default_factory=dict)


class RewardReprocessingReversal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allocation_id: str
    amount: float = Field(gt=0, multiple_of=0.01, allow_inf_nan=False)


class RewardReprocessingConfirmationRequest(BaseModel):
    """Exact credits and reversal effects shown to the customer before recovery."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["reward_reprocessing_confirmation"]
    reprocessing_id: str
    method_id: str
    payouts: list[CashbackInvestigationItem] = Field(min_length=1)
    reversals: list[RewardReprocessingReversal] = Field(default_factory=list)
    state_revision: int


class RewardResolutionMinimumChangeRequest(BaseModel):
    """Ask to change a purchase minimum independently of settlement consent."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["reward_resolution_minimum_change"]
    case_id: str = Field(min_length=1)
    minimum_amount: float = Field(ge=0, multiple_of=0.01, allow_inf_nan=False)


class FeeRefundConfirmationRequest(BaseModel):
    """Present an explicit set of fee corrections for client consent."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["fee_refund_confirmation"]
    card_id: str = Field(min_length=1)
    refunds: list[CashbackInvestigationItem] = Field(min_length=1)
    total_amount: float = Field(gt=0, allow_inf_nan=False)
    state_version: str | None = None
    state_versions: dict[str, str] = Field(default_factory=dict)


class DocumentChannelConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["document_channel_confirmation"]
    contact_id: str = Field(min_length=1)


class AccountClosureSignatureRequest(BaseModel):
    """Present the account-closure document in the declared protected app."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["account_closure_signature"]
    channel: Literal["bank_app"]


class LoanRepaymentConfirmationRequest(BaseModel):
    """Protected-app instruction, never a payment OTP sent to support."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["loan_repayment_confirmation"]
    amount: float = Field(gt=0, allow_inf_nan=False)
    mode: Literal["reduce_payment", "reduce_term"]
    date: str
    quote_id: str


ClientRequest = Annotated[
    IdentityRequest
    | ClientActionRequest
    | TariffChangeConfirmationRequest
    | RestructuringDocumentsRequest
    | InternetOperationsConfirmationRequest
    | OperationsRequest
    | SubscriptionSelectionRequest
    | DeviceRecognitionRequest
    | UtilityPaymentAccountsRequest
    | OperationConfirmationRequest
    | LoanRepaymentConfirmationRequest
    | AccountClosureSignatureRequest
    | TransactionSelectionRequest
    | CaseClarificationRequest
    | RewardResolutionConfirmationRequest
    | RewardReprocessingConfirmationRequest
    | RewardResolutionMinimumChangeRequest
    | FeeRefundConfirmationRequest
    | DocumentChannelConfirmationRequest,
    Field(discriminator="type"),
]
REQUEST_ADAPTER = TypeAdapter(ClientRequest)


class StructuredIdentityBankingTools(FileBankingTools):
    """Client receipts are replayable but do not change the bank DB."""

    def __init__(self, db, corpus):
        super().__init__(db, corpus)
        self._identity_answers: dict[str, dict] = {}
        self._operation_answers: dict[str, dict] = {}
        self._restructuring_answers: dict[str, dict] = {}
        self._tariff_answers: dict[str, dict] = {}
        self._closure_answers: dict[str, dict] = {}

    @is_tool(ToolType.READ, mutates_state=True)
    def ask_client(
        self,
        customer_id: str,
        request: ClientRequest,
        question: str = "",
        transaction_id: str | None = None,
        loan_id: str | None = None,
        account_id: str | None = None,
        payout_account_id: str | None = None,
    ) -> FileResponse | str:
        """Запросить сведения у клиента с обязательным явным типом запроса.

        Вид запроса и его обязательные поля определены входной схемой.
        Вопрос формируется из структурированных данных. Свободный question
        не поддерживается. Ответ фиксирует факты или волеизъявление клиента,
        а не банковское полномочие и не исполнение последующей операции.

        Подпись заявления в приложении возвращает документ, его хеш,
        состояние подписи и идентификатор подтверждения; код подписи не выдаётся.
        Отказ, отсутствие ответа или соглашения не являются подписью.
        Идентификационный ответ содержит answer_id и data.credential.
        Подтверждения распоряжений относятся к указанным объектам, суммам,
        условиям и версиям. Изменение клиентского минимума и согласие на выплату
        являются разными волеизъявлениями. Ответ о документах отражает намерение
        их предоставить, а не факт получения банком. Результат клиентского
        действия содержит его квитанцию, но не подтверждает банковское принятие.

        Args:
            customer_id: Идентификатор найденного клиента.
            request: Явный тип запроса из закрытого набора.
            question: Историческое поле; непустое значение не допускается.
            transaction_id: Своя операция для operation_confirmation или изменения минимума.
            loan_id: Свой кредит только для loan_repayment_confirmation.
            account_id: Свой счёт только для loan_repayment_confirmation.
            payout_account_id: Свой счёт для остатка, только account_closure_signature.

        Returns:
            Структурированный ответ identity либо текст обычного уточнения.

        Raises:
            ValueError: Неверный запрос, неизвестный клиент или лишний question для identity.
        """
        self._get_customer(customer_id)
        request = REQUEST_ADAPTER.validate_python(request)
        if isinstance(request, ClientActionRequest):
            if question or any(
                v is not None
                for v in [transaction_id, loan_id, account_id, payout_account_id]
            ):
                raise ValueError("ERR_INVALID_CLIENT_ACTION")
            return FileResponse(
                self.perform_customer_action(
                    customer_id, request.tool_name, request.arguments
                )
            )
        if isinstance(request, RewardReprocessingConfirmationRequest):
            self._require_verified(customer_id)
            if question or any(
                v is not None
                for v in (transaction_id, loan_id, account_id, payout_account_id)
            ):
                raise ValueError("ERR_INVALID_ARGUMENT: Нужен только пакет request")
            from tau2.domains.banking_ru.reward_reprocessing import confirm

            return FileResponse(confirm(self, customer_id, request))
        if isinstance(request, RewardResolutionConfirmationRequest):
            self._require_verified(customer_id)
            if question or any(
                v is not None
                for v in (transaction_id, loan_id, account_id, payout_account_id)
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: Согласие принимает только пакет request"
                )
            from tau2.domains.banking_ru.reward_resolution import confirm

            return FileResponse(confirm(self, customer_id, request))
        if isinstance(request, RewardResolutionMinimumChangeRequest):
            self._require_verified(customer_id)
            if (
                question
                or transaction_id is None
                or any(v is not None for v in (loan_id, account_id, payout_account_id))
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: Нужны request и отдельный transaction_id"
                )
            from tau2.domains.banking_ru.reward_resolution import change_minimum

            return FileResponse(
                change_minimum(self, customer_id, transaction_id, request)
            )
        if isinstance(request, CaseClarificationRequest):
            self._require_verified(customer_id)
            if question or any(
                value is not None
                for value in [transaction_id, loan_id, account_id, payout_account_id]
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: case_clarification принимает только topic"
                )
            if request.topic == "receipt_scope":
                answers = [
                    a
                    for a in self.db.client_answers.get(customer_id, [])
                    if a.receipt_scope
                ]
                if len(answers) != 1:
                    return FileResponse({"status": "unknown", "data": None})
                declaration = dict(answers[0].receipt_scope)
                case = self.db.cases.get(declaration.get("case_id"))
                if case is None or case.customer_id != customer_id:
                    raise ToolExecutionRejected(
                        "ERR_RECEIPT_SCOPE: Несогласованное обращение"
                    )
                declaration.update(
                    customer_id=customer_id, status="accepted", source="client_answer"
                )
                self.db.client_receipt_declarations[declaration["id"]] = declaration
                return FileResponse(
                    {
                        "status": "answered",
                        "data": declaration,
                        "text": answers[0].answer,
                    }
                )
            if request.topic == "resolution_preferences":
                preferences = self.db.reward_resolution_preferences.get(customer_id)
                return FileResponse(
                    dict(
                        customer_id=customer_id,
                        request_type=request.type,
                        status="answered" if preferences else "unknown",
                        data=(
                            preferences.model_dump(
                                exclude={"minimum_payout_reduction_limits"}
                            )
                            if preferences
                            else None
                        ),
                    )
                )
            sources = [
                answer
                for answer in self.db.client_answers.get(customer_id, [])
                if answer.clarification_topic == request.topic
            ]
            if not sources:
                return FileResponse({"status": "unknown", "data": None})
            if len(sources) != 1:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: неоднозначный фиксированный ответ"
                )
            declared = {}
            if sources[0].receipt_scope:
                declared = dict(sources[0].receipt_scope)
                case = self.db.cases.get(declared.get("case_id"))
                if case is None or case.customer_id != customer_id:
                    raise ToolExecutionRejected(
                        "ERR_RECEIPT_SCOPE: Несогласованное обращение"
                    )
                declared.update(
                    customer_id=customer_id, status="accepted", source="client_answer"
                )
                self.db.client_receipt_declarations[declared["id"]] = declared
            return FileResponse(
                {
                    "customer_id": customer_id,
                    "request_type": request.type,
                    "status": "answered",
                    "data": {"topic": request.topic, **declared},
                    "text": sources[0].answer,
                }
            )
        if isinstance(request, DocumentChannelConfirmationRequest):
            self._require_verified(customer_id)
            contact = self.db.document_contacts.get(request.contact_id)
            if contact is None or contact.customer_id != customer_id:
                raise ToolExecutionRejected(
                    "ERR_FOREIGN_RESOURCE: Контакт не принадлежит клиенту"
                )
            sources = [
                a.document_channel_confirmation
                for a in self.db.client_answers.get(customer_id, [])
                if a.document_channel_confirmation
                and a.document_channel_confirmation.get("contact_id") == contact.id
            ]
            if len(sources) != 1:
                return FileResponse({"status": "unsupported_context", "data": None})
            if sources[0].get("confirmed"):
                contact.verified = True
            return FileResponse(
                {
                    "customer_id": customer_id,
                    "request_type": request.type,
                    "status": "confirmed" if contact.verified else "failed",
                    "contact_id": contact.id,
                }
            )
        if isinstance(
            request, (TransactionSelectionRequest, FeeRefundConfirmationRequest)
        ):
            self._require_verified(customer_id)
            if question or any(
                value is not None
                for value in (transaction_id, loan_id, account_id, payout_account_id)
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: запрос формируется из структурированных данных"
                )
            if isinstance(request, TransactionSelectionRequest):
                answers = [
                    a.transaction_selection
                    for a in self.db.client_answers.get(customer_id, [])
                    if a.transaction_selection is not None
                    and a.transaction_selection.get("merchant", "").casefold()
                    == request.merchant.casefold()
                    and a.transaction_selection.get("amount") == request.amount
                    and (
                        request.date is None
                        or a.transaction_selection.get("date") == request.date
                    )
                    and (
                        request.receipt is None
                        or a.transaction_selection.get("receipt") == request.receipt
                    )
                ]
                if len(answers) != 1:
                    return FileResponse(
                        {
                            "status": "unknown",
                            "data": None,
                            "request_type": request.type,
                        }
                    )
                return FileResponse(
                    {
                        "answer_id": "transaction-selection-answer-1",
                        "customer_id": customer_id,
                        "status": "answered",
                        "request_type": request.type,
                        "question": f"Уточните дату или номер документа для операции {request.merchant} на {request.amount:g} ₽.",
                        "data": answers[0],
                    }
                )
            card = self._get_card(request.card_id)
            if card.customer_id != customer_id:
                raise ValueError("ERR_INVALID_ARGUMENT: карта другого клиента")
            refunds = sorted(
                [item.model_dump() for item in request.refunds],
                key=lambda x: x["transaction_id"],
            )
            if (
                len({item["transaction_id"] for item in refunds}) != len(refunds)
                or round(sum(item["amount"] for item in refunds), 2)
                != request.total_amount
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: сумма не соответствует набору возвратов"
                )
            for item in refunds:
                transaction = self._get_transaction(item["transaction_id"])
                if (
                    transaction.customer_id != customer_id
                    or transaction.card_id != request.card_id
                ):
                    raise ValueError(
                        "ERR_INVALID_ARGUMENT: возврат относится к другой карте"
                    )
            answers = [
                a.fee_refund_confirmation
                for a in self.db.client_answers.get(customer_id, [])
                if a.fee_refund_confirmation is not None
                and a.fee_refund_confirmation.get("card_id") == request.card_id
                and a.fee_refund_confirmation.get("total_amount")
                == request.total_amount
                and sorted(
                    a.fee_refund_confirmation.get("refunds", []),
                    key=lambda x: x["transaction_id"],
                )
                == refunds
            ]
            if len(answers) != 1:
                return FileResponse(
                    {
                        "status": "unsupported_context",
                        "data": None,
                        "request_type": request.type,
                    }
                )
            approved = answers[0].get("approved_refunds", refunds)
            data = {
                **answers[0],
                "customer_id": customer_id,
                "refunds": approved,
                "state_version": request.state_version,
                "state_versions": request.state_versions,
                "requested_total_amount": request.total_amount,
                "total_amount": round(sum(item["amount"] for item in approved), 2),
            }
            if request.state_versions and set(request.state_versions) != set(
                item["transaction_id"] for item in approved
            ):
                raise ValueError("ERR_INVALID_ARGUMENT: Нужны версии точного набора")
            if request.state_version is not None or request.state_versions:
                if any(
                    self._fee_state_version(
                        self._get_transaction(item["transaction_id"])
                    )
                    != request.state_versions.get(
                        item["transaction_id"], request.state_version
                    )
                    for item in approved
                ):
                    raise ValueError(
                        "ERR_INVALID_ARGUMENT: состояние возврата изменилось"
                    )
            for item in approved:
                transaction = self._get_transaction(item["transaction_id"])
                if (
                    transaction.fee_refund_update
                    and self._get_transaction(
                        transaction.fee_refund_update["settlement_transaction_id"]
                    ).status
                    == "processing"
                ):
                    data["update_available"] = True
            if not data.get("confirmed"):
                return FileResponse(
                    {"status": "declined", "data": data, "request_type": request.type}
                )
            for cid, previous in self._fee_refund_confirmations.items():
                if previous == data:
                    break
            else:
                cid = (
                    f"fee-refund-confirmation-{len(self._fee_refund_confirmations) + 1}"
                )
                self._fee_refund_confirmations[cid] = data
            return FileResponse(
                {
                    "confirmation_id": cid,
                    "status": "partial_confirmed"
                    if approved != refunds
                    else "confirmed",
                    "update_available": data.get("update_available", False),
                    "data": data,
                    "customer_id": customer_id,
                    "request_type": request.type,
                    "question": f"Согласны на возвраты по карте *{card.last4} общей суммой {request.total_amount:g} ₽?",
                    "presented_refunds": refunds,
                }
            )
        if isinstance(request, AccountClosureSignatureRequest):
            return self._sign_closure(
                customer_id,
                request,
                question,
                transaction_id,
                loan_id,
                account_id,
                payout_account_id,
            )
        if payout_account_id is not None:
            raise ValueError("ERR_INVALID_ARGUMENT: payout account is only for signing")
        if isinstance(request, TariffChangeConfirmationRequest):
            self._require_verified(customer_id)
            if question or any(
                v is not None for v in (transaction_id, loan_id, account_id)
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: tariff consent generates its own question"
                )
            tariff = self.db.tariffs.get(request.new_tariff_id)
            if tariff is None:
                raise ValueError("ERR_NOT_FOUND: tariff not found")
            today = date.fromisoformat(self.db.today)
            first_next = (
                date(today.year + 1, 1, 1)
                if today.month == 12
                else date(today.year, today.month + 1, 1)
            ).isoformat()
            sources = [
                a.tariff_change_confirmation
                for a in self.db.client_answers.get(customer_id, [])
                if a.tariff_change_confirmation is not None
                and a.tariff_change_confirmation.new_tariff_id == request.new_tariff_id
                and a.tariff_change_confirmation.monthly_fee == tariff.monthly_fee
                and a.tariff_change_confirmation.effective_from == first_next
            ]
            if not sources:
                return FileResponse(
                    {
                        "customer_id": customer_id,
                        "request_type": request.type,
                        "status": "unsupported_context",
                        "data": None,
                    }
                )
            if len(sources) != 1:
                raise ValueError("ERR_INVALID_ARGUMENT: ambiguous fixed tariff consent")
            data = {
                "id": customer_id,
                "customer_id": customer_id,
                **sources[0].model_dump(),
                "retroactive_cashback": False,
                "existing_debt": sum(
                    a.debt
                    for a in self.db.accounts.values()
                    if a.customer_id == customer_id
                ),
            }
            answer = {
                "customer_id": customer_id,
                "request_type": request.type,
                "status": "confirmed" if data["confirmed"] else "declined",
                "question": f"Подтвердите переход на {tariff.name}: {tariff.monthly_fee:g} ₽ в месяц, "
                f"с {first_next}. Прежний долг {data['existing_debt']:g} ₽ сохраняется; "
                "прошлые покупки по новому тарифу не пересчитываются.",
                "data": data,
            }
            if not data["confirmed"]:
                return FileResponse(answer)
            for cid, old in self._tariff_answers.items():
                if old["data"] == data:
                    return FileResponse(old)
            answer["confirmation_id"] = (
                f"tariff-confirmation-{len(self._tariff_answers) + 1}"
            )
            self._tariff_answers[answer["confirmation_id"]] = answer
            return FileResponse(answer)
        if isinstance(request, RestructuringDocumentsRequest):
            self._require_verified(customer_id)
            if (
                question
                or not loan_id
                or account_id is not None
                or transaction_id is not None
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: нужен loan_id без question и других селекторов"
                )
            loan = self._get_loan(loan_id)
            if loan.customer_id != customer_id:
                raise ToolExecutionRejected("ERR_FOREIGN_OWNER: чужой кредит")
            if loan.status != "active":
                raise ValueError("ERR_INVALID_ARGUMENT: кредит не действует")
            sources = [
                a.restructuring_documents
                for a in self.db.client_answers.get(customer_id, [])
                if a.restructuring_documents is not None
                and a.restructuring_documents.loan_id == loan_id
            ]
            if not sources:
                return FileResponse(
                    {
                        "customer_id": customer_id,
                        "request_type": request.type,
                        "status": "unsupported_context",
                        "data": None,
                    }
                )
            if len(sources) != 1:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: неоднозначный фиксированный ответ"
                )
            if loan_id not in self._restructuring_answers:
                source = sources[0]
                self._restructuring_answers[loan_id] = {
                    "answer_id": f"restructuring-answer-{len(self._restructuring_answers) + 1}",
                    "customer_id": customer_id,
                    "request_type": request.type,
                    "status": "answered",
                    "question": "Какие документы, подтверждающие снижение дохода, можете предоставить по этому кредиту и когда?",
                    "data": source.model_dump(mode="json"),
                }
            return FileResponse(self._restructuring_answers[loan_id])
        if isinstance(request, LoanRepaymentConfirmationRequest):
            self._require_verified(customer_id)
            if (
                question
                or transaction_id is not None
                or loan_id is None
                or account_id is None
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: нужен кредит и счёт без question/transaction_id"
                )
            loan, _, quote = self._loan_repayment_quote(
                loan_id,
                account_id,
                request.amount,
                request.mode,
                request.date,
                request.quote_id,
            )
            if loan.customer_id != customer_id:
                raise ToolExecutionRejected("ERR_FOREIGN_OWNER: чужой кредит")
            sources = [
                a.loan_repayment_confirmation
                for a in self.db.client_answers.get(customer_id, [])
                if a.loan_repayment_confirmation is not None
                and a.loan_repayment_confirmation.loan_id == loan_id
                and a.loan_repayment_confirmation.quote_id == quote.quote_id
            ]
            if not sources:
                return FileResponse({"status": "unsupported_context", "data": None})
            if len(sources) != 1:
                raise ValueError("ERR_INVALID_ARGUMENT: неоднозначный ответ")
            answer = sources[0]
            if any(
                getattr(answer, key) != value
                for key, value in {
                    "account_id": account_id,
                    "amount": request.amount,
                    "mode": request.mode,
                    "date": request.date,
                    "quote_id": request.quote_id,
                }.items()
            ):
                raise ValueError("ERR_CONFIRMATION_MISMATCH: параметры изменились")
            data = {"customer_id": customer_id, **answer.model_dump()}
            question_text = f"Подтвердите в приложении погашение {request.amount:g} ₽ по кредиту {loan_id} со счёта {account_id} на {request.date}, способ {request.mode}."
            if not answer.confirmed:
                return FileResponse(
                    {
                        "status": "declined",
                        "request_type": request.type,
                        "question": question_text,
                        "data": data,
                    }
                )
            for cid, previous in self._loan_repayment_confirmations.items():
                if previous == data:
                    return FileResponse(
                        {
                            "status": "confirmed",
                            "confirmation_id": cid,
                            "request_type": request.type,
                            "question": question_text,
                            "data": data,
                        }
                    )
            cid = f"loan-confirmation-{len(self._loan_repayment_confirmations) + 1}"
            self._loan_repayment_confirmations[cid] = data
            return FileResponse(
                {
                    "status": "confirmed",
                    "confirmation_id": cid,
                    "request_type": request.type,
                    "question": question_text,
                    "data": data,
                }
            )
        if loan_id is not None or account_id is not None:
            raise ValueError(
                "ERR_INVALID_ARGUMENT: loan_id/account_id только для loan_repayment_confirmation"
            )
        if isinstance(request, OperationConfirmationRequest):
            self._require_verified(customer_id)
            if question or transaction_id is None:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: нужен transaction_id без question"
                )
            transaction = self._get_transaction(transaction_id)
            if transaction.customer_id != customer_id:
                raise ToolExecutionRejected(
                    "ERR_FOREIGN_OWNER: операция другого клиента"
                )
            if (
                transaction.status != "blocked"
                or transaction.kind != "transfer"
                or transaction.restriction_basis != "internal_soft_antifraud"
                or not transaction.release_allowed
            ):
                raise ValueError(
                    "ERR_RELEASE_NOT_ALLOWED: основание не разрешает снятие"
                )
            if request.amount != transaction.amount:
                raise ValueError("ERR_CONFIRMATION_MISMATCH: другая сумма")
            sources = [
                a.operation_confirmation
                for a in self.db.client_answers.get(customer_id, [])
                if a.operation_confirmation is not None
                and a.operation_confirmation.transaction_id == transaction_id
            ]
            if not sources:
                return FileResponse({"status": "unsupported_context", "data": None})
            if len(sources) != 1:
                raise ValueError("ERR_INVALID_ARGUMENT: неоднозначный ответ")
            answer = sources[0]
            if any(
                getattr(answer, key) != getattr(transaction, key)
                for key in ("amount", "merchant", "date", "account_id")
            ):
                raise ValueError("ERR_CONFIRMATION_MISMATCH: параметры изменились")
            data = {
                "customer_id": customer_id,
                **answer.model_dump(),
                "account_id": transaction.account_id,
                "bank_date": self.db.today,
            }
            if not answer.confirmed:
                return FileResponse(
                    {
                        "status": "declined",
                        "request_type": request.type,
                        "data": data,
                    }
                )
            for confirmation_id, existing in self._operation_confirmations.items():
                if existing == data:
                    return FileResponse(
                        {
                            "status": "confirmed",
                            "confirmation_id": confirmation_id,
                            "request_type": request.type,
                            "data": data,
                        }
                    )
            if not answer.confirmed:
                return FileResponse(
                    {"status": "declined", "request_type": request.type, "data": data}
                )
            confirmation_id = (
                f"operation-confirmation-{len(self._operation_confirmations) + 1}"
            )
            self._operation_confirmations[confirmation_id] = data
            return FileResponse(
                {
                    "status": "confirmed",
                    "confirmation_id": confirmation_id,
                    "request_type": request.type,
                    "data": data,
                }
            )
        if transaction_id is not None:
            raise ValueError(
                "ERR_INVALID_ARGUMENT: transaction_id только для operation_confirmation"
            )

        if isinstance(request, DeviceRecognitionRequest):
            self._require_verified(customer_id)
            if question or loan_id is not None or account_id is not None:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: device_recognition generates its own question"
                )
            sources = [
                a.device_answers
                for a in self.db.client_answers.get(customer_id, [])
                if a.device_answers is not None
            ]
            if not sources:
                return FileResponse(
                    {
                        "customer_id": customer_id,
                        "request_type": request.type,
                        "status": "unknown",
                        "data": None,
                    }
                )
            if len(sources) != 1 or not sources[0]:
                raise ValueError("ERR_INVALID_ARGUMENT: ambiguous fixed device answers")
            rows = []
            devices = []
            for device_id, recognized in sorted(sources[0].items()):
                device = self.db.devices.get(device_id)
                if device is None or device.customer_id != customer_id:
                    raise ValueError(
                        "ERR_INVALID_ARGUMENT: device does not belong to client"
                    )
                rows.append(
                    {
                        "id": device.id,
                        "customer_id": customer_id,
                        "recognized": recognized,
                    }
                )
                devices.append(
                    f"{device.model}, регистрация {device.id}, "
                    f"последний вход {device.last_login or 'не установлен'}, "
                    f"страна входа {device.country or 'не установлена'}"
                )
            return FileResponse(
                {
                    "answer_id": "devices-answer-1",
                    "customer_id": customer_id,
                    "request_type": request.type,
                    "status": "answered",
                    "question": "Какие из этих устройств принадлежат вам: "
                    + "; ".join(devices)
                    + "?",
                    "data": {"items": rows},
                }
            )
        if isinstance(request, UtilityPaymentAccountsRequest):
            self._require_verified(customer_id)
            if question:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: utility_payment_accounts формирует вопрос автоматически"
                )
            answers = [
                a.utility_payment_accounts
                for a in self.db.client_answers.get(customer_id, [])
                if a.utility_payment_accounts is not None
            ]
            if not answers:
                return FileResponse(
                    {
                        "customer_id": customer_id,
                        "request_type": request.type,
                        "status": "unsupported_context",
                        "data": None,
                    }
                )
            if len(answers) != 1:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: неоднозначный фиксированный ответ"
                )
            return FileResponse(
                {
                    "answer_id": "utility-accounts-answer-1",
                    "customer_id": customer_id,
                    "request_type": request.type,
                    "status": "answered",
                    "question": "Назовите ошибочный и правильный лицевые счета поставщика ЖКХ.",
                    "data": answers[0].model_dump(),
                }
            )
        if isinstance(request, SubscriptionSelectionRequest):
            self._require_verified(customer_id)
            if question or any(
                value is not None
                for value in (transaction_id, loan_id, account_id, payout_account_id)
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: subscription_selection без question и других селекторов"
                )
            sources = []
            for answer in self.db.client_answers.get(customer_id, []):
                value = answer.subscription_selection
                if value is None:
                    continue
                data = (
                    value.model_dump(mode="json")
                    if hasattr(value, "model_dump")
                    else dict(value)
                )
                if data["merchant_id"] == request.merchant_id:
                    sources.append(data)
            if not sources:
                return FileResponse(
                    {
                        "customer_id": customer_id,
                        "request_type": request.type,
                        "status": "unsupported_context",
                        "data": None,
                    }
                )
            if len(sources) != 1:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: неоднозначный фиксированный ответ"
                )
            return FileResponse(
                {
                    "answer_id": "subscription-selection-answer-1",
                    "customer_id": customer_id,
                    "request_type": request.type,
                    "status": "answered",
                    "question": "Для какого профиля сервиса прекратить будущие платежи?",
                    "data": sources[0],
                }
            )
        if isinstance(request, InternetOperationsConfirmationRequest):
            self._require_verified(customer_id)
            if question:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: confirmation формирует вопрос автоматически"
                )
            answers = [
                a.internet_operations_confirmation
                for a in self.db.client_answers.get(customer_id, [])
                if a.internet_operations_confirmation is not None
                and a.internet_operations_confirmation.card_last4 == request.card_last4
                and a.internet_operations_confirmation.enabled == request.enabled
            ]
            if len(answers) != 1:
                return FileResponse(
                    {
                        "customer_id": customer_id,
                        "request_type": request.type,
                        "status": "unsupported_context",
                    }
                )
            selected = answers[0]
            return FileResponse(
                {
                    "customer_id": customer_id,
                    "request_type": request.type,
                    "status": "answered",
                    "question": f"Подтвердите {'включение' if request.enabled else 'отключение'} интернет-операций по карте *{request.card_last4}.",
                    "data": selected.model_dump(),
                }
            )
        if isinstance(request, OperationsRequest):
            self._require_verified(customer_id)
            if question:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: confirm_operations формирует вопрос автоматически"
                )
            if customer_id in self._operation_answers:
                return FileResponse(self._operation_answers[customer_id])
            sources = [
                a
                for a in self.db.client_answers.get(customer_id, [])
                if a.operation_answers
            ]
            if not sources:
                return FileResponse(
                    {
                        "customer_id": customer_id,
                        "request_type": request.type,
                        "status": "unknown",
                        "data": None,
                    }
                )
            if len(sources) != 1:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: неоднозначный фиксированный ответ"
                )
            rows = []
            for transaction_id, recognized in sources[0].operation_answers.items():
                transaction = self.db.transactions.get(transaction_id)
                if transaction is None or transaction.customer_id != customer_id:
                    raise ValueError(
                        "ERR_INVALID_ARGUMENT: операция не принадлежит клиенту"
                    )
                rows.append(
                    {
                        "id": transaction_id,
                        "customer_id": customer_id,
                        "recognized": recognized,
                    }
                )
            answer = {
                "answer_id": "operations-answer-1",
                "customer_id": customer_id,
                "request_type": request.type,
                "status": "answered",
                "question": "Какие операции текущего обращения вы совершали сами?",
                "data": {"items": rows},
                "text": sources[0].answer,
            }
            self._operation_answers[customer_id] = answer
            return FileResponse(answer)
        if question:
            raise ValueError(
                "ERR_INVALID_ARGUMENT: identity формирует вопрос автоматически"
            )
        method = request.method
        question = (
            "Назовите кодовое слово для подтверждения личности."
            if method == "code_word"
            else "Назовите дату рождения для подтверждения личности."
        )
        sources = [
            answer
            for answer in self.db.client_answers.get(customer_id, [])
            if answer.identity_method == method
        ]
        if not sources or not sources[0].credential:
            return FileResponse(
                {
                    "customer_id": customer_id,
                    "request_type": "identity",
                    "status": "unknown",
                    "question": question,
                    "data": None,
                    "text": "Клиент не может сообщить запрошенные сведения.",
                }
            )
        if len(sources) != 1:
            raise ValueError("ERR_INVALID_ARGUMENT: неоднозначный фиксированный ответ")
        answer_id = f"identity-answer-{len(self._identity_answers) + 1}"
        answer = {
            "answer_id": answer_id,
            "customer_id": customer_id,
            "request_type": "identity",
            "status": "answered",
            "question": question,
            "data": {"method": method, "credential": sources[0].credential},
            "text": sources[0].answer,
        }
        self._identity_answers[answer_id] = answer
        return FileResponse(answer)

    def _sign_closure(
        self,
        customer_id,
        request,
        question,
        transaction_id,
        loan_id,
        account_id,
        payout_account_id,
    ):
        self._require_verified(customer_id)
        if question or transaction_id is not None or loan_id is not None:
            raise ValueError("ERR_INVALID_ARGUMENT: app presents the document itself")
        source = self._get_account(account_id)
        target = self._get_account(payout_account_id)
        if source.customer_id != customer_id or target.customer_id != customer_id:
            raise ToolExecutionRejected(
                "ERR_FOREIGN_ACCOUNT: both accounts must belong to signer"
            )
        if source.id == target.id or source.currency != target.currency:
            raise ValueError(
                "ERR_INVALID_ARGUMENT: different own accounts of same currency required"
            )
        if source.status != "active" or target.status != "active":
            raise ValueError("ERR_ACCOUNT_CLOSED: signing requires active accounts")
        document = closure_document(
            self._get_customer(customer_id), source, target, self.db.today
        )
        sha = document_hash(document)
        for receipt in self._closure_answers.values():
            if receipt["document_sha256"] == sha:
                return FileResponse(receipt)
        answers = [
            a.account_closure_signature
            for a in self.db.client_answers.get(customer_id, [])
            if a.account_closure_signature is not None
            and a.account_closure_signature.account_id == source.id
            and a.account_closure_signature.payout_account_id == target.id
        ]
        if len(answers) > 1:
            raise ValueError("ERR_INVALID_ARGUMENT: ambiguous fixed signing response")
        decision = answers[0] if answers else None
        status = (
            "unknown"
            if decision is None
            else (
                "agreement_missing"
                if not decision.agreement_id
                else "signed"
                if decision.confirmed
                else "declined"
            )
        )
        receipt = {
            "id": "closure-signature-" + str(len(self._closure_answers) + 1),
            "confirmation_id": "closure-signature-"
            + str(len(self._closure_answers) + 1),
            "customer_id": customer_id,
            "signer_id": customer_id,
            "request_type": request.type,
            "status": status,
            "channel": request.channel,
            "signature_type": "simple_electronic_signature"
            if status == "signed"
            else None,
            "agreement_id": decision.agreement_id if decision else None,
            "signed_at": self.db.today + "T12:00:00+03:00"
            if status == "signed"
            else None,
            "document": document,
            "document_sha256": sha,
            "question": "Ознакомьтесь с заявлением и подпишите его в банковском приложении.",
        }
        self._closure_answers[receipt["confirmation_id"]] = receipt
        return FileResponse(receipt)

    @is_tool(ToolType.WRITE)
    def request_account_closure(
        self,
        account_id: str,
        payout_account_id: str,
        submission_channel: Literal["support_intake", "bank_app"] = "support_intake",
        confirmation_id: str | None = None,
    ):
        """Принять заявление; bank_app требует полученную квитанцию ПЭП.

        Для bank_app требуется status=signed и confirmation_id из результата
        ask_client(account_closure_signature, channel=bank_app) с обоими счетами.
        Передайте этот ID и submission_channel=bank_app; документ должен совпадать.
        Без канала регистрируется лишь предварительное support_intake обращение,
        которое НЕ подтверждает приём подписанного заявления через приложение.
        Регистрация не переводит деньги и не закрывает счёт или карту.

        Args:
            account_id: Свой действующий счёт для прекращения договора.
            payout_account_id: Другой свой действующий счёт той же валюты.
            submission_channel: bank_app для подписанного электронного заявления.
            confirmation_id: Полученная квитанция подписи точного документа этой сессии.
        """
        source = self._get_account(account_id)
        self._require_verified(source.customer_id)
        target = self._get_account(payout_account_id)
        if source.customer_id != target.customer_id:
            raise ToolExecutionRejected(
                "ERR_FOREIGN_ACCOUNT: accounts belong to different customers"
            )
        receipt = None
        if submission_channel == "bank_app":
            receipt = self._closure_answers.get(confirmation_id)
            document = closure_document(
                self._get_customer(source.customer_id), source, target, self.db.today
            )
            if (
                receipt is None
                or receipt["status"] != "signed"
                or receipt["signer_id"] != source.customer_id
                or receipt["channel"] != "bank_app"
                or receipt["agreement_id"] != "bank-app-pep-v1"
                or receipt["document_sha256"] != document_hash(document)
            ):
                raise ValueError(
                    "ERR_SIGNATURE_MISMATCH: signed app document receipt required"
                )
        elif confirmation_id is not None:
            raise ValueError(
                "ERR_INVALID_ARGUMENT: signature belongs to bank_app channel"
            )
        case = super().request_account_closure(account_id, payout_account_id)
        # Preserve the existing preliminary-intake end state for other scenarios.
        if receipt:
            from copy import deepcopy

            case.submission_channel = submission_channel
            case.closure_document = deepcopy(receipt["document"])
            case.closure_signature = {
                key: deepcopy(receipt[key])
                for key in (
                    "confirmation_id",
                    "document_sha256",
                    "signer_id",
                    "channel",
                    "signature_type",
                    "agreement_id",
                    "signed_at",
                )
            }
        return case

    @is_tool(ToolType.WRITE)
    def change_tariff(
        self, customer_id: str, new_tariff_id: str, confirmation_id: str
    ) -> Customer:
        """Register the client's confirmed servicing tariff for the next period.

        Requires a received confirmation_id from ask_client(tariff_change_confirmation).
        Old debt is preserved and does not block this non-credit tariff change.

        Args:
            customer_id: Identified owner of the service.
            new_tariff_id: Tariff named in the received consent.
            confirmation_id: Receipt from this session for this exact instruction.

        Returns:
            Customer with pending_tariff_id and pending_tariff_from.

        Raises:
            ValueError: Missing, declined, stale or incompatible consent.
        """
        self._get_customer(customer_id)
        self._require_verified(customer_id)
        answer = self._tariff_answers.get(confirmation_id)
        tariff = self.db.tariffs.get(new_tariff_id)
        today = date.fromisoformat(self.db.today)
        first_next = (
            date(today.year + 1, 1, 1)
            if today.month == 12
            else date(today.year, today.month + 1, 1)
        ).isoformat()
        if (
            answer is None
            or answer["customer_id"] != customer_id
            or answer["status"] != "confirmed"
            or answer["data"]["new_tariff_id"] != new_tariff_id
            or tariff is None
            or answer["data"]["monthly_fee"] != tariff.monthly_fee
            or answer["data"]["effective_from"] != first_next
        ):
            raise ValueError(
                "ERR_CONSENT_MISMATCH: received consent for this tariff is required"
            )
        return super().change_tariff(customer_id, new_tariff_id)

    @is_tool(ToolType.WRITE)
    def verify_identity(self, customer_id: str, credential: str, answer_id: str) -> str:
        """Проверить секрет, полученный от клиента через ask_client(identity).

        Args:
            customer_id: Клиент, которому был задан вопрос.
            credential: Значение data.credential из полученного ответа клиента.
            answer_id: Значение answer_id из того же полученного ответа.

        Returns:
            Подтверждение личности при совпадении ответа и банковских данных.

        Raises:
            ValueError: Ответ отсутствует, принадлежит другому клиенту или содержит другой секрет.
        """
        answer = self._identity_answers.get(answer_id)
        if (
            answer is None
            or answer["customer_id"] != customer_id
            or answer["data"]["credential"].strip().casefold()
            != credential.strip().casefold()
        ):
            raise ValueError(
                "ERR_IDENTITY_MISMATCH: нужен секрет из ответа этого клиента"
            )
        return super().verify_identity(customer_id, credential)
