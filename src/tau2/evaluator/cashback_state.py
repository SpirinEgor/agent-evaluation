"""Received banking facts and unknown-execution boundaries shared by HALF/FULL."""

import json
from decimal import Decimal
from types import SimpleNamespace


def records(value):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from records(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from records(nested)


def payload(call):
    if (
        not call.result
        or call.result.error
        or call.call.requestor != "assistant"
        or call.result.requestor != "assistant"
        or call.result.id != call.call.id
    ):
        return {}
    try:
        return json.loads(call.result.content or "{}")
    except (ValueError, TypeError):
        return {}


def signature(args):
    if "offer_id" in args:
        return tuple(
            json.dumps(args.get(k), sort_keys=True)
            for k in (
                "offer_id",
                "offer_version",
                "case_id",
                "payouts",
                "total_amount",
                "confirmation_id",
            )
        )
    return (
        args.get("permission_id"),
        args.get("transaction_id"),
        Decimal(str(args.get("amount", 0))),
    )


def cashback_state_failures(observed, policy):
    if not policy:
        return []
    customer, period = policy["customer_id"], policy["period"]
    failures = []

    def received(source, target):
        return (
            source.result_position is not None
            and source.result_position < target.call_position
            and bool(payload(source))
        )

    def version_available(rev, target):
        for source in observed:
            if not received(source, target):
                continue
            for row in records(payload(source)):
                if rev is None or str(
                    row.get("state_revision", row.get("current_revision"))
                ) != str(rev):
                    continue
                owner = row.get("customer_id", source.call.arguments.get("customer_id"))
                source_period = row.get(
                    "period",
                    row.get("reward_period", source.call.arguments.get("period")),
                )
                # A returned case/operation carries its own customer and period.
                if owner == customer and source_period == period:
                    return True
        return False

    unknowns = [
        c
        for c in observed
        if isinstance(payload(c), dict)
        and payload(c).get("status") == "execution_unknown"
        and c.call.name == "execute_cashback_adjustment"
    ]

    def resolved(prior, target):
        operation = payload(prior).get("operation_id")
        args = prior.call.arguments
        for source in observed:
            if (
                not received(source, target)
                or source.call_position <= prior.call_position
            ):
                continue
            for row in records(payload(source)):
                if (
                    row.get("operation_id") == operation
                    and row.get("status") == "succeeded"
                    and row.get("transaction_id") == args.get("transaction_id")
                    and row.get("permission_id") == args.get("permission_id")
                    and Decimal(str(row.get("granted", 0)))
                    == Decimal(str(args.get("amount", 0)))
                ):
                    return True
                if (
                    source.call.name in ["get_transactions", "get_transaction_details"]
                    and row.get("kind") == "cashback"
                    and row.get("status") == "posted"
                    and row.get("customer_id") == customer
                    and row.get("original_transaction_id") == args.get("transaction_id")
                    and str(row.get("id", "")).startswith(
                        "cashback-adjustment:" + args.get("permission_id", "")
                    )
                    and Decimal(str(row.get("amount", 0)))
                    == Decimal(str(args.get("amount", 0)))
                ):
                    return True
        return False

    for target in observed:
        name, args = target.call.name, target.call.arguments
        result = payload(target)
        if (
            name == "execute_reward_resolution"
            and isinstance(result, dict)
            and result.get("status") in {"funding_pending", "offer_changed"}
        ):
            # A refused preliminary/stale offer made no payment. Its guessed
            # revision cannot invalidate a later lawful, fully received retry.
            continue
        if name in [
            "execute_cashback_adjustment",
            "execute_reward_resolution",
            "amend_cashback_investigation",
        ]:
            retry = (
                name in ["execute_cashback_adjustment", "execute_reward_resolution"]
                and args.get("idempotency_key") is not None
                and any(
                    received(p, target)
                    and p.call.name == name
                    and p.call.arguments.get("idempotency_key")
                    == args.get("idempotency_key")
                    and signature(p.call.arguments) == signature(args)
                    and isinstance(payload(p), dict)
                    and payload(p).get("status") in ["succeeded", "execution_unknown"]
                    for p in observed
                )
            )
            if not retry and not version_available(
                args.get("expected_revision"), target
            ):
                failures.append(f"{target.call.id}:revision_not_received")
            recoverable = isinstance(result, dict) and result.get("status") in [
                "settlement_pending",
                "state_changed",
                "consent_stale",
                "funding_pending",
                "offer_changed",
            ]
            if not recoverable and result:
                settled = any(
                    received(s, target)
                    and s.call.name == "refresh_cashback_settlement"
                    and isinstance(payload(s), dict)
                    and payload(s).get("status") == "settled"
                    and s.call.arguments == {"customer_id": customer, "period": period}
                    for s in observed
                )
                no_pending = any(
                    received(s, target)
                    and s.call.name == "get_cashback_rules"
                    and any(
                        r.get("period") == period
                        and "settlements" in r
                        and all(x.get("status") == "settled" for x in r["settlements"])
                        for r in records(payload(s))
                    )
                    for s in observed
                )
                if not settled and not no_pending:
                    failures.append(f"{target.call.id}:settlement_not_received")
            for prior in unknowns:
                if prior.call_position >= target.call_position or resolved(
                    prior, target
                ):
                    continue
                if (
                    name == "execute_cashback_adjustment"
                    and retry
                    and signature(prior.call.arguments) == signature(args)
                    and prior.call.arguments.get("idempotency_key")
                    == args.get("idempotency_key")
                ):
                    continue
                if name == "amend_cashback_investigation" and (
                    not any(
                        x.get("transaction_id")
                        == prior.call.arguments.get("transaction_id")
                        for x in args.get("items", [])
                    )
                    or args.get("reserved_adjustments")
                ):
                    continue
                failures.append(f"{target.call.id}:unknown_execution_not_resolved")
        if name == "done":
            for prior in unknowns:
                if not resolved(prior, target):
                    failures.append(
                        f"{target.call.id}:unresolved_at_done:{payload(prior).get('operation_id')}"
                    )
            for prior in observed:
                if (
                    prior.call.name
                    in [
                        "execute_cashback_adjustment",
                        "execute_reward_resolution",
                        "amend_cashback_investigation",
                        "refresh_cashback_settlement",
                    ]
                    and prior.call_position < target.call_position
                    and payload(prior)
                    and not received(prior, target)
                ):
                    failures.append(
                        f"{target.call.id}:operation_result_not_received:{prior.call.id}"
                    )
    # Platform done is a terminal MCP event, deliberately absent from the bank
    # replay history (trace_call=False). A completed graded trajectory must also
    # discharge unknown outcomes when there is no later dependent bank call.
    terminal = SimpleNamespace(
        call_position=max(
            [
                position
                for call in observed
                for position in [call.call_position, call.result_position]
                if position is not None
            ],
            default=-1,
        )
        + 1
    )
    for prior in unknowns:
        if not resolved(prior, terminal):
            failures.append(
                f"terminal:unresolved_execution:{payload(prior).get('operation_id')}"
            )
    return sorted(set(failures))
