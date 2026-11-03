"""Successful payout restrictions and received bank decisions, for HALF and FULL."""

from copy import deepcopy

from tau2.evaluator.cashback_state import payload, records


def _received_record(observed, target, names, ident, fields):
    return any(
        source.call.name in names
        and source.result_position is not None
        and source.result_position < target.call_position
        and any(
            row.get("id") == ident
            and all(str(row.get(k)) == str(v) for k, v in fields.items())
            for row in records(payload(source))
        )
        for source in observed
    )


def duplicate_review_failures(observed, initial_db):
    if initial_db is None:
        raise ValueError("Duplicate review requires initialized database context")
    reviews = deepcopy(initial_db.get("reward_duplicate_reviews", {}))
    bases = initial_db.get("payment_bases", {})
    failures, posted, decisions = [], set(), {}
    accepted_operations = set(initial_db.get("reward_resolution_executions", {}))
    for target in sorted(observed, key=lambda c: c.execution_index):
        # Only bank receipts delivered before this call update known authority.
        available_bases = deepcopy(bases)
        for source in sorted(
            observed,
            key=lambda c: c.result_position if c.result_position is not None else -1,
        ):
            if (
                source.call.name == "get_payment_basis"
                and source.result_position is not None
                and source.result_position < target.call_position
            ):
                for row in records(payload(source)):
                    if row.get("id") in bases:
                        available_bases[row["id"]] = row
        for review in reviews.values():
            basis = available_bases.get(review.get("decision_basis_id"))
            if review["status"] in {"cleared", "duplicate_confirmed"} and (
                basis is None
                or str(basis.get("revision", 1))
                != str(review.get("decision_basis_revision"))
            ):
                review["status"] = "pending"
        result = payload(target)
        if not isinstance(result, dict) or not result:
            continue
        name, args = target.call.name, target.call.arguments
        if name == "resolve_reward_duplicate_review" and result.get("status") in {
            "cleared",
            "duplicate_confirmed",
        }:
            review = reviews.get(args.get("review_id"))
            basis = available_bases.get(args.get("basis_id"))

            valid = bool(review and basis) and (
                basis.get("customer_id") == review["customer_id"]
                and basis.get("period") == review["period"]
                and basis.get("external_reference") == review["external_reference"]
                and str(basis.get("revision", 1)) == str(args.get("basis_revision"))
                and review["status"] != "queued"
                and args.get("expected_revision")
                in (
                    {review["revision"]}
                    if review["status"] == "pending"
                    else {review["revision"], review["revision"] - 1}
                )
                and _received_record(
                    observed,
                    target,
                    {"get_payment_basis"},
                    basis["id"],
                    {
                        "revision": args["basis_revision"],
                        "external_reference": review["external_reference"],
                        "status": "final",
                    },
                )
                and _received_record(
                    observed,
                    target,
                    {
                        "get_reward_duplicate_reviews",
                        "execute_reward_resolution",
                        "get_reward_resolution_status",
                        "resolve_reward_duplicate_review",
                    },
                    review["id"],
                    {
                        "revision": args["expected_revision"],
                        "customer_id": review["customer_id"],
                    },
                )
            )
            expected = (
                "duplicate_confirmed"
                if basis
                and review
                and any(
                    line.get("purpose") == "cashback"
                    and line.get("purchase_id") == review["transaction_id"]
                    and line.get("reward_program") == review["reward_program"]
                    for line in basis["lines"]
                )
                else "cleared"
            )
            if not valid or result["status"] != expected:
                failures.append(target.call.id + ":duplicate_review_evidence")
            elif review["status"] == "pending":
                review.update(
                    status=expected,
                    revision=review["revision"] + 1,
                    decision_basis_revision=args["basis_revision"],
                    decision_basis_id=args["basis_id"],
                )
                decisions[review["id"]] = target
        if name not in {
            "execute_reward_resolution",
            "get_reward_resolution_status",
            "retry_reward_resolution",
        }:
            continue
        # Registering a new asynchronous intent also consumes authority/reserve.
        operation_id = result.get("id", result.get("operation_id"))
        if (
            name == "execute_reward_resolution"
            and operation_id
            and operation_id not in accepted_operations
        ):
            if any(
                review["status"] in {"pending", "duplicate_confirmed"}
                and any(
                    row.get("transaction_id") == review["transaction_id"]
                    for row in args.get("payouts", [])
                )
                for review in reviews.values()
            ):
                failures.append(
                    target.call.id + ":execution_under_duplicate_restriction"
                )
            accepted_operations.add(operation_id)
        # Only new posted rows count; replaying a previous result is not a payout.
        for row in result.get("ledger", []):
            if row.get("status") != "posted" or row.get("id") in posted:
                continue
            if any(
                (
                    r["status"] in {"pending", "duplicate_confirmed"}
                    or r["id"] in decisions
                    and (
                        decisions[r["id"]].result_position is None
                        or decisions[r["id"]].result_position >= target.call_position
                    )
                    and not _received_record(
                        observed,
                        target,
                        {
                            "get_reward_duplicate_reviews",
                            "get_reward_resolution_status",
                            "resolve_reward_duplicate_review",
                        },
                        r["id"],
                        {
                            key: r[key]
                            for key in (
                                "customer_id",
                                "case_id",
                                "transaction_id",
                                "revision",
                                "status",
                                "decision_basis_id",
                                "decision_basis_revision",
                            )
                        },
                    )
                )
                and r["customer_id"] == row.get("customer_id")
                and r["transaction_id"] == row.get("original_transaction_id")
                for r in reviews.values()
            ):
                failures.append(target.call.id + ":payout_under_duplicate_restriction")
            posted.add(row.get("id"))
        if result.get("retry_required") and result.get("ledger"):
            for review in reviews.values():
                if (
                    review["status"] == "queued"
                    and review["trigger_offer_id"] == result.get("offer_id")
                    and review["case_id"] == result.get("case_id")
                    and review["customer_id"] == result.get("customer_id")
                ):
                    review.update(status="pending", revision=review["revision"] + 1)
    return failures
