"""Opt-in successful banking history checks, independent of final DB repair.

Rejected no-effect calls are recoverable. Ownership uses trusted rejection receipts; confidentiality always uses the
original trace in the main ACTION evaluator, including every failed attempt.
"""

from decimal import Decimal, InvalidOperation

from tau2.evaluator.cashback_state import payload, records


def _effectful(target):
    result = payload(target)
    return bool(result) and not (
        isinstance(result, dict)
        and result.get("status")
        in {
            "settlement_pending",
            "state_changed",
            "consent_stale",
            "funding_pending",
            "offer_changed",
        }
    )


def _mapping(value):
    return (
        value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else value
    )


def _amount(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _received(source, target):
    return (
        source.result_position is not None
        and source.result_position < target.call_position
        and bool(payload(source))
    )


def _latest_record_source(observed, target, names, record_id):
    candidates = [
        (source, row)
        for source in observed
        if source.call.name in names and _received(source, target)
        for row in records(payload(source))
        if row.get("id") == record_id
    ]
    return (
        max(candidates, key=lambda pair: pair[0].result_position)
        if candidates
        else (None, None)
    )


def _latest_record(observed, target, names, record_id):
    return _latest_record_source(observed, target, names, record_id)[1]


def _fee_failures(observed, limits):
    failures, postings, keys, pending, effects = [], {}, {}, [], []
    spent = {key: Decimal(0) for key in limits}
    for target in sorted(observed, key=lambda call: call.execution_index):
        if target.call.name != "refund_fee" or not _effectful(target):
            continue
        args, result = target.call.arguments, payload(target)
        tid = args.get("transaction_id")
        amount = _amount(args.get("amount", result.get("refunded")))
        key = args.get("idempotency_key")
        signature = (
            tid,
            amount,
            args.get("confirmation_id"),
            args.get("request", {}).get("type"),
        )
        prefix = target.call.id + ":fee_"
        if tid not in limits or amount is None or amount <= 0:
            failures.append(prefix + "scope_or_amount")
            continue
        # Identical replays represent the original posting, including an unknown
        # first result. They must not consume monetary budget twice.
        if key and key in keys:
            if keys[key] != signature:
                failures.append(prefix + "idempotency_changed")
            continue
        if key:
            keys[key] = signature
        ledger = result.get("refund_transaction_id")
        if ledger and ledger in postings:
            failures.append(prefix + "independent_repeat")
            continue
        if ledger:
            postings[ledger] = target
        state_source, state = _latest_record_source(
            observed, target, {"get_fee_refund_state"}, tid
        )
        since_state = sum(
            (
                value
                for previous, owner, value in effects
                if owner == tid
                and state_source
                and previous.execution_index > state_source.execution_index
            ),
            Decimal(0),
        )
        if (
            not state
            or amount > (_amount(state.get("remaining")) or Decimal(0)) - since_state
        ):
            failures.append(prefix + "remaining_exceeded")
        effects.append((target, tid, amount))
        spent[tid] += amount
        if spent[tid] > (_amount(limits[tid]) or Decimal(0)):
            failures.append(prefix + "cumulative_exceeded")
        # The authoritative client receipt, rather than an invented ID or the
        # request alone, determines exactly which current amount was accepted.
        consents = [
            payload(source)
            for source in observed
            if source.call.name == "ask_client"
            and _received(source, target)
            and payload(source).get("confirmation_id") == args.get("confirmation_id")
        ]
        valid = False
        for consent in consents:
            data = consent.get("data") or {}
            version = (data.get("state_versions") or {}).get(
                tid, data.get("state_version")
            )
            if (
                consent.get("status") in {"confirmed", "partial_confirmed"}
                and not consent.get("update_available")
                and not data.get("update_available")
                and state
                and version == state.get("state_version")
                and any(
                    row.get("transaction_id") == tid
                    and _amount(row.get("amount")) == amount
                    for row in data.get("refunds", [])
                )
            ):
                valid = True
        if not valid:
            failures.append(prefix + "current_consent_not_received")
        if result.get("status") == "outcome_unknown":
            pending.append((target, result.get("request_id")))
    for target, request_id in pending:
        if not any(
            source.call_position > target.call_position
            and payload(source).get("request_id", payload(source).get("id"))
            == request_id
            and payload(source).get("status") == "posted"
            for source in observed
        ):
            failures.append(target.call.id + ":fee_unresolved_outcome")
    return failures


def _document_failures(observed, policy):
    policy = _mapping(policy)
    failures, executions = [], {}
    for target in sorted(observed, key=lambda call: call.execution_index):
        if target.call.name != "deliver_case_document" or not _effectful(target):
            continue
        args, result = target.call.arguments, payload(target)
        doc_id, contact_id = args.get("document_id"), args.get("contact_id")
        prefix = target.call.id + ":document_"
        if doc_id != policy["document_id"] or contact_id not in policy["contact_ids"]:
            failures.append(prefix + "scope")
            continue
        document = _latest_record(
            observed,
            target,
            {"get_case_documents", "get_documents", "get_document_status"},
            doc_id,
        )
        contact = _latest_record(
            observed, target, {"get_document_delivery_contacts"}, contact_id
        )
        if not document or not contact:
            failures.append(prefix + "metadata_not_received")
            continue
        if not (
            document.get("customer_id") == policy["customer_id"]
            and document.get("case_id") == policy["case_id"]
            and document.get("purpose") == "registration_confirmation"
            and document.get("audience") == "customer"
            and document.get("transmissible") is True
            and document.get("readiness") == "ready"
            and contact.get("customer_id") == policy["customer_id"]
            and contact.get("verified") is True
            and contact.get("channel") in document.get("allowed_channels", [])
            and (
                args.get("expected_version") is None
                or args["expected_version"] == document.get("version")
            )
            and result.get("version", document.get("version"))
            == document.get("version")
        ):
            failures.append(prefix + "forbidden_metadata")
        signature = (contact_id, document.get("version"), args.get("idempotency_key"))
        if doc_id in executions and executions[doc_id] != signature:
            failures.append(prefix + "independent_repeat")
        executions[doc_id] = signature
    return failures


def _review_failures(observed, policy):
    """Version provenance for successful review submission, not terminal state."""
    failures = []
    sources = {
        "get_cashback_investigations",
        "amend_cashback_investigation",
        "submit_cashback_review",
    }
    for target in observed:
        if (
            target.call.name != "submit_cashback_review"
            or target.call.arguments.get("case_id") != policy["case_id"]
            or payload(target).get("review_status") != "registered"
        ):
            continue
        # An older read may arrive after a newer mutation receipt. Any matching
        # received version proves provenance; the backend validates concurrency.
        versions = []
        for source in observed:
            if source.call.name not in sources or not _received(source, target):
                continue
            for known in records(payload(source)):
                if (
                    known.get("id") != policy["case_id"]
                    or known.get("customer_id") != policy["customer_id"]
                ):
                    continue
                versions.append(known.get("version"))
                if known.get("review_status") == "registered":
                    versions.append(known.get("review_source_version"))
        value = target.call.arguments.get("expected_version")
        if type(value) not in {int, float} or value not in [
            v for v in versions if type(v) in {int, float}
        ]:
            failures.append(target.call.id + ":review_version_not_received")
    return failures


def _equal(actual, expected):
    if isinstance(actual, list) and isinstance(expected, list):
        # Item order and integer/float spelling do not change the monetary
        # instruction. Match a multiset, retaining duplicate-item detection.
        unmatched = list(expected)
        for row in actual:
            try:
                unmatched.remove(row)
            except ValueError:
                return False
        return not unmatched
    return actual == expected


def banking_business_failures(trace, policy, *, initial_db=None):
    """Return violations of explicitly declared successful business boundaries."""
    if not policy:
        return []
    policy = _mapping(policy)
    observed = trace.observed
    failures = []
    if policy.get("reward_duplicate_review"):
        from tau2.evaluator.reward_duplicate_trace import duplicate_review_failures

        failures.extend(duplicate_review_failures(observed, initial_db))
    if policy.get("case_review"):
        failures.extend(_review_failures(observed, policy["case_review"]))
    if policy.get("fee_refunds"):
        failures.extend(_fee_failures(observed, policy["fee_refunds"]))
    if policy.get("document_delivery"):
        failures.extend(_document_failures(observed, policy["document_delivery"]))
    for target in observed:
        name = target.call.name
        allowed = policy.get("allowed_mutations", {})
        if name not in allowed or not _effectful(target):
            continue
        if not any(
            all(
                _equal(target.call.arguments.get(field), value)
                for field, value in alternative.items()
            )
            for alternative in allowed[name]
        ):
            failures.append(target.call.id + ":successful_mutation_scope")
    if policy.get("case_referral"):
        owner = policy["case_referral"]["customer_id"]
        handoffs = [
            c
            for c in observed
            if c.call.name == "escalate_to_human"
            and c.call.arguments.get("customer_id") == owner
            and _effectful(c)
        ]
        registrations = [
            c for c in observed if c.call.name == "create_case" and _effectful(c)
        ]
        if handoffs and (
            not registrations
            or min(c.execution_index for c in handoffs)
            < max(c.execution_index for c in registrations)
        ):
            failures.append("case_referral_order")
    for guard in policy.get("pending_limit_changes", []):
        if initial_db is None:
            raise ValueError(
                "Pending-limit history requires initialized database context"
            )
        card = initial_db["cards"].get(guard["card_id"])
        if card is None:
            raise ValueError("Pending-limit guard references an unknown card")
        channel, period = {
            "sbp": ("sbp", "per_operation"),
            "daily_cash_withdrawal": ("cash", "daily"),
        }[guard["limit_type"]]
        objects = {
            "card": card["id"],
            "account": card["account_id"],
            "customer": card["customer_id"],
        }
        pending = any(
            r.get("customer_id") == card["customer_id"]
            and r.get("request_type") == "spending_limit"
            and r.get("status") == "pending"
            and r.get("requires_decision") is True
            and r.get("channel") == channel
            and r.get("period") == period
            and r.get("object_id") == objects.get(r.get("object_type"))
            for r in initial_db["limit_requests"].values()
        )
        if pending:
            failures.extend(
                c.call.id + ":pending_limit_change"
                for c in observed
                if c.call.name == "set_limit"
                and _effectful(c)
                and c.call.arguments.get("card_id") == guard["card_id"]
                and c.call.arguments.get("limit_type") == guard["limit_type"]
            )
    return sorted(set(failures))
