"""Conditional settlement authority; no optimization or scenario-specific branches."""

from datetime import datetime
from zoneinfo import ZoneInfo

# Shared private reads are declared once, rather than copied into every task.
COMMON_TOOL_ACCESS = {
    "get_service_incidents": {"customer_id": "customers"},
    "get_partner_confirmations": {"customer_id": "customers"},
    "get_reward_resolution_status": {
        "resolution_operation_id": "reward_resolution_executions"
    },
    "execute_reward_resolution": {
        "fallback_operation_id": "reward_resolution_executions"
    },
    "ask_client": {"request.fallback_operation_id": "reward_resolution_executions"},
}


COMMON_RESULT_COLLECTIONS = {
    "get_reward_resolution_offers": ["reward_resolution_executions"],
    "execute_reward_resolution": ["reward_resolution_executions"],
    "get_reward_resolution_status": ["reward_resolution_executions"],
}


def row(value):
    return (
        value.model_dump()
        if hasattr(value, "model_dump")
        else vars(value)
        if hasattr(value, "__dict__")
        else value
    )


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("A timezone is required")
    return parsed


def validate_eligibility(
    offer, component_ids, evidence, *, transactions, contracts, incidents, confirmations
):
    """Check exact presented grounds for selected conditional components only."""
    conditions = {
        p.id: row(p.eligibility)
        for p in offer.components
        if getattr(p, "eligibility", None) is not None and p.id in component_ids
    }
    if set(evidence) != set(conditions):
        raise ValueError(
            "ERR_COMPONENT_NOT_APPLICABLE: Нужны основания выбранных условных компонентов"
        )
    for ident, condition in conditions.items():
        try:
            proof = row(evidence[ident])
            tx = row(transactions[condition["transaction_id"]])
            contract = row(contracts[proof["contract_id"]])
            when = timestamp(tx["purchased_at"])
            day = when.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
            if (
                condition["transaction_id"] not in offer.transaction_ids
                or tx["customer_id"] != offer.customer_id
                or tx["kind"] != "purchase"
                or tx["status"] != "posted"
                or contract["id"] != proof["contract_id"]
                or contract["customer_id"] != offer.customer_id
                or contract["account_id"] != tx["account_id"]
                or tx["card_id"] not in contract["card_ids"]
                or not contract["effective_from"] <= day
                or (
                    contract.get("effective_to") is not None
                    and day > contract["effective_to"]
                )
            ):
                raise ValueError("Договор не относится к покупке")
            collection = (
                incidents if condition["kind"] == "service_incident" else confirmations
            )
            record = row(collection[proof["record_id"]])
            if (
                record["id"] != proof["record_id"]
                or record["customer_id"] != offer.customer_id
                or record["contract_id"] != contract["id"]
            ):
                raise ValueError("Основание другого владельца или договора")
            if condition["kind"] == "service_incident":
                if record["kind"] != condition["incident_kind"] or not timestamp(
                    record["starts_at"]
                ) <= when < timestamp(record["ends_at"]):
                    raise ValueError("Инцидент не охватывает покупку")
            elif condition["kind"] == "partner_confirmation":
                if (
                    record["status"] != "accepted"
                    or not tx.get("receipt_id")
                    or record["receipt_id"] != tx["receipt_id"]
                ):
                    raise ValueError("Нет принятого подтверждения нужного чека")
                enrolled = record["enrollment_card_id"]
                if enrolled != tx["card_id"] and not (
                    enrolled == contract.get("replaced_card_id")
                    and tx["card_id"] == contract.get("replacement_card_id")
                    and contract.get("replacement_effective_from") is not None
                    and contract["replacement_effective_from"] <= day
                ):
                    raise ValueError("Нет преемственности карты участия")
            else:
                raise ValueError("Неизвестное условие применимости")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError("ERR_COMPONENT_NOT_APPLICABLE: " + str(exc)) from exc


def prepare_access_policy(policy):
    """Expand shared banking ownership rules before the generic ACTION evaluator."""
    if policy is None or policy.policy_id != "banking_private_access":
        return policy
    policy = policy.model_copy(deep=True)
    for name, fields in COMMON_TOOL_ACCESS.items():
        policy.tool_arguments[name] = {**policy.tool_arguments.get(name, {}), **fields}
        for collection in fields.values():
            policy.resources_by_customer.setdefault(collection, {})
    for name, collections in COMMON_RESULT_COLLECTIONS.items():
        policy.result_resource_collections[name] = sorted(
            set(policy.result_resource_collections.get(name, [])) | set(collections)
        )
    return type(policy).model_validate(policy.model_dump())
