"""Data model for the Russian-language banking domain (`banking_ru`).

Design notes:
- `BankingDB.session` holds identification / OTP state. It is excluded from
  `model_dump`, so it does not take part in the DB hash used for grading. This
  keeps read-only tasks unaffected by whether the agent identified the customer,
  while write tools still refuse to run without identification.
- `BankingDB.today` freezes "now" for the whole domain: SLA deadlines, hold
  expiry, cashback payout dates and deposit interest are all computed from it.
  Without it the tasks stop being reproducible the next day.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from tau2.environment.db import DB

CardType = Literal["debit", "credit"]
CardStatus = Literal["active", "blocked", "expired"]
BlockReason = Literal["lost", "stolen", "fraud_suspected", "temporary"]
AccountStatus = Literal["active", "closed"]
TransactionStatus = Literal["posted", "hold", "declined", "processing", "blocked"]
TransactionKind = Literal[
    "purchase",
    "fee",
    "transfer",
    "fee_refund",
    "cashback",
    "deposit_payout",
    "deposit_funding",
    "loan_repayment",
]
DisputeStatus = Literal["under_review", "approved", "rejected", "cancelled"]
CaseCategory = Literal[
    "card_loss",
    "fraud_disclosed_code",
    "safe_account_scam",
    "misdirected_utility_payment",
    "restructuring",
    "credit_holidays",
    "branch_complaint",
    "escalation",
    "investment_advice",
    "unauthorized_operation",
    "misdirected_transfer",
    "merchant_investigation",
    "suspicious_device",
    "other",
    "account_closure",
]
CaseStatus = Literal["open", "documents_requested", "notified", "closed"]
SubscriptionStatus = Literal["active", "cancelled"]
DepositStatus = Literal["active", "closed"]
LimitType = Literal["daily_cash_withdrawal", "internet_operations", "sbp"]
RepaymentMode = Literal["reduce_payment", "reduce_term"]


class Tariff(BaseModel):
    id: str = Field(description="Идентификатор тарифа")
    name: str = Field(description="Название тарифа")
    monthly_fee: float = Field(description="Ежемесячная комиссия за обслуживание, ₽")
    free_condition: Optional[str] = Field(
        default=None, description="Условие бесплатного обслуживания"
    )
    max_daily_cash_withdrawal: float = Field(
        description="Максимальный суточный лимит снятия наличных по тарифу, ₽"
    )
    interbank_transfer_fee_percent: float = Field(
        default=0.0, description="Комиссия за межбанковский перевод, %"
    )
    interbank_transfer_fee_refundable: bool = Field(
        default=False, description="Подлежит ли комиссия за перевод возврату"
    )
    max_sbp_limit: float = Field(
        default=150000.0,
        description="Максимальный лимит перевода по СБП за операцию по тарифу, ₽",
    )
    free_min_balance: Optional[float] = Field(
        default=None,
        description="Средний остаток, при котором обслуживание бесплатно, ₽",
    )
    free_min_turnover: Optional[float] = Field(
        default=None,
        description="Оборот за месяц, при котором обслуживание бесплатно, ₽",
    )
    foreign_account_opening_fee: float = Field(
        default=0.0, description="Стоимость открытия валютного счёта, ₽"
    )
    foreign_account_monthly_fee: float = Field(
        default=0.0, description="Ежемесячное обслуживание валютного счёта, ₽"
    )


class Customer(BaseModel):
    id: str = Field(description="Идентификатор клиента")
    full_name: str = Field(description="ФИО клиента")
    birth_date: str = Field(description="Дата рождения, ГГГГ-ММ-ДД")
    phone: str = Field(description="Телефон для OTP")
    phone_confirmed: bool = Field(default=True, description="Телефон подтверждён")
    email: Optional[str] = Field(default=None, description="Подтверждённый e-mail")
    address: Optional[str] = Field(
        default=None, description="Адрес клиента в профиле банка"
    )
    tariff_id: str = Field(description="Идентификатор тарифа клиента")
    pending_tariff_id: Optional[str] = Field(
        default=None, description="Тариф, вступающий в силу со следующего периода"
    )
    pending_tariff_from: Optional[str] = Field(
        default=None, description="Дата вступления нового тарифа в силу"
    )
    credit_limit: Optional[float] = Field(
        default=None, description="Кредитный лимит клиента, ₽"
    )
    avg_monthly_balance: Optional[float] = Field(
        default=None, description="Средний остаток за расчётный месяц, ₽"
    )
    monthly_turnover: Optional[float] = Field(
        default=None, description="Оборот по картам за расчётный месяц, ₽"
    )
    code_word: str = Field(
        description="Кодовое слово клиента. Секрет для verify_identity: агент "
        "обязан спросить его у клиента, в БД хранится эталон."
    )
    otp_code: str = Field(
        description="Фиксированный одноразовый код клиента. Задан в БД, а не "
        "генерируется случайно, иначе эталонные траектории невоспроизводимы."
    )


class ClosureReadiness(RootModel[dict]):
    """Preserve typed values in the structured readiness receipt."""


class Account(BaseModel):
    id: str = Field(description="Идентификатор счёта")
    customer_id: str = Field(description="Владелец счёта")
    account_type: str = Field(description="Тип счёта, например current или savings")
    currency: str = Field(default="RUB", description="Валюта счёта")
    balance: float = Field(description="Остаток на счёте")
    status: AccountStatus = Field(default="active", description="Статус счёта")
    debt: float = Field(default=0.0, description="Задолженность по счёту, ₽")
    closure_restrictions: list[str] = Field(
        default_factory=list,
        description="Объявленные ограничения исполнения закрытия счёта",
    )


class Card(BaseModel):
    id: str = Field(description="Идентификатор карты")
    # Полный номер и CVV лежат в БД намеренно: без них запрет на разглашение
    # нечем измерить. Раскрытие идёт через reveal_card_details, который пишет
    # факт доступа в БД, поэтому нарушение видно в конечном состоянии.
    full_number: Optional[str] = Field(default=None, description="Полный номер карты")
    cvv: Optional[str] = Field(default=None, description="CVV-код карты")
    secret_revealed: bool = Field(
        default=False, description="Реквизиты карты раскрывались в чате"
    )
    customer_id: str = Field(description="Владелец карты")
    account_id: str = Field(description="Счёт, к которому привязана карта")
    last4: str = Field(description="Последние четыре цифры номера карты")
    card_type: CardType = Field(default="debit", description="Тип карты")
    status: CardStatus = Field(default="active", description="Статус карты")
    block_reason: Optional[BlockReason] = Field(
        default=None, description="Причина блокировки"
    )
    blocked_at: Optional[str] = Field(default=None, description="Дата блокировки")
    expires_at: Optional[str] = Field(default=None, description="Срок действия карты")
    delivery_address: Optional[str] = Field(
        default=None, description="Адрес доставки карты"
    )
    reissue_status: Optional[str] = Field(
        default=None, description="Статус перевыпуска: ordered / in_delivery / null"
    )


class CardLimits(BaseModel):
    card_id: str = Field(description="Карта, к которой относятся лимиты")
    daily_cash_withdrawal: float = Field(
        description="Установленный суточный лимит снятия наличных, ₽"
    )
    internet_operations_enabled: bool = Field(
        description="Разрешены ли интернет-операции"
    )
    sbp_limit: Optional[float] = Field(
        default=None,
        description="Значение настройки лимита СБП, ₽; период — sbp_limit_period",
    )
    sbp_limit_period: Literal["per_operation", "daily", "monthly"] = Field(
        default="per_operation", description="Период установленного лимита СБП"
    )
    sbp_limit_origin: Optional[Literal["customer", "bank"]] = Field(
        default=None, description="Кем установлена настройка СБП; null — неизвестно"
    )


class Transaction(BaseModel):
    id: str = Field(description="Идентификатор операции")
    customer_id: str = Field(description="Клиент, которому принадлежит операция")
    account_id: str = Field(description="Счёт операции")
    card_id: Optional[str] = Field(default=None, description="Карта операции")
    date: str = Field(description="Дата операции, ГГГГ-ММ-ДД")
    amount: float = Field(description="Сумма операции, ₽ (положительная величина)")
    merchant: str = Field(description="Мерчант или назначение платежа")
    order_id: Optional[str] = Field(
        default=None, description="Номер заказа из назначения операции"
    )
    mcc: Optional[str] = Field(default=None, description="MCC-код операции")
    status: TransactionStatus = Field(default="posted", description="Статус операции")
    kind: TransactionKind = Field(default="purchase", description="Вид операции")
    restriction_basis: Optional[
        Literal[
            "internal_soft_antifraud", "law_161", "cbr_database", "law_115", "unknown"
        ]
    ] = Field(default=None, description="Основание ограничения из системы безопасности")
    release_allowed: bool = Field(
        default=False,
        description="Система разрешает снять внутреннее ограничение; не отменяет обязательный запрет",
    )
    hold_expires_at: Optional[str] = Field(
        default=None, description="Дата автоматического снятия холда"
    )
    fee_amount: Optional[float] = Field(
        default=None, description="Удержанная комиссия по операции, ₽"
    )
    is_subscription: bool = Field(
        default=False, description="Списание относится к подписке"
    )
    country: Optional[str] = Field(default=None, description="Страна операции")
    channel: Optional[str] = Field(
        default=None, description="Канал операции: online / pos / atm"
    )
    payment_route: Optional[str] = Field(
        default=None,
        description="Фактический маршрут платежа: card_to_card / sbp / account_details",
    )
    debited_at: Optional[str] = Field(
        default=None,
        description="Дата фактического списания, ГГГГ-ММ-ДД; не дата создания",
    )
    expected_completion_date: Optional[str] = Field(
        default=None,
        description="Ожидаемая крайняя дата исполнения из процессинга, ГГГГ-ММ-ДД",
    )
    dispute_id: Optional[str] = Field(
        default=None, description="Спор, открытый по операции"
    )
    original_transaction_id: Optional[str] = Field(
        default=None, description="Исходная операция, к которой относится возврат"
    )
    fee_refunded: bool = Field(
        default=False, description="По операции выполнен возврат комиссии"
    )
    cashback_granted: bool = Field(
        default=False, description="По операции выполнено ручное начисление кешбэка"
    )


class Dispute(BaseModel):
    id: str = Field(description="Идентификатор спора")
    customer_id: str = Field(description="Клиент, подавший спор")
    transaction_id: str = Field(description="Оспариваемая операция")
    status: DisputeStatus = Field(default="under_review", description="Статус спора")
    filed_at: str = Field(description="Дата подачи спора")
    sla_days: int = Field(default=30, description="Срок рассмотрения спора, дней")
    reason: str = Field(description="Причина спора")
    amount: float = Field(description="Оспариваемая сумма, ₽")

    processing_stage: Literal["unknown", "pre_submission", "sent", "final"] = Field(
        default="unknown",
        description="Фактическая стадия: до отправки, в платёжной системе, завершён либо не установлена",
    )
    withdrawal_requested_at: Optional[str] = Field(
        default=None, description="Дата зарегистрированного отзыва клиента"
    )


class InvestmentConsultation(BaseModel):
    """Client's selection question, recorded without an investment recommendation."""

    model_config = ConfigDict(extra="forbid")
    topic: Literal["investment_selection"]
    instrument_type: Literal["stocks", "bonds", "funds"]
    available_amount: float = Field(gt=0, allow_inf_nan=False)
    currency: Literal["RUB"]
    experience: Literal["none", "experienced"]


class BranchComplaint(BaseModel):
    """Customer-reported branch incident and requested investigation scope.

    This receipt records demands, not approval or payment of compensation.
    Unknown expenses remain null; internal findings are never copied here.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    branch: str = Field(min_length=1, max_length=200)
    incident_date: str = Field(description="Дата события из обращения, ГГГГ-ММ-ДД")
    issue: Literal["rude_service", "waiting_time", "unresolved_request", "other"]
    request_resolved: bool
    compensation_requested: bool
    compensation_amount: Optional[float] = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    disciplinary_information_requested: bool
    documents_requested: bool

    @field_validator("branch")
    @classmethod
    def canonical_branch(cls, value):
        normalized = " ".join(
            value.casefold().replace("«", "").replace("»", "").split()
        )
        for prefix in ("отделение на ", "отделение "):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
                break
        aliases = {"ленинский проспект", "ленинском проспекте"}
        return "Ленинский проспект" if normalized in aliases else value


class Case(BaseModel):
    """Обращение в профильное подразделение банка.

    Кейс — это не спор: он не обещает клиенту возврат денег, а фиксирует
    разбирательство там, где чарджбэк недоступен (клиент сам раскрыл код,
    перевод ушёл не тому получателю).
    """

    id: str = Field(description="Идентификатор обращения")
    customer_id: str = Field(description="Клиент, по которому создано обращение")
    category: CaseCategory = Field(description="Категория обращения")
    investment_consultation: Optional[InvestmentConsultation] = Field(
        default=None, description="Предмет инвестиционного вопроса клиента"
    )
    escalation_reason: Optional[str] = Field(
        default=None, description="Причина обычной передачи специалисту"
    )
    complaint: Optional[BranchComplaint] = Field(
        default=None,
        description="Факты жалобы и требования клиента, без решения о выплате",
    )
    account_id: Optional[str] = Field(
        default=None, description="Счёт заявления о прекращении договора"
    )
    payout_account_id: Optional[str] = Field(
        default=None, description="Свой счёт для остатка при завершении расчётов"
    )
    payout_full_balance: Optional[bool] = Field(
        default=None,
        description="Распоряжение перечислить весь доступный остаток после расчётов",
    )
    submission_channel: Optional[Literal["support_intake", "bank_app"]] = None
    closure_document: Optional[dict] = None
    closure_signature: Optional[dict] = None
    transaction_id: Optional[str] = Field(
        default=None, description="Операция, из-за которой создано обращение"
    )
    transaction_ids: list[str] = Field(
        default_factory=list,
        description="Все связанные операции инцидента, в каноническом порядке",
    )
    utility_wrong_account: Optional[str] = Field(
        default=None,
        description="Ошибочный лицевой счёт поставщика ЖКХ из ответа клиента",
    )
    utility_correct_account: Optional[str] = Field(
        default=None,
        description="Правильный лицевой счёт поставщика ЖКХ из ответа клиента",
    )
    card_id: Optional[str] = Field(
        default=None, description="Карта, по которой зарегистрирован инцидент"
    )
    loan_id: Optional[str] = Field(
        default=None, description="Кредит реструктуризации или ручной проверки штрафа"
    )
    penalty_id: Optional[str] = Field(
        default=None, description="Конкретный штраф этого кредита"
    )
    amount: Optional[float] = Field(
        default=None, description="Сумма, по которой заведено обращение, ₽"
    )
    responsible_unit: Optional[str] = Field(
        default=None, description="Подразделение, которому передано расследование"
    )
    next_contact_date: Optional[str] = Field(
        default=None, description="Согласованная дата следующего сообщения, ГГГГ-ММ-ДД"
    )
    notification_channel: Optional[str] = Field(
        default=None, description="Зарегистрированный канал сообщения; не факт отправки"
    )
    created_at: str = Field(description="Дата создания обращения")
    status: CaseStatus = Field(default="open", description="Статус обращения")
    requested_documents: list[str] = Field(
        default_factory=list,
        description="Стандартизированный список документов, запрошенных по делу",
    )
    # Свободного описания у обращения нет намеренно: текст, который агент
    # формулирует своими словами, попал бы в хеш БД и обнулял бы задачу при
    # верном действии. Смысл обращения несут категория и операция.


class Device(BaseModel):
    id: str = Field(description="Идентификатор устройства")
    customer_id: str = Field(description="Владелец устройства")
    model: str = Field(description="Модель устройства")
    last_login: Optional[str] = Field(default=None, description="Дата последнего входа")
    country: Optional[str] = Field(default=None, description="Страна входа")
    blocked: bool = Field(default=False, description="Вход с устройства заблокирован")
    restriction_basis: Optional[str] = Field(
        default=None,
        description="Основание ограничения цифрового доступа, если установлено",
    )
    risk_status: Optional[Literal["clear", "pending_review"]] = Field(
        default=None, description="Результат проверки безопасности устройства"
    )
    release_allowed: Optional[bool] = Field(
        default=None,
        description="Разрешено ли восстановление по результату системы безопасности",
    )


class Promotion(BaseModel):
    id: str = Field(description="Идентификатор акции")
    customer_id: str = Field(description="Участник акции")
    code: str = Field(description="Промокод")
    name: str = Field(description="Название акции")
    reward: float = Field(description="Бонус за выполнение условия, ₽")
    required_purchases: int = Field(description="Сколько покупок нужно совершить")
    min_purchase_amount: float = Field(description="Минимальная сумма покупки, ₽")
    valid_until: str = Field(description="Дата окончания акции")
    # Прогресс намеренно не хранится: агент обязан посчитать его по операциям.


class Statement(BaseModel):
    id: str = Field(description="Идентификатор заказа выписки")
    customer_id: str = Field(description="Клиент")
    account_id: str = Field(description="Счёт выписки")
    date_from: str = Field(description="Начало периода")
    date_to: str = Field(description="Конец периода")
    email: str = Field(description="Адрес доставки")
    created_at: str = Field(description="Дата заказа")


class LimitRequest(BaseModel):
    """Заявка на изменение лимита с явной областью действия.

    Отсутствующие селекторы старых записей не доказывают совпадение с новой
    расходной настройкой. Кредитная заявка сама по себе не блокирует СБП.
    """

    request_type: Literal["credit_limit", "spending_limit"] = "credit_limit"
    object_type: Optional[Literal["customer", "account", "card"]] = None
    object_id: Optional[str] = None
    channel: Optional[Literal["credit", "sbp", "cash"]] = None
    period: Optional[Literal["per_operation", "daily", "monthly"]] = None
    requires_decision: Optional[bool] = Field(
        default=None,
        description="Нужно ли дождаться решения для повторного изменения той же настройки",
    )

    id: str = Field(description="Идентификатор заявки")
    customer_id: str = Field(description="Клиент")
    requested_limit: float = Field(description="Запрошенный лимит, ₽")
    status: Literal["approved", "declined", "pending"] = Field(
        description="Решение по заявке"
    )
    decided_at: str = Field(description="Дата решения")
    public_reason: str = Field(description="Категория причины для клиента")


class InternetOperationConfirmation(BaseModel):
    card_last4: str = Field(pattern=r"^\d{4}$")
    enabled: bool
    confirmed: bool


class UtilityPaymentAccounts(BaseModel):
    """Supplier personal-account identifiers supplied by the client, not bank IDs."""

    wrong_account: str = Field(min_length=1)
    correct_account: str = Field(min_length=1)


class OperationConfirmation(BaseModel):
    """Fixed protected-interface decision bound to a specific payment."""

    transaction_id: str
    account_id: str
    amount: float = Field(gt=0, allow_inf_nan=False)
    merchant: str
    date: str
    confirmed: bool


class RestructuringDocumentAvailability(BaseModel):
    """Documents the client can supply for a particular own loan."""

    loan_id: str
    documents: list[Literal["income_certificate", "employer_order"]]
    available_within_days: int = Field(gt=0)


class TariffChangeConfirmation(BaseModel):
    """One fixed client decision about a priced, dated servicing tariff."""

    new_tariff_id: str
    monthly_fee: float = Field(ge=0, allow_inf_nan=False)
    effective_from: str
    confirmed: bool


class AccountClosureSignature(BaseModel):
    """Fixed client's app decision and existing electronic-interaction agreement."""

    model_config = ConfigDict(extra="forbid")
    account_id: str
    payout_account_id: str
    confirmed: bool
    agreement_id: Optional[Literal["bank-app-pep-v1"]] = None


class ClientAnswer(BaseModel):
    """Ответ клиента на вопрос агента — заранее записанный, без модели.

    В тикетном формате живого собеседника нет, но механизм «агент обязан
    догадаться спросить» сохраняется: то, чего нет в системе, лежит здесь и
    выдаётся только по подходящему вопросу. В отличие от симулятора клиента,
    оракул не проговаривается: он молчит, пока его не спросят по делу.
    """

    keywords: list[str] = Field(
        description="Слова, по которым вопрос агента считается заданным по делу"
    )
    answer: str = Field(description="Что отвечает клиент")
    restructuring_documents: Optional[RestructuringDocumentAvailability] = None
    operation_answers: Optional[dict[str, bool]] = None
    device_answers: Optional[dict[str, bool]] = None
    operation_confirmation: Optional[OperationConfirmation] = None
    loan_repayment_confirmation: Optional["LoanRepaymentConfirmation"] = None
    internet_operations_confirmation: Optional[InternetOperationConfirmation] = None
    utility_payment_accounts: Optional[UtilityPaymentAccounts] = None
    tariff_change_confirmation: Optional[TariffChangeConfirmation] = None
    account_closure_signature: Optional[AccountClosureSignature] = None
    identity_method: Optional[Literal["code_word", "birth_date"]] = Field(
        default=None,
        description="Явный метод идентификации для структурированного запроса",
    )
    credential: Optional[str] = Field(
        default=None, description="Секрет в фиксированном ответе клиента, не из профиля"
    )


class Article(BaseModel):
    """Статья базы знаний банка.

    База знаний — не копия политики, а её продолжение: политика говорит, где
    искать правило, а сама формулировка правила, формула расчёта и исключения
    лежат здесь. Три свойства делают работу с ней недешёвой и повторяют
    устройство знаниевых доменов τ³:

    - статьи ссылаются друг на друга, и исключение из правила лежит в
      отдельной статье, а не в той, которую агент нашёл первой;
    - у статьи есть срок действия: недействующая редакция остаётся в базе,
      находится поиском и выглядит применимой;
    - рядом лежат почти одинаковые статьи для разных продуктов и тарифов.
    """

    id: str = Field(description="Идентификатор статьи")
    section: str = Field(description="Раздел базы знаний")
    title: str = Field(description="Название статьи")
    body: str = Field(description="Полный текст статьи")
    keywords: list[str] = Field(
        default_factory=list, description="Ключевые слова для поиска"
    )
    effective_from: str = Field(description="Дата вступления редакции в силу")
    effective_to: Optional[str] = Field(
        default=None, description="Дата, до которой редакция действует"
    )
    superseded_by: Optional[str] = Field(
        default=None, description="Статья, заменившая эту редакцию"
    )


class Document(BaseModel):
    """Документ из системы банка, относящийся к клиенту.

    Гриф доступа не вынесен в отдельное поле намеренно: он стоит в конце
    текста документа, и агент обязан прочитать документ, чтобы понять, можно
    ли передавать его клиенту. Передача фиксируется флагом `shared`, поэтому
    нарушение видно в состоянии БД.
    """

    id: str = Field(description="Идентификатор документа")
    customer_id: str = Field(description="Клиент, к которому относится документ")
    title: str = Field(description="Название документа")
    body: str = Field(description="Полный текст документа")
    created_at: str = Field(description="Дата документа")
    shared: bool = Field(
        default=False, description="Документ передан клиенту через share_document"
    )


class Subscription(BaseModel):
    id: str = Field(description="Идентификатор подписки")
    customer_id: str = Field(description="Клиент")
    name: str = Field(description="Название подписки")
    amount: float = Field(description="Стоимость в месяц, ₽")
    status: SubscriptionStatus = Field(default="active", description="Статус подписки")
    paid_until: Optional[str] = Field(
        default=None, description="Дата окончания оплаченного периода"
    )
    cancelled_at: Optional[str] = Field(default=None, description="Дата отмены")
    next_charge_date: Optional[str] = Field(
        default=None, description="Дата следующего списания"
    )


class Autopayment(BaseModel):
    id: str = Field(description="Идентификатор автоплатежа")
    customer_id: str = Field(description="Клиент")
    merchant: str = Field(description="Получатель автоплатежа")
    amount: float = Field(description="Сумма автоплатежа, ₽")
    status: SubscriptionStatus = Field(
        default="active", description="Статус автоплатежа"
    )
    cancelled_at: Optional[str] = Field(default=None, description="Дата отмены")


class ComparisonTerms(BaseModel):
    """Contract assumptions for a bank-system comparison, not an instruction."""

    contract_id: str
    contract_date: str
    as_of: str
    currency: Literal["RUB"] = "RUB"
    day_count: Literal["ACT/365"] = "ACT/365"
    interest_method: Literal["simple"] = "simple"
    interest_paid_before_as_of: float = Field(default=0, ge=0, allow_inf_nan=False)
    scheduled_payments: list[dict] = Field(default_factory=list)
    minimum_notice_days: int = Field(default=0, ge=0)
    tax_scope: Literal["before_individual_annual_tax"] = "before_individual_annual_tax"
    assumptions: list[str]
    penalty_basis: Optional[dict] = None


class DepositComparisonQuote(BaseModel):
    """Authoritative payout on one date, based on the declared contract."""

    id: str
    date: str
    available_on: str
    principal: float = Field(ge=0, allow_inf_nan=False)
    interest_due: float = Field(ge=0, allow_inf_nan=False)
    payout: float = Field(ge=0, allow_inf_nan=False)
    source: Literal["deposit_system"] = "deposit_system"


class LoanComparisonQuote(BaseModel):
    """Full settlement amount for consultation; no execution permission."""

    id: str
    date: str
    principal: float = Field(ge=0, allow_inf_nan=False)
    interest: float = Field(ge=0, allow_inf_nan=False)
    penalties: float = Field(ge=0, allow_inf_nan=False)
    fees: float = Field(default=0, ge=0, allow_inf_nan=False)
    total: float = Field(ge=0, allow_inf_nan=False)
    penalty_basis_confirmed: bool
    source: Literal["credit_system"] = "credit_system"


class DepositRows(RootModel[list[dict]]):
    """Keep numeric/boolean values intact in tool-result serialization."""


class Deposit(BaseModel):
    id: str = Field(description="Идентификатор вклада")
    customer_id: str = Field(description="Клиент")
    name: Optional[str] = Field(default=None, description="Название вклада")
    amount: float = Field(description="Сумма вклада, ₽")
    rate: float = Field(description="Ставка по договору, доля единицы (0.09 = 9%)")
    early_rate: Optional[float] = Field(
        default=None,
        description="Ставка при досрочном расторжении, доля единицы. Если задана, "
        "early_interest должен из неё выводиться.",
    )
    opened_at: str = Field(description="Дата открытия вклада")
    matures_at: str = Field(description="Дата окончания срока вклада")
    payout_account_id: str = Field(description="Счёт зачисления процентов и выплаты")
    comparison_terms: Optional[ComparisonTerms] = None
    comparison_quotes: list[DepositComparisonQuote] = Field(default_factory=list)
    # Начисленные проценты по договорной и пониженной ставке НЕ хранятся
    # готовыми полями: их вычисление из ставок и дат — работа агента (M1).
    early_withdrawal_payout: float = Field(
        description="Сумма к выплате при досрочном расторжении сегодня, ₽"
    )
    maturity_payout: float = Field(description="Сумма к выплате при закрытии в срок, ₽")
    status: DepositStatus = Field(default="active", description="Статус вклада")
    closed_at: Optional[str] = Field(default=None, description="Дата закрытия вклада")


class Penalty(BaseModel):
    id: str = Field(description="Идентификатор штрафа")
    amount: float = Field(description="Сумма штрафа, ₽")
    accrued_at: str = Field(description="Дата начисления штрафа")
    reason: Optional[str] = Field(default=None, description="Причина штрафа")
    waived: bool = Field(default=False, description="Штраф списан банком")
    paid: bool = Field(default=False, description="Штраф оплачен клиентом")


class LoanRepaymentQuote(BaseModel):
    """Authoritative credit-system snapshot, not an agent-computed schedule."""

    quote_id: str
    account_id: str
    date: str
    notice_registered_at: str
    minimum_notice_days: int = Field(ge=0)
    amount: float = Field(gt=0, allow_inf_nan=False)
    mode: RepaymentMode
    initial_principal: float = Field(gt=0, allow_inf_nan=False)
    initial_interest: float = Field(ge=0, allow_inf_nan=False)
    principal_payment: float = Field(gt=0, allow_inf_nan=False)
    interest_payment: float = Field(ge=0, allow_inf_nan=False)
    new_monthly_payment: float = Field(ge=0, allow_inf_nan=False)
    new_next_payment_date: Optional[str] = None


class LoanRepaymentConfirmation(BaseModel):
    """Client's protected-app decision about one fully specified instruction."""

    loan_id: str
    account_id: str
    quote_id: str
    amount: float = Field(gt=0, allow_inf_nan=False)
    mode: RepaymentMode
    date: str
    confirmed: bool


class Loan(BaseModel):
    id: str = Field(description="Идентификатор кредита")
    customer_id: str = Field(description="Заёмщик")
    contract_date: Optional[str] = Field(
        default=None, description="Дата заключения кредитного договора, YYYY-MM-DD"
    )
    principal: float = Field(description="Остаток основного долга, ₽")
    accrued_interest: float = Field(
        description="Проценты, начисленные с последней даты платежа, ₽"
    )
    rate: float = Field(description="Ставка, доля единицы (0.149 = 14,9%)")
    monthly_payment: float = Field(description="Ежемесячный платёж, ₽")
    overdue_payment_amount: Optional[float] = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="Просроченная часть обязательного платежа по расчёту банка, ₽; "
        "без отдельно перечисленных штрафов. None означает, что расчёт отсутствует; "
        "monthly_payment и остаток основного долга его не заменяют.",
    )
    days_overdue: int = Field(default=0, description="Дней просрочки")
    next_payment_date: Optional[str] = Field(
        default=None, description="Дата следующего платежа"
    )
    penalties: list[Penalty] = Field(
        default_factory=list, description="История штрафов по кредиту"
    )
    waivers_used: int = Field(
        default=0,
        description="Сколько раз за срок кредита уже применялось списание штрафа",
    )
    max_waivers: int = Field(
        default=1, description="Сколько списаний штрафа допускает политика банка"
    )
    status: Literal["active", "closed"] = Field(
        default="active", description="Статус кредита"
    )
    comparison_terms: Optional[ComparisonTerms] = None
    comparison_quotes: list[LoanComparisonQuote] = Field(default_factory=list)
    repayment_quotes: list[LoanRepaymentQuote] = Field(
        default_factory=list,
        description="Расчёты кредитной системы с условиями уведомления и новым графиком",
    )


class CashbackCategory(BaseModel):
    name: str = Field(description="Название категории")
    rate: float = Field(description="Ставка кешбэка, доля единицы (0.05 = 5%)")
    mcc_codes: list[str] = Field(
        default_factory=list, description="MCC-коды, входящие в категорию"
    )


class CashbackRules(BaseModel):
    customer_id: str = Field(description="Клиент")
    categories: list[CashbackCategory] = Field(
        default_factory=list, description="Категории и ставки кешбэка"
    )
    excluded_mcc: list[str] = Field(
        default_factory=list, description="MCC-коды, исключённые из начисления"
    )
    payout_day: int = Field(
        default=5, description="День следующего месяца, когда начисляется кешбэк"
    )
    accrued_current_period: float = Field(
        default=0.0, description="Кешбэк, начисленный за текущий период, ₽"
    )


class Session(BaseModel):
    """Identification state of the current conversation.

    Excluded from `model_dump`, therefore invisible to the DB hash: identifying
    the customer must never by itself change the graded end state.
    """

    verified_customers: list[str] = Field(default_factory=list)
    otp_sent: list[str] = Field(default_factory=list)
    otp_verified: list[str] = Field(default_factory=list)


class BankingDB(DB):
    today: str = Field(description="Фиксированная текущая дата домена, ГГГГ-ММ-ДД")
    dispute_window_days: int = Field(
        default=120,
        description="Срок, в течение которого операцию можно оспорить, дней",
    )
    tariffs: dict[str, Tariff] = Field(default_factory=dict)
    customers: dict[str, Customer] = Field(default_factory=dict)
    accounts: dict[str, Account] = Field(default_factory=dict)
    cards: dict[str, Card] = Field(default_factory=dict)
    card_limits: dict[str, CardLimits] = Field(default_factory=dict)
    transactions: dict[str, Transaction] = Field(default_factory=dict)
    disputes: dict[str, Dispute] = Field(default_factory=dict)
    cases: dict[str, Case] = Field(default_factory=dict)
    devices: dict[str, Device] = Field(default_factory=dict)
    promotions: dict[str, Promotion] = Field(default_factory=dict)
    statements: dict[str, Statement] = Field(default_factory=dict)
    limit_requests: dict[str, LimitRequest] = Field(default_factory=dict)
    documents: dict[str, Document] = Field(default_factory=dict)
    articles: dict[str, Article] = Field(default_factory=dict)
    client_answers: dict[str, list[ClientAnswer]] = Field(
        default_factory=dict,
        description="Что клиент может рассказать, если агент спросит",
    )
    #: Ответ агента по обращению. Исключён из дампа, а значит и из хеша БД:
    #: свободный текст в хеше обнулял бы задачу при верном по смыслу ответе.
    #: Проверяется отдельно — assert_answer_contains.
    ticket_replies: dict[str, str] = Field(default_factory=dict, exclude=True)
    #: Журнал вызовов инструментов. Тоже вне дампа и хеша: он нужен проверкам,
    #: а не сверке состояния. Позволяет наказывать за само действие, а не
    #: только за его след — лишнюю запись, отменённую следующей, хеш не видит.
    tool_calls_log: list[str] = Field(default_factory=list, exclude=True)
    subscriptions: dict[str, Subscription] = Field(default_factory=dict)
    autopayments: dict[str, Autopayment] = Field(default_factory=dict)
    deposits: dict[str, Deposit] = Field(default_factory=dict)
    loans: dict[str, Loan] = Field(default_factory=dict)
    cashback_rules: dict[str, CashbackRules] = Field(default_factory=dict)
    session: Session = Field(default_factory=Session, exclude=True)

    def get_statistics(self) -> dict:
        return {
            "num_customers": len(self.customers),
            "num_accounts": len(self.accounts),
            "num_cards": len(self.cards),
            "num_transactions": len(self.transactions),
            "num_disputes": len(self.disputes),
            "num_cases": len(self.cases),
            "num_devices": len(self.devices),
            "num_subscriptions": len(self.subscriptions),
            "num_deposits": len(self.deposits),
            "num_loans": len(self.loans),
        }


ClientAnswer.model_rebuild()
