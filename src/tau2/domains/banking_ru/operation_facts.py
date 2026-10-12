"""Banking facts from successfully received results, never private initial DB."""

import json
from decimal import Decimal, InvalidOperation
from typing import get_args

from tau2.domains.banking_ru.data_model import CaseCategory
from tau2.evaluator.claim_models import OperationFact, current_state_facts_at
from tau2.evaluator.trace_evidence import ObservedCall, TraceEvidence


def _money(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def _id(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _enum(value, allowed: set[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _fact(
    observed: ObservedCall,
    family,
    kind,
    resources,
    values,
    amount=None,
    invalidated_fields=None,
    invalidation_scope=None,
):
    return OperationFact(
        family=family,
        kind=kind,
        resources=resources,
        values=values,
        amount=amount,
        call_id=observed.call.id,
        requestor=observed.call.requestor,
        received_position=observed.result_position,
        execution_index=observed.execution_index,
        invalidated_fields=invalidated_fields or [],
        invalidation_scope=invalidation_scope or {},
    )


def _main_adapter(observed: ObservedCall, payload: dict) -> list[OperationFact]:
    name, args = observed.call.name, observed.call.arguments
    for field in ("reason", "resolution", "limit_type", "mode", "category"):
        if field in args and not isinstance(args[field], str):
            return []
    if name == "block_card":
        card = args.get("card_id")
        if not (
            _id(card)
            and payload.get("id") == card
            and payload.get("status") == "blocked"
            and args.get("reason") in {"lost", "stolen", "temporary", "fraud_suspected"}
            and payload.get("block_reason") == args.get("reason")
        ):
            return []
        return [
            _fact(
                observed,
                "card",
                "completed_action",
                {"card_id": card},
                {"status": "blocked", "block_reason": payload["block_reason"]},
            )
        ]
    if name == "open_dispute":
        transaction = args.get("transaction_id")
        amount = _money(payload.get("amount"))
        if not (
            _id(transaction)
            and _id(payload.get("id"))
            and payload.get("transaction_id") == transaction
            and payload.get("status") == "under_review"
            and _enum(
                args.get("reason"),
                {
                    "fraud_suspected",
                    "service_not_provided",
                    "duplicate_charge",
                    "subscription_after_cancel",
                    "wrong_amount",
                    "other",
                },
            )
            and _id(payload.get("filed_at"))
            and payload.get("reason") == args.get("reason")
            and amount is not None
            and (args.get("amount") is None or _money(args["amount"]) == amount)
        ):
            return []
        return [
            _fact(
                observed,
                "dispute",
                "accepted_request",
                {"dispute_id": payload["id"], "transaction_id": transaction},
                {"status": "under_review"},
                amount,
            )
        ]
    if name == "transfer_between_own_accounts":
        source, target = args.get("from_account_id"), args.get("to_account_id")
        amount = _money(payload.get("amount"))
        if not (
            _id(source)
            and _id(target)
            and source != target
            and amount is not None
            and amount > 0
            and _money(args.get("amount")) == amount
            and _money(payload.get("from_balance")) is not None
            and _money(payload.get("to_balance")) is not None
        ):
            return []
        return [
            _fact(
                observed,
                "transfer",
                "completed_action",
                {"from_account_id": source, "to_account_id": target},
                {
                    "from_balance": str(_money(payload["from_balance"])),
                    "to_balance": str(_money(payload["to_balance"])),
                },
                amount,
            )
        ]
    if name == "close_deposit":
        deposit = payload.get("deposit")
        payout = _money(payload.get("payout"))
        if not (
            isinstance(deposit, dict)
            and _id(args.get("deposit_id"))
            and deposit.get("id") == args["deposit_id"]
            and deposit.get("status") == "closed"
            and _id(deposit.get("closed_at"))
            and _id(args.get("payout_account_id"))
            and payout is not None
            and payout > 0
            and _money(payload.get("account_balance")) is not None
            and _boolean(payload.get("early_withdrawal")) is not None
        ):
            return []
        return [
            _fact(
                observed,
                "deposit",
                "completed_action",
                {
                    "deposit_id": args["deposit_id"],
                    "payout_account_id": args["payout_account_id"],
                },
                {
                    "status": "closed",
                    "early_withdrawal": str(payload["early_withdrawal"]),
                },
                payout,
            )
        ]
    return []


def _boolean(value) -> bool | None:
    # Exact wire forms: dict serialization emits title-case boolean strings.
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value in {"True", "False"}:
        return value == "True"
    return None


# Each tuple names the financial field, its interpretation, and optional enum.
FIELDS = {
    "card": {
        "status": {"active", "blocked", "expired"},
        "block_reason": {"lost", "stolen", "temporary", "fraud_suspected"},
    },
    "card_reissue": {"reissue_status": {"ordered", "in_delivery"}},
    "account": {
        "customer_id": "text",
        "status": {"active", "closed"},
        "balance": "money",
        "debt": "money",
        "currency": "text",
    },
    "transaction": {
        "account_id": "text",
        "customer_id": "text",
        "status": {"posted", "hold", "declined", "processing", "blocked"},
        "hold_expires_at": "text",
        "fee_refunded": "bool",
        "cashback_granted": "bool",
    },
    "dispute": {
        "status": {"under_review", "approved", "rejected", "cancelled"},
        "amount": "money",
        "filed_at": "text",
        "sla_days": "number",
    },
    "subscription": {
        "status": {"active", "cancelled"},
        "amount": "money",
        "paid_until": "text",
        "next_charge_date": "text",
    },
    "autopayment": {"status": {"active", "cancelled"}, "amount": "money"},
    "deposit": {
        "status": {"active", "closed"},
        "amount": "money",
        "rate": "number",
        "early_rate": "number",
        "opened_at": "text",
        "matures_at": "text",
        "payout_account_id": "text",
    },
    "loan": {
        "customer_id": "text",
        "status": {"active", "closed"},
        "principal": "money",
        "accrued_interest": "money",
        "monthly_payment": "money",
        "next_payment_date": "text",
        "days_overdue": "number",
        "waivers_used": "number",
    },
    "penalty": {
        "amount": "money",
        "paid": "bool",
        "waived": "bool",
        "accrued_at": "text",
    },
    "device": {"blocked": "bool"},
    "document": {"shared": "bool"},
    "customer": {
        "tariff_id": "text",
        "credit_limit": "money",
        "pending_tariff_id": "text",
        "pending_tariff_from": "text",
        "avg_monthly_balance": "money",
        "monthly_turnover": "money",
    },
    "tariff": {
        "monthly_fee": "money",
        "max_daily_cash_withdrawal": "money",
        "max_sbp_limit": "money",
        "foreign_account_opening_fee": "money",
        "foreign_account_monthly_fee": "money",
        "free_min_balance": "money",
        "free_min_turnover": "money",
    },
    "cashback_rules": {"payout_day": "number", "accrued_current_period": "money"},
    "promotion": {
        "reward": "money",
        "min_purchase_amount": "money",
        "required_purchases": "number",
        "valid_until": "text",
    },
    "credit_limit_request": {
        "status": {"pending", "approved", "declined"},
        "requested_limit": "money",
        "decided_at": "text",
    },
    "case": {"status": {"open", "documents_requested", "notified", "closed"}},
}


def _snapshot(observed, family, resources, payload):
    """Whitelist scalar financial fields; never retain raw records or free prose."""
    result = []
    for field, interpretation in FIELDS.get(family, {}).items():
        value = payload.get(field)
        if value is None:
            if field in payload and field in {
                "block_reason",
                "hold_expires_at",
                "next_charge_date",
                "pending_tariff_id",
                "pending_tariff_from",
                "next_payment_date",
                "reissue_status",
            }:
                result.append(
                    _fact(observed, family, "current_state", resources, {field: "None"})
                )
            continue
        amount = None
        if interpretation in ("money", "number"):
            decimal = _money(value)
            if decimal is None:
                continue
            value = str(decimal)
            if interpretation == "money":
                amount = decimal
        elif interpretation == "bool":
            boolean = _boolean(value)
            if boolean is None:
                continue
            value = str(boolean)
        elif interpretation == "text":
            if not isinstance(value, str) or not value:
                continue
        elif not isinstance(value, str) or value not in interpretation:
            continue
        result.append(
            _fact(observed, family, "current_state", resources, {field: value}, amount)
        )
        if family == "card" and field == "status":
            # A validated observation remains evidence of this observed status
            # after current state changes. Never infer the pre-write status.
            result.append(
                _fact(observed, family, "historical", resources, {field: value})
            )
    return result


def _limits(observed, payload):
    card = payload.get("card_id")
    if not _id(card):
        return []
    result = []
    for field in ("daily_cash_withdrawal", "sbp_limit"):
        amount = _money(payload.get(field))
        if amount is not None:
            result.append(
                _fact(
                    observed,
                    "card_limit",
                    "current_state",
                    {"card_id": card},
                    {field: str(amount)},
                    amount,
                )
            )
    boolean = _boolean(payload.get("internet_operations_enabled"))
    if boolean is not None:
        result.append(
            _fact(
                observed,
                "internet_operations",
                "current_state",
                {"card_id": card},
                {"enabled": str(boolean)},
            )
        )
    return result


def _write(observed, payload):
    """Validate each declared business outcome before publishing its effect."""
    name, args = observed.call.name, observed.call.arguments
    for field in ("reason", "resolution", "limit_type", "mode", "category"):
        if field in args and not isinstance(args[field], str):
            return []
    if name in {
        "block_card",
        "open_dispute",
        "transfer_between_own_accounts",
        "close_deposit",
    }:
        actions = _main_adapter(observed, payload)
        if not actions:
            return []
        action = actions[0]
        if name == "block_card":
            return actions + _snapshot(
                observed, "card", {"card_id": args["card_id"]}, payload
            )
        if name == "open_dispute":
            return actions + _snapshot(
                observed, "dispute", {"dispute_id": payload["id"]}, payload
            )
        if name == "close_deposit":
            return (
                actions
                + _snapshot(
                    observed,
                    "deposit",
                    {"deposit_id": args["deposit_id"]},
                    payload["deposit"],
                )
                + _snapshot(
                    observed,
                    "account",
                    {"account_id": args["payout_account_id"]},
                    {"balance": payload["account_balance"]},
                )
            )
        return (
            actions
            + _snapshot(
                observed,
                "account",
                {"account_id": args["from_account_id"]},
                {"balance": payload["from_balance"]},
            )
            + _snapshot(
                observed,
                "account",
                {"account_id": args["to_account_id"]},
                {"balance": payload["to_balance"]},
            )
        )
    # Record replies carrying an explicit selector and a definitive status.
    specifications = {
        "unblock_card": ("card", "card_id", "completed_action", "status", "active"),
        "freeze_card": ("card", "card_id", "completed_action", "status", "blocked"),
        "reissue_card": (
            "card_reissue",
            "card_id",
            "accepted_request",
            "reissue_status",
            "ordered",
        ),
        "cancel_dispute": (
            "dispute",
            "dispute_id",
            "completed_action",
            "status",
            "cancelled",
        ),
        "close_dispute": (
            "dispute",
            "dispute_id",
            "completed_action",
            "status",
            args.get("resolution")
            if args.get("resolution") in {"approved", "rejected"}
            else None,
        ),
        "release_hold": (
            "hold",
            "transaction_id",
            "completed_action",
            "status",
            "declined",
        ),
        "notify_card_loss_case": (
            "case",
            "case_id",
            "completed_action",
            "status",
            "notified",
        ),
        "request_restructuring_documents": (
            "case",
            "case_id",
            "completed_action",
            "status",
            "documents_requested",
        ),
        "cancel_subscription": (
            "subscription",
            "subscription_id",
            "completed_action",
            "status",
            "cancelled",
        ),
        "cancel_autopayment": (
            "autopayment",
            "autopayment_id",
            "completed_action",
            "status",
            "cancelled",
        ),
        "close_account": (
            "account",
            "account_id",
            "completed_action",
            "status",
            "closed",
        ),
        "unblock_device": ("device", "device_id", "completed_action", "blocked", False),
        "unblock_operation": (
            "transaction",
            "transaction_id",
            "completed_action",
            "status",
            "posted",
        ),
        "share_document": (
            "document",
            "document_id",
            "completed_action",
            "shared",
            True,
        ),
    }
    if name in specifications:
        family, selector, kind, field, wanted = specifications[name]
        target = args.get(selector)
        actual = (
            _boolean(payload.get(field))
            if isinstance(wanted, bool)
            else payload.get(field)
        )
        if not (
            _id(target)
            and payload.get("id") == target
            and wanted is not None
            and actual == wanted
        ):
            return []
        if name == "freeze_card" and payload.get("block_reason") != "temporary":
            return []
        if name == "unblock_card" and payload.get("block_reason") is not None:
            return []
        if name == "release_hold" and (
            "hold_expires_at" not in payload or payload["hold_expires_at"] is not None
        ):
            return []
        if name == "notify_card_loss_case" and payload.get("category") != "card_loss":
            return []
        if name == "request_restructuring_documents" and (
            payload.get("category") != "restructuring"
            or args.get("documents") != ["income_certificate", "employer_order"]
            or payload.get("requested_documents") != args["documents"]
        ):
            return []
        if name == "close_account" and (
            _money(payload.get("balance")) != 0 or _money(payload.get("debt")) != 0
        ):
            return []
        values = {field: str(actual)}
        if name == "freeze_card":
            values["block_reason"] = "temporary"
        resources = {selector: target}
        snapshots = _snapshot(
            observed, "transaction" if family == "hold" else family, resources, payload
        )
        return [_fact(observed, family, kind, resources, values)] + snapshots
    if name == "set_limit":
        card, channel = args.get("card_id"), args.get("limit_type")
        if not (_id(card) and payload.get("card_id") == card):
            return []
        if channel == "internet_operations":
            requested, actual = (
                _boolean(args.get("enabled")),
                _boolean(payload.get("internet_operations_enabled")),
            )
            if requested is None or actual is None or requested != actual:
                return []
            action = _fact(
                observed,
                "internet_operations",
                "completed_action",
                {"card_id": card},
                {"enabled": str(actual)},
            )
        elif channel in {"daily_cash_withdrawal", "sbp"}:
            field = "sbp_limit" if channel == "sbp" else channel
            requested, actual = _money(args.get("amount")), _money(payload.get(field))
            if requested is None or actual is None or requested != actual:
                return []
            action = _fact(
                observed,
                "card_limit",
                "completed_action",
                {"card_id": card},
                {"limit_type": channel},
                actual,
            )
        else:
            return []
        return [action] + _limits(observed, payload)
    if name in {
        "create_case",
        "open_card_loss_case",
        "request_credit_holidays",
        "escalate_to_human",
    }:
        category = {
            "open_card_loss_case": "card_loss",
            "request_credit_holidays": "credit_holidays",
            "escalate_to_human": "escalation",
        }.get(name, args.get("category"))
        if not (
            _id(payload.get("id"))
            and payload.get("status") == "open"
            and _enum(category, set(get_args(CaseCategory)))
            and _enum(payload.get("category"), set(get_args(CaseCategory)))
            and payload.get("category") == category
        ):
            return []
        resources = {"case_id": payload["id"]}
        selector = {
            "open_card_loss_case": "card_id",
            "request_credit_holidays": "loan_id",
        }.get(name, "customer_id")
        if not _id(args.get(selector)):
            return []
        if selector != "loan_id" and payload.get(selector) != args[selector]:
            return []
        resources[selector] = args[selector]
        amount = None
        if name == "create_case":
            if args.get("transaction_id") is not None:
                if (
                    not _id(args["transaction_id"])
                    or payload.get("transaction_id") != args["transaction_id"]
                ):
                    return []
                resources["transaction_id"] = args["transaction_id"]
            if payload.get("amount") is not None:
                amount = _money(payload["amount"])
                if amount is None:
                    return []
            if args.get("amount") is not None and (
                amount is None or _money(args["amount"]) != amount
            ):
                return []
        family = "credit_holidays" if name == "request_credit_holidays" else "case"
        return [
            _fact(
                observed,
                family,
                "accepted_request",
                resources,
                {"status": "open", "category": category},
                amount,
            )
        ] + _snapshot(observed, "case", {"case_id": payload["id"]}, payload)
    if name == "open_account":
        if not (
            _id(payload.get("id"))
            and _id(args.get("customer_id"))
            and payload.get("customer_id") == args["customer_id"]
            and isinstance(args.get("currency"), str)
            and payload.get("currency") == args["currency"].upper()
            and payload.get("status") == "active"
            and payload.get("account_type") == args.get("account_type", "current")
            and _money(payload.get("balance")) == 0
        ):
            return []
        resources = {"account_id": payload["id"]}
        return [
            _fact(
                observed,
                "account",
                "completed_action",
                resources,
                {"status": "active", "currency": payload["currency"]},
            )
        ] + _snapshot(observed, "account", resources, payload)
    if name == "open_deposit":
        deposit = payload.get("deposit")
        amount = _money(args.get("amount"))
        if not (
            isinstance(deposit, dict)
            and _id(deposit.get("id"))
            and deposit.get("status") == "active"
            and _id(args.get("customer_id"))
            and deposit.get("customer_id") == args["customer_id"]
            and _id(args.get("source_account_id"))
            and deposit.get("payout_account_id") == args["source_account_id"]
            and deposit.get("name") == args.get("name")
            and amount is not None
            and amount > 0
            and _money(deposit.get("amount")) == amount
            and _id(payload.get("funding_transaction_id"))
            and _money(payload.get("account_balance")) is not None
        ):
            return []
        resources = {
            "deposit_id": deposit["id"],
            "source_account_id": args["source_account_id"],
        }
        return (
            [
                _fact(
                    observed,
                    "deposit",
                    "completed_action",
                    resources,
                    {
                        "status": "active",
                        "funding_transaction_id": payload["funding_transaction_id"],
                    },
                    amount,
                )
            ]
            + _snapshot(observed, "deposit", {"deposit_id": deposit["id"]}, deposit)
            + _snapshot(
                observed,
                "account",
                {"account_id": args["source_account_id"]},
                {"balance": payload["account_balance"]},
            )
        )
    if name == "early_repayment":
        loan = payload.get("loan")
        charged, requested = _money(payload.get("charged")), _money(args.get("amount"))
        if not (
            isinstance(loan, dict)
            and _id(args.get("loan_id"))
            and loan.get("id") == args["loan_id"]
            and _enum(loan.get("status"), {"active", "closed"})
            and args.get("mode") in {"reduce_payment", "reduce_term"}
            and payload.get("mode") == args["mode"]
            and charged is not None
            and requested is not None
            and 0 < charged <= requested
            and _money(payload.get("account_balance")) is not None
            and all(
                _money(loan.get(field)) is not None
                for field in ("principal", "accrued_interest", "monthly_payment")
            )
        ):
            return []
        resources = {"loan_id": args["loan_id"]}
        return (
            [
                _fact(
                    observed,
                    "loan_repayment",
                    "completed_action",
                    resources,
                    {"mode": args["mode"], "status": loan["status"]},
                    charged,
                )
            ]
            + _snapshot(observed, "loan", resources, loan)
            + _penalties(observed, args["loan_id"], loan)
        )
    if name == "waive_penalty":
        loan = payload.get("loan")
        if not (
            isinstance(loan, dict)
            and _id(args.get("loan_id"))
            and loan.get("id") == args["loan_id"]
            and _id(args.get("penalty_id"))
            and payload.get("waived_penalty_id") == args["penalty_id"]
            and isinstance(loan.get("penalties"), list)
        ):
            return []
        penalty = next(
            (
                row
                for row in loan["penalties"]
                if isinstance(row, dict) and row.get("id") == args["penalty_id"]
            ),
            None,
        )
        if (
            penalty is None
            or _boolean(penalty.get("waived")) is not True
            or _money(penalty.get("amount")) is None
        ):
            return []
        resources = {"loan_id": args["loan_id"], "penalty_id": args["penalty_id"]}
        return (
            [
                _fact(
                    observed,
                    "penalty",
                    "completed_action",
                    resources,
                    {"waived": "True"},
                    _money(penalty["amount"]),
                )
            ]
            + _snapshot(observed, "penalty", resources, penalty)
            + _snapshot(observed, "loan", {"loan_id": args["loan_id"]}, loan)
        )
    if name in {"refund_fee", "refund_transaction", "grant_cashback"}:
        field = "granted" if name == "grant_cashback" else "refunded"
        amount = _money(payload.get(field))
        if not (
            _id(args.get("transaction_id"))
            and amount is not None
            and amount > 0
            and _money(payload.get("account_balance")) is not None
        ):
            return []
        if name != "grant_cashback" and payload.get("reason") != args.get("reason"):
            return []
        if args.get("amount") is not None and _money(args["amount"]) != amount:
            return []
        actions = [
            _fact(
                observed,
                {
                    "refund_fee": "fee_refund",
                    "refund_transaction": "transaction_refund",
                    "grant_cashback": "cashback",
                }[name],
                "completed_action",
                {"transaction_id": args["transaction_id"]},
                {"account_balance": str(_money(payload["account_balance"]))},
                amount,
            )
        ]
        flag = {"refund_fee": "fee_refunded", "grant_cashback": "cashback_granted"}.get(
            name
        )
        if flag:
            actions.extend(
                _snapshot(
                    observed,
                    "transaction",
                    {"transaction_id": args["transaction_id"]},
                    {flag: True},
                )
            )
        return actions

    if name == "order_statement":
        if not (
            _id(payload.get("id"))
            and _id(payload.get("customer_id"))
            and _id(args.get("account_id"))
            and payload.get("account_id") == args["account_id"]
            and all(
                isinstance(args.get(field), str) and payload.get(field) == args[field]
                for field in ("date_from", "date_to")
            )
            and isinstance(args.get("email"), str)
            and isinstance(payload.get("email"), str)
            and payload["email"].lower() == args["email"].strip().lower()
            and _id(payload.get("created_at"))
        ):
            return []
        return [
            _fact(
                observed,
                "statement",
                "accepted_request",
                {"statement_id": payload["id"], "account_id": args["account_id"]},
                {
                    "date_from": payload["date_from"],
                    "date_to": payload["date_to"],
                    "created_at": payload["created_at"],
                },
            )
        ]
    if name == "change_tariff":
        if not (
            _id(args.get("customer_id"))
            and payload.get("id") == args["customer_id"]
            and _id(args.get("new_tariff_id"))
            and payload.get("pending_tariff_id") == args["new_tariff_id"]
            and _id(payload.get("pending_tariff_from"))
            and _id(payload.get("tariff_id"))
        ):
            return []
        resources = {"customer_id": args["customer_id"]}
        return [
            _fact(
                observed,
                "tariff_change",
                "accepted_request",
                resources,
                {
                    "pending_tariff_id": payload["pending_tariff_id"],
                    "pending_tariff_from": payload["pending_tariff_from"],
                },
            )
        ] + _snapshot(observed, "customer", resources, payload)
    return []


READ_ROWS = {
    "get_accounts": ("account", "account_id"),
    "get_cards": ("card", "card_id"),
    "get_disputes": ("dispute", "dispute_id"),
    "get_devices": ("device", "device_id"),
    "get_promotions": ("promotion", "promotion_id"),
    "get_limit_request": ("credit_limit_request", "request_id"),
    "get_documents": ("document", "document_id"),
    "get_deposit": ("deposit", "deposit_id"),
    "get_loans": ("loan", "loan_id"),
    "get_transactions": ("transaction", "transaction_id"),
    "get_subscriptions": ("subscription", "subscription_id"),
    "get_transaction_details": ("transaction", "transaction_id"),
    "get_customer_profile": ("customer", "customer_id"),
    "list_tariffs": ("tariff", "tariff_id"),
    "get_tariff": ("tariff", "tariff_id"),
    "get_cashback_rules": ("cashback_rules", "customer_id"),
}


def _rows(observed, family, selector, rows):
    if not isinstance(rows, list):
        return []
    result = []
    args = observed.call.arguments
    for row in rows:
        if not isinstance(row, dict):
            continue
        target = row.get("customer_id") if family == "cashback_rules" else row.get("id")
        if not _id(target):
            continue
        if family != "tariff" and args.get("customer_id") is not None:
            owner = row.get("id") if family == "customer" else row.get("customer_id")
            if not _id(args["customer_id"]) or owner != args["customer_id"]:
                continue
        if (
            family in {"transaction", "deposit"}
            and args.get(selector) is not None
            and target != args[selector]
        ):
            continue
        if family == "transaction" and any(
            args.get(field) is not None and row.get(field) != args[field]
            for field in ("card_id", "account_id")
        ):
            continue
        if (
            family == "promotion"
            and args.get("promo_code") is not None
            and (
                not isinstance(row.get("code"), str)
                or row["code"].upper() != str(args["promo_code"]).upper()
            )
        ):
            continue
        resources = {selector: target}
        result.extend(_snapshot(observed, family, resources, row))
        if family == "card":
            result.extend(_snapshot(observed, "card_reissue", resources, row))
        if family == "transaction":
            amount = _money(row.get("amount"))
            if amount is not None and _id(row.get("date")):
                values = {"date": row["date"]}
                if _id(row.get("kind")):
                    values["kind"] = row["kind"]
                result.append(
                    _fact(
                        observed, "transaction", "historical", resources, values, amount
                    )
                )
        if family == "loan" and isinstance(row.get("penalties"), list):
            for penalty in row["penalties"]:
                if isinstance(penalty, dict) and _id(penalty.get("id")):
                    result.extend(
                        _snapshot(
                            observed,
                            "penalty",
                            {"loan_id": target, "penalty_id": penalty["id"]},
                            penalty,
                        )
                    )
    return result


def _read(observed, payload):
    name, args = observed.call.name, observed.call.arguments
    if name == "get_limits":
        if (
            not isinstance(payload, dict)
            or not _id(args.get("card_id"))
            or payload.get("card_id") != args["card_id"]
        ):
            return []
        return _limits(observed, payload)
    if name == "get_operations":
        if not isinstance(payload, list) or not _id(args.get("customer_id")):
            return []
        return [
            _fact(
                observed,
                "transaction",
                "historical",
                {"transaction_id": row["id"]},
                {"date": row["date"]},
                _money(row["amount"]),
            )
            for row in payload
            if isinstance(row, dict)
            and _id(row.get("id"))
            and _id(row.get("date"))
            and _money(row.get("amount")) is not None
        ]
    if name not in READ_ROWS:
        return []
    family, selector = READ_ROWS[name]
    if name == "get_transactions":
        if not isinstance(payload, dict):
            return []
        rows = payload.get("items")
    elif name == "get_subscriptions":
        if not isinstance(payload, dict):
            return []
        return _rows(
            observed, "subscription", "subscription_id", payload.get("subscriptions")
        ) + _rows(
            observed, "autopayment", "autopayment_id", payload.get("autopayments")
        )
    elif name in {
        "get_transaction_details",
        "get_customer_profile",
        "get_tariff",
        "get_cashback_rules",
    }:
        rows = [payload]
        if name == "get_tariff" and not _id(args.get("customer_id")):
            return []
    else:
        rows = payload
    return _rows(observed, family, selector, rows)


def _penalties(observed, loan_id, loan):
    if not isinstance(loan.get("penalties"), list):
        return []
    result = []
    for penalty in loan["penalties"]:
        if isinstance(penalty, dict) and _id(penalty.get("id")):
            result.extend(
                _snapshot(
                    observed,
                    "penalty",
                    {"loan_id": loan_id, "penalty_id": penalty["id"]},
                    penalty,
                )
            )
    return result


def _invalidation(observed, family, field, owner=None):
    return _fact(
        observed,
        family,
        "current_state",
        {},
        {},
        invalidated_fields=[field],
        invalidation_scope={"customer_id": owner} if owner else {},
    )


def _credit_balance_effects(observed, payload, history):
    name, args = observed.call.name, observed.call.arguments
    if name not in {
        "refund_fee",
        "refund_transaction",
        "grant_cashback",
        "early_repayment",
    }:
        return []
    # A source must already be received when the dependent request is formed.
    available = current_facts_at(history, observed.call_position - 1)
    if name == "early_repayment":
        loan = payload["loan"]
        owner = loan.get("customer_id")
        if not _id(owner):
            owner = next(
                (
                    fact.values["customer_id"]
                    for fact in available
                    if fact.family == "loan"
                    and fact.resources == {"loan_id": args["loan_id"]}
                    and "customer_id" in fact.values
                ),
                None,
            )
        # The returned owner can scope a negative freshness fence; it does not
        # retroactively witness knowledge at formation or identify the account.
        return [_invalidation(observed, "account", "balance", owner)]
    transaction = args["transaction_id"]
    transaction_fields = {
        field: value
        for fact in available
        if fact.family == "transaction"
        and fact.resources == {"transaction_id": transaction}
        for field, value in fact.values.items()
    }
    binding = transaction_fields.get("account_id")
    owner = transaction_fields.get("customer_id")
    if binding:
        result = _snapshot(
            observed,
            "account",
            {"account_id": binding},
            {"balance": payload["account_balance"]},
        )
    else:
        # Preserve a wildcard fence even before any affected record arrives.
        result = [_invalidation(observed, "account", "balance", owner)]
    if name == "grant_cashback":
        # The result omits updated accrual: never reconstruct it by arithmetic.
        result.append(
            _invalidation(observed, "cashback_rules", "accrued_current_period", owner)
        )
    return result


def banking_facts(trace: TraceEvidence) -> list[OperationFact]:
    """Return observation history in receipt order; errors/user calls yield none.

    This is a result-backed knowledge adapter, not an access/policy/replay grader.
    Response-absent selectors bind only to the originating successful request.
    Initial database records never enter this function.
    """
    result = []
    for observed in sorted(
        trace.observed,
        key=lambda item: item.result_position
        if item.result_position is not None
        else float("inf"),
    ):
        if (
            observed.call.requestor != "assistant"
            or observed.result is None
            or observed.result.error
            or observed.result_position is None
        ):
            continue
        try:
            payload = json.loads(observed.result.content or "null")
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            written = _write(observed, payload)
            if written:
                written.extend(_credit_balance_effects(observed, payload, result))
            result.extend(written)
        result.extend(_read(observed, payload))
    return result


def current_facts_at(facts: list[OperationFact], position: int) -> list[OperationFact]:
    """Project received scalar state with the shared domain-neutral freshness rule."""
    return current_state_facts_at(facts, position)


# Explicit inventory of all tools. Exclusion means a different contract owns
# the event, never that the call is safe or carries no risk. This catalog describes
# only declared scalar outcomes; policy prose and arbitrary document bodies need
# their own source interpretation. It is intentionally not auto-generated from
# decorated methods, so additions to the domain require a classification review.
WRITE_EFFECTS = {
    "block_card": "completed blocking; current card status/reason",
    "unblock_card": "completed unblock; current active status and cleared reason",
    "freeze_card": "completed temporary block; current card status/reason",
    "reissue_card": "accepted card order; current reissue stage; no delivery",
    "set_limit": "completed numeric channel or internet flag; separate current channels",
    "open_dispute": "accepted dispute under_review; current dispute stage/amount; no refund",
    "cancel_dispute": "completed withdrawal; current dispute stage; no money reversal",
    "close_dispute": "completed decision; current approved/rejected; no refund",
    "release_hold": "completed release; current declined operation/cleared hold; no refund",
    "create_case": "accepted case; current open stage; no resolution or payout",
    "open_card_loss_case": "accepted card-loss case; current open stage",
    "notify_card_loss_case": "completed notification; current case notified stage",
    "request_restructuring_documents": "completed document request; current case stage; no approval",
    "cancel_subscription": "completed cancellation; current subscription state/paid period",
    "cancel_autopayment": "completed cancellation; independent current autopayment state",
    "open_deposit": "completed funding/opening; current deposit and source balance",
    "close_deposit": "completed closure/payout; current deposit and requested payout balance",
    "early_repayment": "completed actual charged amount; current debt/payment/penalty flags; returned-owner balance fence or global fallback; no inferred term/account",
    "waive_penalty": "completed target waiver; current penalty waived flag",
    "refund_fee": "completed returned fee amount; transaction-bound flag; earlier received account binding or owner/global balance fence",
    "refund_transaction": "completed returned transaction amount; earlier received account binding or owner/global balance fence",
    "grant_cashback": "completed manual credit amount and flag; earlier received account binding or owner/global balance fence; owner/global accrued-period fence",
    "open_account": "completed creation; current account status/currency/balance",
    "close_account": "completed closure; current closed/zero balance/debt",
    "transfer_between_own_accounts": "completed amount; request-bound accounts and current balances",
    "order_statement": "accepted statement order/period; no email delivery evidence",
    "change_tariff": "accepted future tariff; current old/pending tariffs; no activation",
    "unblock_device": "completed device unblock; current device flag; no card action",
    "unblock_operation": "completed security unblock; current posted operation",
    "request_credit_holidays": "accepted application with request-bound loan; no holidays approval",
    "share_document": "completed sharing; current shared flag; no copied document content",
    "escalate_to_human": "accepted escalation case; no specialist resolution",
}
READ_EFFECTS = {
    "get_customer_profile": "current tariff/pending tariff/credit limit/balance metrics; secrets excluded",
    "get_accounts": "current per-account balance/debt/status/currency",
    "get_cards": "current per-card status/reason/reissue stage; PAN/CVV/expiry excluded",
    "get_transactions": "historical row amount/date/kind; current mutable operation status/flags; page only",
    "get_operations": "historical amount/date of at most ten rows; no payload status/ownership/completeness",
    "get_transaction_details": "historical amount/date/kind; current operation status/flags",
    "get_disputes": "current stage/amount/filed date/SLA; no agent action or refund",
    "get_subscriptions": "independent current subscriptions/autopayments status/amount/paid period",
    "get_limits": "current per-card cash/SBP amounts and internet flag; no execution claim",
    "get_tariff": "current tariff financial terms; customer association only from request",
    "list_tariffs": "available tariff financial terms; no customer association",
    "get_cashback_rules": "current accrued period and payout day; no executed payout; categories prose excluded",
    "get_devices": "current per-device blocked flag; no agent unblock",
    "get_promotions": "conditional reward/threshold/count/expiry terms; no progress or payout",
    "get_limit_request": "current request stage/amount/decision date; no current card/credit limit change",
    "get_documents": "current shared flag; bodies/access markings require source contract",
    "get_deposit": "current deposit principal/rates/dates/status/contract payout account; no computed payout",
    "get_loans": "current principal/interest/payment/status/overdue/waivers and individual penalty flags",
}
EXCLUSIONS = {
    "verify_identity": (
        "write",
        "Authentication event, not financial execution; credential stays out of facts",
    ),
    "send_otp": (
        "write",
        "OTP delivery authorization, not operation completion; auth contract owns it",
    ),
    "check_otp": (
        "write",
        "OTP verification, not financial execution; internal secret stays out of facts",
    ),
    "reveal_card_details": (
        "write",
        "Secret-access event, not financial outcome; access/confidentiality contracts retain it",
    ),
    "reply_to_ticket": (
        "write",
        "Saved outgoing answer, not evidence its financial claims are true",
    ),
    "find_customer": (
        "read",
        "Minimal discovery candidates, not product knowledge or access authority",
    ),
    "ask_client": (
        "read",
        "Client input/consent/credentials, not bank operation completion or current system state",
    ),
    "search_knowledge": (
        "read",
        "Source candidates/snippets need policy interpretation and validity dates",
    ),
    "get_article": (
        "read",
        "Policy source, not customer operation/state; direct reads remain allowed",
    ),
    "calculate": (
        "generic",
        "Computed expression, not a received bank state or execution result",
    ),
}
TOOL_FACT_CATALOG = {
    **{
        name: {"tool_type": "write", "effects": [effect]}
        for name, effect in WRITE_EFFECTS.items()
    },
    **{
        name: {"tool_type": "read", "effects": [effect]}
        for name, effect in READ_EFFECTS.items()
    },
    **{
        name: {"tool_type": tool_type, "excluded_reason": reason}
        for name, (tool_type, reason) in EXCLUSIONS.items()
    },
}
