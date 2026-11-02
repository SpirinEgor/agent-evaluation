"""Explicit system ownership of the existing banking tools; no name guessing."""

from collections import Counter
from typing import Literal

import mcp.types as t

MCPLayout = Literal["single", "systems", "directory"]
SINGLE_SERVER = "banking_ru_pilot"

SYSTEMS = {
    "crm": "Клиентский справочник и реестр обращений в подразделения банка.",
    "identity": "Идентификация клиента и подтверждение операций одноразовыми кодами.",
    "support": "Канал общения с клиентом, передача специалисту и завершение сессии.",
    "knowledge": "Поиск и чтение политик и справочных документов банка.",
    "cards": "Карточный процессинг: карта, блокировки, перевыпуск и лимиты.",
    "payments": "История и детали платежей, холды, переводы и возвраты по операциям.",
    "disputes": "Карточные споры: просмотр, регистрация, отзыв и закрытие.",
    "core_banking": "АБС: счета, вклады, обслуживание кредитов и учетные операции; калькулятор.",
    "servicing": "Регулярные банковские услуги, подписки, автоплатежи и тарифы обслуживания.",
    "loyalty": "Программа лояльности: правила, акции и начисление кешбэка.",
    "digital_access": "Устройства клиента и восстановление доступа к приложению.",
    "documents": "Документы клиента, заказ выписок и защищенная передача.",
    "risk": "Разрешенное снятие блокировки безопасности с операции.",
}

TOOL_OWNERS = {
    "get_payment_basis": "payments",
    "get_payment_allocations": "crm",
    "reconcile_payment_allocation": "crm",
    "list_documents": "knowledge",
    "search_documents": "knowledge",
    "list_revisions": "knowledge",
    "read_document": "knowledge",
    "unlock_discoverable_agent_tool": "support",
    "call_discoverable_agent_tool": "support",
    "give_discoverable_user_tool": "support",
    "submit_cashback_review": "crm",
    "register_reward_statement": "loyalty",
    "get_reward_statement_status": "loyalty",
    "corpus_command": "knowledge",
    "find_customer": "crm",
    "get_customer_profile": "crm",
    "create_case": "crm",
    "create_cashback_investigation": "crm",
    "get_cashback_investigations": "crm",
    "amend_cashback_investigation": "crm",
    "get_bank_operation_status": "core_banking",
    "get_bank_date": "core_banking",
    "open_card_loss_case": "crm",
    "notify_card_loss_case": "crm",
    "verify_identity": "identity",
    "send_otp": "identity",
    "check_otp": "identity",
    "ask_client": "support",
    "reply_to_ticket": "support",
    "escalate_to_human": "support",
    "done": "support",
    "file_list": "knowledge",
    "file_find": "knowledge",
    "file_search": "knowledge",
    "file_read": "knowledge",
    "get_cards": "cards",
    "get_limits": "cards",
    "get_limit_request": "cards",
    "block_card": "cards",
    "unblock_card": "cards",
    "freeze_card": "cards",
    "reissue_card": "cards",
    "set_limit": "cards",
    "reveal_card_details": "cards",
    "get_transactions": "payments",
    "get_operations": "payments",
    "get_transaction_details": "payments",
    "release_hold": "payments",
    "refund_transaction": "payments",
    "transfer_between_own_accounts": "payments",
    "get_disputes": "disputes",
    "open_dispute": "disputes",
    "cancel_dispute": "disputes",
    "close_dispute": "disputes",
    "get_accounts": "core_banking",
    "get_deposit": "core_banking",
    "get_loans": "core_banking",
    "get_loan_cases": "crm",
    "open_account": "core_banking",
    "close_account": "core_banking",
    "check_account_closure": "core_banking",
    "request_account_closure": "core_banking",
    "open_deposit": "core_banking",
    "close_deposit": "core_banking",
    "early_repayment": "core_banking",
    "waive_penalty": "core_banking",
    "request_restructuring_documents": "core_banking",
    "request_credit_holidays": "core_banking",
    "calculate": "core_banking",
    "refund_fee": "core_banking",
    "get_fee_refund_state": "core_banking",
    "get_subscriptions": "servicing",
    "cancel_subscription": "servicing",
    "cancel_autopayment": "servicing",
    "get_tariff": "servicing",
    "list_tariffs": "servicing",
    "change_tariff": "servicing",
    "get_cashback_rules": "loyalty",
    "get_promotions": "loyalty",
    "get_reward_contracts": "loyalty",
    "get_reward_reprocessing_cases": "loyalty",
    "repair_reward_binding": "loyalty",
    "reprocess_rewards": "loyalty",
    "retry_reward_resolution": "loyalty",
    "amend_reward_resolution": "loyalty",
    "get_service_incidents": "crm",
    "get_partner_confirmations": "loyalty",
    "get_promotion_activations": "loyalty",
    "grant_cashback": "loyalty",
    "get_reward_resolution_offers": "loyalty",
    "get_reward_resolution_components": "loyalty",
    "execute_reward_resolution": "loyalty",
    "get_reward_resolution_status": "loyalty",
    "get_cashback_adjustment_permissions": "loyalty",
    "execute_cashback_adjustment": "loyalty",
    "refresh_cashback_settlement": "loyalty",
    "get_cashback_adjustment_status": "loyalty",
    "get_devices": "digital_access",
    "unblock_device": "digital_access",
    "get_documents": "documents",
    "get_case_documents": "documents",
    "get_document_status": "documents",
    "get_document_delivery_contacts": "documents",
    "deliver_case_document": "documents",
    "order_statement": "documents",
    "share_document": "documents",
    "unblock_operation": "risk",
}


def partition_tools(tools: list[t.Tool]) -> dict[str, list[t.Tool]]:
    """Partition the full catalog, failing closed for duplicates or new tools."""
    duplicates = [
        name for name, count in Counter(t.name for t in tools).items() if count > 1
    ]
    unknown = [tool.name for tool in tools if tool.name not in TOOL_OWNERS]
    if duplicates or unknown:
        raise ValueError(
            f"Invalid banking catalog: duplicates={duplicates}, unmapped={unknown}"
        )
    groups: dict[str, list[t.Tool]] = {name: [] for name in SYSTEMS}
    for tool in tools:
        groups[TOOL_OWNERS[tool.name]].append(tool)
    return groups
