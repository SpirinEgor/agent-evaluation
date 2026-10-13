"""Explicit system ownership of the existing banking tools; no name guessing."""

from collections import Counter
from typing import Literal

import mcp.types as t

MCPLayout = Literal["single", "systems"]
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
    "find_customer": "crm",
    "get_customer_profile": "crm",
    "create_case": "crm",
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
    "get_subscriptions": "servicing",
    "cancel_subscription": "servicing",
    "cancel_autopayment": "servicing",
    "get_tariff": "servicing",
    "list_tariffs": "servicing",
    "change_tariff": "servicing",
    "get_cashback_rules": "loyalty",
    "get_promotions": "loyalty",
    "grant_cashback": "loyalty",
    "get_devices": "digital_access",
    "unblock_device": "digital_access",
    "get_documents": "documents",
    "order_statement": "documents",
    "share_document": "documents",
    "unblock_operation": "risk",
    "search_knowledge": "knowledge",
    "get_article": "knowledge",
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


def startup_policy() -> str:
    """Return the shared entrypoint without system maps or document targets."""
    return (
        "# AGENTS.md\n\n"
        "Вы — агент банковской поддержки. Решите обращение клиента через подключённые MCP банковских систем.\n"
        "Политики и справочные материалы доступны через MCP. Самостоятельно найдите применимые документы.\n"
        "После выполнения работы и получения результатов вызовите done. Итоговый ответ клиенту не отправляйте. Структурированные запросы клиенту разрешены.\n"
    )
