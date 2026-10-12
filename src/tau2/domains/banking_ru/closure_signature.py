"""Canonical application document; signature keys stay inside the simulator."""

import hashlib
import json


def closure_document(customer, account, payout, today):
    """Build the exact client-visible instruction before the app signs it."""
    return {
        "version": "account-closure-v1",
        "title": "Заявление о прекращении договора банковского счёта",
        "customer_id": customer.id,
        "full_name": customer.full_name,
        "account_id": account.id,
        "payout_account_id": payout.id,
        "currency": account.currency,
        "date": today,
        "payout_full_balance": True,
        "payout_after_settlement": True,
        "close_linked_services_by_department": True,
        "immediate_payment": False,
        "channel": "bank_app",
        "agreement_id": "bank-app-pep-v1",
        "text": (
            "Прошу прекратить договор указанного счёта. Поручаю перечислить весь "
            "доступный остаток на указанный собственный счёт после завершения "
            "расчётов. Связанные услуги завершает профильное подразделение. "
            "Подписание не означает немедленного перевода или закрытия карты."
        ),
    }


def document_hash(document):
    """Hash canonical UTF-8 JSON including every instruction field."""
    payload = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()
