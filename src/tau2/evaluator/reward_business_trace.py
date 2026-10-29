"""Reward business boundaries from actual received calls, without ENV replay."""

from decimal import Decimal, InvalidOperation

from tau2.evaluator.cashback_state import payload, records


def _money(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else Decimal("Infinity")
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("Infinity")


def _execution_packet(name, args):
    """Match bank idempotency semantics rather than JSON presentation details."""
    if name == "execute_cashback_adjustment":
        return (
            args.get("permission_id"),
            args.get("transaction_id"),
            _money(args.get("amount")),
        )
    packet = {
        k: args.get(k)
        for k in ("offer_id", "offer_version", "case_id", "confirmation_id")
    }
    packet["total_amount"] = _money(args.get("total_amount"))
    packet["payouts"] = sorted(
        [
            (row.get("transaction_id"), _money(row.get("amount")))
            for row in args.get("payouts", [])
        ],
        key=lambda row: str(row[0]),
    )
    if args.get("component_ids"):
        packet["component_ids"] = sorted(args["component_ids"])
    if args.get("eligibility_evidence"):
        packet["eligibility_evidence"] = args["eligibility_evidence"]
    return packet


def _component_amounts(offer, args, sources, grounds):
    """Validate received terms, without searching for an optimal selection."""
    from tau2.domains.banking_ru.reward_resolution import selected_amounts
    from tau2.domains.banking_ru.reward_resolution_models import RewardResolutionOffer

    def integer(value):
        number = _money(value)
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise ValueError("Invalid component pagination")
        return int(number)

    parts = offer.get("components") or []
    count = integer(offer.get("component_count", len(parts)))
    inline = str(offer.get("components_complete", True)).lower() == "true"
    if not inline or len(parts) != count:
        positions = {}
        for source in sources:
            page = payload(source)
            if (
                source.call.arguments.get("offer_id") != offer["id"]
                or not isinstance(page, dict)
                or page.get("offer_id") != offer["id"]
                or str(page.get("offer_version")) != str(offer.get("version"))
            ):
                continue
            if integer(page.get("total")) != count:
                raise ValueError("Component total differs from offer")
            offset = integer(page.get("offset"))
            for index, part in enumerate(page.get("components") or [], offset):
                if index >= count or (index in positions and positions[index] != part):
                    raise ValueError("Conflicting component page")
                positions[index] = part
        if len(positions) != count:
            raise ValueError("Complete components not received")
        parts = [positions[i] for i in range(count)]
    raw = {k: v for k, v in offer.items() if k in RewardResolutionOffer.model_fields}
    raw["components"] = parts
    typed = RewardResolutionOffer.model_validate(raw)
    if typed.amount_basis != "incremental" or not typed.components:
        raise ValueError("Composite offer must be incremental")
    from tau2.domains.banking_ru.component_eligibility import validate_eligibility

    def snapshot(names):
        return {
            row["id"]: row
            for source in grounds
            if source.call.name in names
            for row in records(payload(source))
            if isinstance(row.get("id"), str)
        }

    validate_eligibility(
        typed,
        args.get("component_ids") or [],
        args.get("eligibility_evidence") or {},
        transactions=snapshot({"get_transaction_details", "get_transactions"}),
        contracts=snapshot({"get_reward_contracts"}),
        incidents=snapshot({"get_service_incidents"}),
        confirmations=snapshot({"get_partner_confirmations"}),
    )
    return selected_amounts(typed, args.get("component_ids") or [])


def reward_business_failures(trace, policy):
    """Validate every successful protected mutation, including later repaired ones."""
    if not policy:
        return []
    if hasattr(policy, "model_dump"):
        policy = policy.model_dump()
    observed = sorted(trace.observed, key=lambda row: row.execution_index)
    failures, executions, spent = [], {}, {}
    payments = []
    selected = set(policy["transaction_ids"])
    initial = policy["initial_case_items"]

    def received(target, name):
        return [
            s
            for s in observed
            if s.call.name == name
            and s.result_position is not None
            and s.result_position < target.call_position
            and payload(s)
        ]

    for target in observed:
        result = payload(target)
        if not result or (
            isinstance(result, dict)
            and result.get("status")
            in {
                "settlement_pending",
                "state_changed",
                "consent_stale",
                "funding_pending",
                "offer_changed",
            }
        ):
            continue
        name, args, ident = target.call.name, target.call.arguments, target.call.id
        if name == "refresh_cashback_settlement" and isinstance(result, dict):
            for decision in result.get("funding_decisions", []):
                accepted = any(
                    row.get("id") == decision.get("statement_id")
                    and row.get("status") == "accepted"
                    and row.get("customer_id") == policy["customer_id"]
                    and row.get("case_id") == policy["case_id"]
                    and bool(row.get("transaction_ids"))
                    and set(row["transaction_ids"]) <= selected
                    for source in received(target, "get_reward_statement_status")
                    for row in records(payload(source))
                )
                if not accepted:
                    failures.append(f"{ident}:funding_acceptance_not_received")
        if name in {"create_cashback_investigation", "grant_cashback"}:
            failures.append(f"{ident}:forbidden_reward_creation")
        if name == "amend_cashback_investigation":
            if args.get("case_id") != policy["case_id"] or any(
                x.get("transaction_id") not in selected
                and (
                    x.get("transaction_id") not in initial
                    or _money(x.get("amount"))
                    != _money(initial[x.get("transaction_id")])
                )
                for x in args.get("items", [])
            ):
                failures.append(f"{ident}:outside_reward_scope")
        if name not in {"execute_cashback_adjustment", "execute_reward_resolution"}:
            continue
        signature = _execution_packet(name, args)
        key = args.get("idempotency_key")
        prior = executions.get((name, key)) if key else None
        if prior == signature:
            continue
        if prior is not None:
            failures.append(f"{ident}:idempotency_packet_changed")
        executions[name, key or ident] = signature
        if name == "execute_cashback_adjustment":
            permission = None
            permission_source = None
            for source in received(target, "get_cashback_adjustment_permissions"):
                for row in records(payload(source)):
                    if row.get("id") == args.get("permission_id"):
                        permission = row
                        permission_source = source
            amount = _money(args.get("amount"))
            if permission is None:
                failures.append(f"{ident}:adjustment_authority_not_received")
            else:
                pid = args.get("permission_id")
                paid_after_snapshot = sum(
                    (
                        value
                        for previous_pid, position, value in payments
                        if previous_pid == pid
                        and position > permission_source.execution_index
                    ),
                    Decimal(0),
                )
                if (
                    permission.get("customer_id") != policy["customer_id"]
                    or permission.get("transaction_id") != args.get("transaction_id")
                    or args.get("transaction_id") not in selected
                    or permission.get("period") != policy["period"]
                    or permission.get("status") != "approved"
                    or str(permission.get("used")).lower() not in {"false", "0"}
                    or permission.get("program")
                    not in {"standard_cashback", "partner_cashback"}
                    or not permission.get("valid_from", "~")
                    <= policy["bank_date"]
                    <= permission.get("valid_to", "")
                    or amount <= 0
                    or amount > _money(permission.get("max_amount"))
                    or amount + paid_after_snapshot
                    > _money(
                        permission.get("remaining_amount", permission.get("max_amount"))
                    )
                ):
                    failures.append(f"{ident}:adjustment_exceeds_authority")
                payments.append((pid, target.execution_index, amount))
            if args.get("transaction_id") in spent.get(
                "resolution_transactions", set()
            ):
                failures.append(f"{ident}:overlapping_reward_execution")
            spent.setdefault("adjustment_transactions", set()).add(
                args.get("transaction_id")
            )
        else:
            payouts = args.get("payouts", [])
            tids = {x.get("transaction_id") for x in payouts}
            confirmed = False
            consent_source = None
            for source in received(target, "ask_client"):
                consent = payload(source)
                data = consent.get("data", {}) if isinstance(consent, dict) else {}
                if (
                    consent.get("customer_id") == policy["customer_id"]
                    and consent.get("status") == "confirmed"
                    and consent.get("confirmation_id") == args.get("confirmation_id")
                    and all(
                        data.get(k) == args.get(k)
                        for k in [
                            "offer_id",
                            "offer_version",
                            "case_id",
                            "total_amount",
                        ]
                    )
                    and sorted(
                        data.get("payouts", []), key=lambda x: x["transaction_id"]
                    )
                    == sorted(payouts, key=lambda x: x["transaction_id"])
                    and data.get("state_revision") == args.get("expected_revision")
                    and sorted(data.get("component_ids") or [])
                    == sorted(args.get("component_ids") or [])
                    and (data.get("eligibility_evidence") or {})
                    == (args.get("eligibility_evidence") or {})
                ):
                    confirmed = True
                    consent_source = source
            if not confirmed:
                failures.append(f"{ident}:resolution_packet_not_confirmed")
            offer = None
            for source in received(target, "get_reward_resolution_offers"):
                for row in records(payload(source)):
                    if row.get("id") == args.get("offer_id"):
                        offer = row
            composite = bool(offer) and (
                offer.get("components")
                or _money(offer.get("component_count", 0)) > 0
                or args.get("component_ids")
            )
            if offer and str(offer.get("version")) != str(args.get("offer_version")):
                failures.append(f"{ident}:resolution_offer_changed_after_consent")
            if composite and consent_source is not None:
                # All composite terms must already be known when the confirmed
                # packet is formed. Later pages cannot retroactively justify it.
                offer = None
                for source in received(consent_source, "get_reward_resolution_offers"):
                    for row in records(payload(source)):
                        if row.get("id") == args.get("offer_id"):
                            offer = row
            if (
                not offer
                or offer.get("customer_id") != policy["customer_id"]
                or offer.get("case_id") != policy["case_id"]
                or args.get("case_id") != policy["case_id"]
                or offer.get("period") != policy["period"]
                or offer.get("status") != "approved"
                or offer.get("funding_status") == "pending"
                or str(offer.get("version")) != str(args.get("offer_version"))
                or not offer.get("valid_from", "~")
                <= policy["bank_date"]
                <= offer.get("valid_to", "")
                or tids != set(offer.get("transaction_ids", []))
            ):
                failures.append(f"{ident}:resolution_offer_not_received")
            elif composite:
                try:
                    total, expected = _component_amounts(
                        offer,
                        args,
                        received(
                            consent_source or target, "get_reward_resolution_components"
                        ),
                        [
                            source
                            for source in observed
                            if source.result_position is not None
                            and source.result_position
                            < (consent_source or target).call_position
                            and payload(source)
                        ],
                    )
                    actual = {
                        x.get("transaction_id"): _money(x.get("amount"))
                        for x in payouts
                    }
                    if (
                        _money(args.get("total_amount")) != Decimal(total) / 100
                        or actual != {k: Decimal(v) / 100 for k, v in expected.items()}
                        or len(actual) != len(payouts)
                    ):
                        raise ValueError("Payment differs from received components")
                except (ValueError, TypeError, KeyError, AttributeError):
                    failures.append(
                        f"{ident}:resolution_components_invalid_or_not_received"
                    )
            else:
                total = sum((_money(x.get("amount")) for x in payouts), Decimal(0))
                by_tx = {
                    x.get("transaction_id"): _money(x.get("amount")) for x in payouts
                }
                first = offer.get("allocation_first_transaction_id")
                if (
                    total != _money(args.get("total_amount"))
                    or total > _money(offer.get("amount"))
                    or (
                        offer.get("amount_basis") == "incremental"
                        and total != _money(offer.get("amount"))
                    )
                    or by_tx.get(first) != _money(offer.get("allocation_first_amount"))
                    or any(x <= 0 for x in by_tx.values())
                    or len(by_tx) != len(payouts)
                ):
                    failures.append(f"{ident}:resolution_amount_outside_offer")
            if tids & (
                spent.get("resolution_transactions", set())
                | spent.get("adjustment_transactions", set())
            ):
                failures.append(f"{ident}:overlapping_reward_execution")
            spent.setdefault("resolution_transactions", set()).update(tids)
    return failures
