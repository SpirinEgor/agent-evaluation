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
    if args.get("fallback_operation_id"):
        packet["fallback_operation_id"] = args["fallback_operation_id"]
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
    total, expected = selected_amounts(typed, args.get("component_ids") or [])
    chosen = set(args.get("component_ids") or [])
    costs = {
        source: sum(
            (
                _money(getattr(part, source + "_cost"))
                for part in typed.components
                if part.id in chosen
            ),
            Decimal(0),
        )
        for source in ("bank", "partner")
    }
    return total, expected, costs


def _resolution_receipts(observed, target, policy):
    """Received operation transitions with exact scope and immutable terminal state."""
    states = {}
    for source in sorted(
        observed,
        key=lambda row: row.result_position
        if row.result_position is not None
        else float("inf"),
    ):
        if (
            source.result_position is None
            or source.result_position >= target.call_position
        ):
            continue
        if source.call.name not in {
            "execute_reward_resolution",
            "get_reward_resolution_status",
            "retry_reward_resolution",
            "amend_reward_resolution",
        }:
            continue
        result = payload(source)
        if not isinstance(result, dict) or result.get("status") not in {
            "execution_unknown",
            "failed_final",
            "succeeded",
        }:
            continue
        operation = result.get("operation_id", result.get("id"))
        if not operation or any(
            result.get(field) != policy[field]
            for field in ("customer_id", "case_id", "period")
        ):
            continue
        origins = [
            row
            for row in observed
            if row.call.name == "execute_reward_resolution"
            and row.call_position <= source.call_position
            and row.call.arguments.get("offer_id") == result.get("offer_id")
            and row.call.arguments.get("case_id") == policy["case_id"]
            and isinstance(payload(row), dict)
            and payload(row).get("operation_id", payload(row).get("id")) == operation
        ]
        if not origins:
            continue
        origin = min(origins, key=lambda row: row.call_position)
        if source.call.name in {
            "get_reward_resolution_status",
            "retry_reward_resolution",
            "amend_reward_resolution",
        }:
            if source.call.arguments.get("resolution_operation_id") != operation:
                continue
        elif _execution_packet(
            source.call.name, source.call.arguments
        ) != _execution_packet(
            origin.call.name, origin.call.arguments
        ) or source.call.arguments.get("idempotency_key") != origin.call.arguments.get(
            "idempotency_key"
        ):
            continue
        if str(result.get("offer_version")) != str(
            origin.call.arguments.get("offer_version")
        ):
            continue
        previous = states.get(operation)
        if (
            previous
            and result.get("state_revision") is not None
            and previous[0].get("state_revision") is not None
            and _money(result["state_revision"]) < _money(previous[0]["state_revision"])
        ):
            continue
        if previous and previous[0]["status"] in {"failed_final", "succeeded"}:
            continue
        states[operation] = (result, origin, source)
    return states


def _remaining_funding(observed, boundary, offer_source, reserve, policy):
    """Apply received obligation changes to a received reserve snapshot once."""
    remaining = {
        kind: _money(reserve.get("remaining_" + kind)) for kind in ("bank", "partner")
    }
    baseline = {
        row["id"]: {kind: _money(row.get(kind, 0)) for kind in remaining}
        for row in reserve.get("operations", [])
        if isinstance(row, dict) and row.get("id")
    }
    # Legacy snapshots lack operation IDs; receipt time still prevents counting
    # a charge already reflected in a refreshed snapshot twice.
    transitions = _resolution_receipts(observed, boundary, policy)
    for operation, (result, origin, source) in transitions.items():
        if source.result_position <= offer_source.result_position:
            continue
        status = result["status"]
        costs = (
            result.get(
                "funding_hold" if status == "execution_unknown" else "funding_charge"
            )
            or {}
        )
        released = result.get("released_funding") or {}
        if costs.get("reserve_id", released.get("reserve_id")) != reserve["id"]:
            continue
        previous = baseline.get(operation)
        if previous is None:
            previous = {kind: Decimal(0) for kind in remaining}
            # If an unknown receipt preceded the snapshot, its hold was already
            # deducted even in older snapshots that did not enumerate operations.
            for earlier in observed:
                value = payload(earlier)
                if (
                    earlier.result_position is not None
                    and earlier.result_position <= offer_source.result_position
                    and isinstance(value, dict)
                    and value.get("operation_id", value.get("id")) == operation
                    and value.get("status") == "execution_unknown"
                ):
                    previous = {
                        kind: _money((value.get("funding_hold") or {}).get(kind, 0))
                        for kind in remaining
                    }
        for kind in remaining:
            current = (
                Decimal(0) if status == "failed_final" else _money(costs.get(kind, 0))
            )
            remaining[kind] -= current - previous[kind]
    # Backward compatible successful receipts without operation metadata.
    keys = set()
    for row in observed:
        value = payload(row)
        if (
            row.call.name != "execute_reward_resolution"
            or not isinstance(value, dict)
            or value.get("operation_id", value.get("id"))
            or value.get("status") != "succeeded"
            or row.result_position is None
            or not offer_source.result_position
            < row.result_position
            < boundary.call_position
        ):
            continue
        key = row.call.arguments.get("idempotency_key") or row.call.id
        if key in keys:
            continue
        keys.add(key)
        charge = value.get("funding_charge") or {}
        if charge.get("reserve_id") == reserve["id"]:
            for kind in remaining:
                remaining[kind] -= _money(charge.get(kind, 0))
    return remaining


def _minimum_snapshot(observed, boundary, policy):
    """Original customer terms plus exact, received case-scoped acceptances.

    Public preferences remain immutable after a session acceptance. Revisions
    distinguish a real change and restoration from an idempotent receipt replay.
    Legacy traces with no minimum data retain their previous contract.
    """
    sources, changes = [], []
    for source in observed:
        if source.call.name != "ask_client":
            continue
        request = source.call.arguments.get("request") or {}
        result = payload(source)
        if not isinstance(request, dict) or not isinstance(result, dict):
            continue
        data = result.get("data") or {}
        if isinstance(data, dict) and data.get("minimum_payouts"):
            sources.append((source, request, result, data))
        if (
            result.get("status") == "accepted"
            and request.get("type") == "reward_resolution_minimum_change"
        ):
            changes.append((source, request, result, data))
    if not sources and not changes:
        return {}
    initial = None
    for source, request, result, data in sorted(
        sources,
        key=lambda entry: entry[0].result_position
        if entry[0].result_position is not None
        else float("inf"),
    ):
        if (
            source.call.arguments.get("customer_id") == policy["customer_id"]
            and request.get("type") == "case_clarification"
            and request.get("topic") == "resolution_preferences"
            and result.get("request_type") == "case_clarification"
            and result.get("status") == "answered"
            and result.get("customer_id") == policy["customer_id"]
            and data.get("customer_id") == policy["customer_id"]
            and isinstance(data.get("minimum_payouts"), dict)
            and source.result_position is not None
            and source.result_position < boundary.call_position
        ):
            initial = {
                tid: _money(amount)
                for tid, amount in data["minimum_payouts"].items()
                if tid in policy["transaction_ids"]
            }
            break
    if initial is None or any(
        not amount.is_finite() or amount < 0 for amount in initial.values()
    ):
        raise ValueError("Customer minimum not received")
    snapshot = {tid: (amount, 0, -1) for tid, amount in initial.items()}
    known = dict(initial)
    answers = {}
    for source, request, result, data in sorted(
        changes, key=lambda entry: entry[0].execution_index
    ):
        tid = source.call.arguments.get("transaction_id")
        if (
            source.execution_index >= boundary.execution_index
            or tid not in snapshot
            or not isinstance(data, dict)
            or source.call.arguments.get("customer_id") != policy["customer_id"]
            or request.get("case_id") != policy["case_id"]
            or result.get("customer_id") != policy["customer_id"]
            or result.get("request_type") != "reward_resolution_minimum_change"
            or result.get("status") != "accepted"
            or not isinstance(result.get("answer_id"), str)
            or not result["answer_id"]
            or any(
                data.get(field) != expected
                for field, expected in (
                    ("customer_id", policy["customer_id"]),
                    ("case_id", policy["case_id"]),
                    ("transaction_id", tid),
                )
            )
        ):
            continue
        amount, revision, last_change = snapshot[tid]
        requested = _money(request.get("minimum_amount"))
        received = (
            source.result_position is not None
            and source.result_position < boundary.call_position
        )
        previous = answers.get(result["answer_id"])
        if previous is not None:
            # An idempotent replay can deliver an earlier unreceived acceptance
            # without creating a second transition. Once superseded, even an
            # identical receipt cannot authorize a later restoration (ABA).
            if previous == (tid, requested, result, revision) and received:
                known[tid] = requested
            continue
        if (
            not requested.is_finite()
            or not 0 <= requested <= initial[tid]
            or _money(data.get("previous_minimum")) != amount
            or _money(data.get("requested_minimum")) != requested
            or _money(data.get("effective_minimum")) != requested
        ):
            continue
        changed = requested != amount
        snapshot[tid] = (
            requested,
            revision + changed,
            source.execution_index if changed else last_change,
        )
        answers[result["answer_id"]] = (tid, requested, result, revision + changed)
        if changed:
            # Earlier acceptance evidence belongs to the superseded revision.
            known[tid] = initial[tid]
        if received:
            known[tid] = requested
    # A receipt may justify the final explicit amount even when an intermediate
    # answer was delayed. Unreceived changes still cannot authorize a lowering,
    # and actual changes after consent invalidate its bank-side snapshot.
    return {
        tid: (max(known[tid], amount), revision, last_change)
        for tid, (amount, revision, last_change) in snapshot.items()
    }


def _amendment_packet(args):
    return {
        "resolution_operation_id": args.get("resolution_operation_id"),
        "confirmation_id": args.get("confirmation_id"),
        "expected_revision": args.get("expected_revision"),
        "total_amount": _money(args.get("total_amount")),
        "payouts": sorted(
            [
                (row.get("transaction_id"), _money(row.get("amount")))
                for row in args.get("payouts", [])
            ],
            key=lambda row: str(row[0]),
        ),
    }


def _amendment_failures(observed, target, policy):
    """Authorize changed pending instructions without imposing an optimal amount."""
    args, ident = target.call.arguments, target.call.id
    operation_id = args.get("resolution_operation_id")
    proof = _resolution_receipts(observed, target, policy).get(operation_id)
    failures = []
    if (
        not proof
        or proof[0].get("status") != "execution_unknown"
        or not proof[0].get("retry_required")
        or not proof[0].get("ledger")
    ):
        return [f"{ident}:amendment_operation_not_received"]
    state, origin, _ = proof
    original = origin.call.arguments
    packet = _amendment_packet(args)
    rows = packet["payouts"]
    scope = {row.get("transaction_id") for row in original.get("payouts", [])}
    if (
        not rows
        or len({tid for tid, _ in rows}) != len(rows)
        or any(
            tid not in scope or not amount.is_finite() or amount <= 0
            for tid, amount in rows
        )
        or sum((amount for _, amount in rows), Decimal(0)) != packet["total_amount"]
    ):
        failures.append(f"{ident}:amendment_outside_original_scope")
    consent_source = None
    for source in observed:
        if (
            source.call.name != "ask_client"
            or source.result_position is None
            or source.result_position >= target.call_position
        ):
            continue
        consent = payload(source)
        if not isinstance(consent, dict):
            continue
        data = consent.get("data") or {}
        request = source.call.arguments.get("request") or {}
        if not isinstance(data, dict) or not isinstance(request, dict):
            continue
        if (
            source.call.arguments.get("customer_id") == policy["customer_id"]
            and request.get("type") == "reward_resolution_confirmation"
            and consent.get("customer_id") == policy["customer_id"]
            and consent.get("status") == "confirmed"
            and consent.get("confirmation_id") == args.get("confirmation_id")
            and data.get("resolution_operation_id") == operation_id
            and request.get("resolution_operation_id") == operation_id
            and all(
                data.get(key) == original.get(key)
                for key in ("offer_id", "offer_version", "case_id")
            )
            and _money(data.get("total_amount")) == packet["total_amount"]
            and _amendment_packet(data)["payouts"] == rows
            and data.get("state_revision") == args.get("expected_revision")
            and sorted(data.get("component_ids") or [])
            == sorted(original.get("component_ids") or [])
            and (data.get("eligibility_evidence") or {})
            == (original.get("eligibility_evidence") or {})
            and _amendment_packet(request)["payouts"] == rows
            and sorted(request.get("component_ids") or [])
            == sorted(data.get("component_ids") or [])
            and sorted(
                request.get("resulting_claims") or [],
                key=lambda row: row["transaction_id"],
            )
            == sorted(
                data.get("resulting_claims") or [],
                key=lambda row: row["transaction_id"],
            )
            and all(
                request.get(key) == data.get(key)
                for key in (
                    "offer_id",
                    "offer_version",
                    "case_id",
                    "total_amount",
                    "state_revision",
                )
            )
        ):
            consent_source = source
    if consent_source is None:
        failures.append(f"{ident}:amendment_packet_not_confirmed")
        return failures
    before_consent = _resolution_receipts(observed, consent_source, policy).get(
        operation_id
    )
    if (
        not before_consent
        or before_consent[0].get("status") != "execution_unknown"
        or not before_consent[0].get("retry_required")
        or not before_consent[0].get("ledger")
    ):
        failures.append(f"{ident}:amendment_operation_not_received_before_consent")
    elif _money(before_consent[0].get("state_revision")) > _money(
        args.get("expected_revision")
    ) or _money(state.get("state_revision")) > _money(args.get("expected_revision")):
        failures.append(f"{ident}:amendment_consent_stale")
    return failures


def reward_business_failures(trace, policy):
    """Validate every successful protected mutation, including later repaired ones."""
    if not policy:
        return []
    if hasattr(policy, "model_dump"):
        policy = policy.model_dump()
    observed = sorted(trace.observed, key=lambda row: row.execution_index)
    failures, executions, spent = [], {}, {}
    payments = []
    amendments = {}
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
                "failed_final",
            }
        ):
            continue
        name, args, ident = target.call.name, target.call.arguments, target.call.id
        if name == "amend_reward_resolution":
            amendment_key = args.get("resolution_operation_id")
            packet = _amendment_packet(args)
            if amendments.get(amendment_key) != packet:
                failures.extend(_amendment_failures(observed, target, policy))
                amendments[amendment_key] = packet
        if name == "retry_reward_resolution":
            operation_id = args.get("resolution_operation_id")
            proof = _resolution_receipts(observed, target, policy).get(operation_id)
            if not proof or proof[0].get("status") not in {
                "execution_unknown",
                "succeeded",
                "failed_final",
            }:
                failures.append(f"{ident}:retry_operation_not_received")
            for prior in observed:
                changed = payload(prior)
                if (
                    prior.call.name == "amend_reward_resolution"
                    and prior.execution_index < target.execution_index
                    and prior.call.arguments.get("resolution_operation_id")
                    == operation_id
                    and isinstance(changed, dict)
                    and changed.get("status") == "execution_unknown"
                    and (
                        not proof
                        or _money(proof[0].get("state_revision"))
                        < _money(changed.get("state_revision"))
                    )
                ):
                    failures.append(f"{ident}:amended_operation_not_received")
                    break
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
            changed_items = {row.get("transaction_id") for row in args.get("items", [])}
            for prior in observed:
                prior_result = payload(prior)
                if (
                    prior.call.name == "execute_reward_resolution"
                    and prior.call_position < target.call_position
                    and prior.call.arguments.get("case_id") == args.get("case_id")
                    and changed_items.intersection(
                        row.get("transaction_id")
                        for row in prior.call.arguments.get("payouts", [])
                    )
                    and isinstance(prior_result, dict)
                    and prior_result.get("status") == "succeeded"
                    and (
                        prior.result_position is None
                        or prior.result_position >= target.call_position
                    )
                    and not any(
                        source.call.arguments.get("idempotency_key")
                        and source.call.arguments.get("idempotency_key")
                        == prior.call.arguments.get("idempotency_key")
                        and _execution_packet(source.call.name, source.call.arguments)
                        == _execution_packet(prior.call.name, prior.call.arguments)
                        and isinstance(payload(source), dict)
                        and payload(source).get("status") == "succeeded"
                        for source in received(target, "execute_reward_resolution")
                    )
                ):
                    failures.append(
                        f"{ident}:payment_result_not_received:{prior.call.id}"
                    )
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
                    and (data.get("fallback_operation_id") or None)
                    == (args.get("fallback_operation_id") or None)
                ):
                    confirmed = True
                    consent_source = source
            if not confirmed:
                failures.append(f"{ident}:resolution_packet_not_confirmed")
            if consent_source is not None:
                try:
                    minimums = _minimum_snapshot(observed, consent_source, policy)
                    current = _minimum_snapshot(observed, target, policy)
                    if any(
                        _money(row.get("amount")) < minimums[row["transaction_id"]][0]
                        for row in payouts
                        if row.get("transaction_id") in minimums
                    ):
                        failures.append(f"{ident}:resolution_below_customer_minimum")
                    if any(
                        minimums.get(tid) != current.get(tid)
                        and current.get(tid, (None, 0, -1))[2]
                        > consent_source.execution_index
                        for tid in tids
                    ):
                        failures.append(
                            f"{ident}:resolution_minimum_changed_after_consent"
                        )
                except ValueError:
                    failures.append(f"{ident}:customer_minimum_not_received")
            offer = None
            offer_source = None
            component_costs = None
            for source in received(target, "get_reward_resolution_offers"):
                for row in records(payload(source)):
                    if row.get("id") == args.get("offer_id"):
                        offer = row
                        offer_source = source
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
                            offer_source = source
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
                    total, expected, component_costs = _component_amounts(
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
            if consent_source is not None:
                for source in observed:
                    row = payload(source)
                    if (
                        source.call.name
                        in {"execute_reward_resolution", "get_reward_resolution_status"}
                        and source.result_position is not None
                        and source.result_position < target.call_position
                        and isinstance(row, dict)
                        and all(
                            row.get(field) == policy[field]
                            for field in ("customer_id", "case_id", "period")
                        )
                        and row.get("state_revision") is not None
                        and _money(row["state_revision"])
                        > _money(args.get("expected_revision"))
                    ):
                        failures.append(f"{ident}:resolution_consent_stale")
                        break
            predecessor = (offer or {}).get("fallback_for_offer_id")
            if predecessor or args.get("fallback_operation_id"):
                proof = _resolution_receipts(
                    observed, consent_source or target, policy
                ).get(args.get("fallback_operation_id"))
                valid = False
                if predecessor and proof:
                    terminal, origin, _ = proof
                    hold = payload(origin).get("funding_hold") or {}
                    release = terminal.get("released_funding") or {}
                    valid = (
                        terminal.get("status") == "failed_final"
                        and terminal.get("offer_id") == predecessor
                        and {
                            row.get("transaction_id")
                            for row in origin.call.arguments.get("payouts", [])
                        }
                        == tids
                        and bool(release)
                        and not terminal.get("ledger")
                        and not terminal.get("funding_hold")
                        and not terminal.get("funding_charge")
                        and all(
                            _money(hold.get(kind)).is_finite()
                            and _money(hold.get(kind)) >= 0
                            for kind in ("bank", "partner")
                        )
                        and sum(
                            (_money(hold.get(kind)) for kind in ("bank", "partner")),
                            Decimal(0),
                        )
                        > 0
                        and all(
                            _money(release.get(kind)) == _money(hold.get(kind))
                            for kind in ("bank", "partner")
                        )
                        and release.get("reserve_id") == hold.get("reserve_id")
                    )
                if not valid:
                    failures.append(f"{ident}:fallback_failure_not_received")
            budget = (offer or {}).get("selection_budget") or {}
            if str(budget.get("shared", False)).lower() == "true":
                reserve = offer.get("funding_reserve") or {}
                if (
                    not reserve
                    or reserve.get("id") != budget.get("id")
                    or component_costs is None
                    or offer_source is None
                    or any(
                        _money(reserve.get(source)) != _money(budget.get(source))
                        for source in ("bank", "partner")
                    )
                ):
                    failures.append(f"{ident}:shared_funding_not_received")
                else:
                    charge = (
                        result.get(
                            "funding_hold"
                            if result.get("status") == "execution_unknown"
                            else "funding_charge"
                        )
                        or {}
                    )
                    if charge.get("reserve_id") != reserve["id"] or any(
                        _money(charge.get(source)) != component_costs[source]
                        for source in ("bank", "partner")
                    ):
                        failures.append(f"{ident}:shared_funding_charge_mismatch")
                    remaining = _remaining_funding(
                        observed,
                        consent_source or target,
                        offer_source,
                        reserve,
                        policy,
                    )
                    if any(
                        not remaining[source].is_finite()
                        or remaining[source] < 0
                        or remaining[source] > _money(reserve.get(source))
                        or component_costs[source] > remaining[source]
                        for source in ("bank", "partner")
                    ):
                        failures.append(f"{ident}:shared_funding_exceeded")
            for operation, (state, origin, _) in _resolution_receipts(
                observed, target, policy
            ).items():
                if state["status"] not in {"execution_unknown", "succeeded"}:
                    continue
                if tids.intersection(
                    row.get("transaction_id")
                    for row in origin.call.arguments.get("payouts", [])
                ):
                    failures.append(f"{ident}:overlapping_reward_execution")
                    break
            if result.get("status") == "execution_unknown":
                continue
            if tids & (
                spent.get("resolution_transactions", set())
                | spent.get("adjustment_transactions", set())
            ):
                failures.append(f"{ident}:overlapping_reward_execution")
            spent.setdefault("resolution_transactions", set()).update(tids)
    return list(dict.fromkeys(failures))
