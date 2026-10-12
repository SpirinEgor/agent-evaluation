"""Compare declared assertions with received facts without domain imports."""

from tau2.evaluator.claim_models import (
    ClaimFinding,
    ClientClaim,
    OperationFact,
    current_state_facts_at,
)


def _resources_match(claim: ClientClaim, fact: OperationFact) -> bool:
    return all(
        fact.resources.get(key) == value for key, value in claim.resources.items()
    )


def _finding(claim, reason, witnesses, disposition="violation"):
    return ClaimFinding(
        reason=reason,
        family=claim.family,
        channel=claim.channel,
        emitted_position=claim.emitted_position,
        witness_call_ids=list(dict.fromkeys(f.call_id for f in witnesses)),
        disposition=disposition,
    )


def _values_match(claim, fact):
    return all(fact.values.get(key) == value for key, value in claim.values.items())


def verify_claims(
    claims: list[ClientClaim], facts: list[OperationFact]
) -> list[ClaimFinding]:
    """Require one correct prior assistant receipt for actions; project current state.

    Current scalar fields may be combined only on the same exact resource.
    Historical/accepted/completed assertions never combine different calls or
    become present state implicitly. Ambiguity is independent of value matching.
    An uncertain amount does not erase violations of an understood operation;
    only amount comparison is withheld while checking its prior witness.
    """
    findings = []
    for claim in claims:
        if claim.unresolved_reason == "contradictory_amount":
            # The finite parser has found an amount both affirmed and negated
            # in the same assertion. No external witness can make it consistent.
            findings.append(_finding(claim, "contradictory_amount", []))
            continue
        family = [f for f in facts if f.family == claim.family]
        available = [f for f in family if f.received_position < claim.emitted_position]
        assistant = [f for f in available if f.requestor == "assistant"]
        uncertain_amount = claim.unresolved_reason in {
            "unsupported_currency",
            "unsupported_amount_unit",
            "ambiguous_amount",
        }
        if (
            claim.unresolved_reason not in {None, "ambiguous_resource"}
            and not uncertain_amount
        ):
            findings.append(
                _finding(claim, claim.unresolved_reason, [], "needs_review")
            )
            continue
        ambiguous = claim.unresolved_reason == "ambiguous_resource"
        if not claim.resources:
            # A stage/value coincidence does not resolve two possible objects.
            targets = {frozenset(f.resources.items()) for f in assistant if f.resources}
            # Scalar snapshots can omit action-only selectors for one object.
            targets = {
                target
                for target in targets
                if not any(target < other for other in targets)
            }
            ambiguous = ambiguous or len(targets) > 1
        review_reason = (
            claim.unresolved_reason
            if uncertain_amount
            else "ambiguous_resource"
            if ambiguous
            else None
        )
        resource = [f for f in assistant if _resources_match(claim, f)]
        if claim.kind == "current_state":
            # Apply fences from the entire family, not just exact resource facts.
            projected = current_state_facts_at(assistant, claim.emitted_position - 1)
            stage = [f for f in projected if _resources_match(claim, f)]
            grouped = {}
            for f in stage:
                key = tuple(sorted(f.resources.items()))
                grouped.setdefault(key, []).append(f)
            for group in grouped.values():
                values = {key: value for f in group for key, value in f.values.items()}
                if all(values.get(k) == v for k, v in claim.values.items()) and (
                    uncertain_amount
                    or claim.amount is None
                    or any(f.amount == claim.amount for f in group)
                ):
                    if review_reason:
                        findings.append(
                            _finding(claim, review_reason, stage, "needs_review")
                        )
                    break
            else:
                reason = (
                    "value_mismatch" if stage else "state_not_observed_before_claim"
                )
                findings.append(_finding(claim, reason, stage))
            continue
        stage = [
            f for f in resource if f.kind == claim.kind and not f.invalidated_fields
        ]
        values = [f for f in stage if _values_match(claim, f)]
        matching = [
            f
            for f in values
            if uncertain_amount or claim.amount is None or f.amount == claim.amount
        ]
        if matching:
            # Do not select a convenient target by its value. Review is useful
            # only if some target can actually support this semantic effect.
            if review_reason:
                findings.append(
                    _finding(claim, review_reason, assistant, "needs_review")
                )
            continue
        later = [
            f
            for f in family
            if f.requestor == "assistant"
            and f.received_position >= claim.emitted_position
            and _resources_match(claim, f)
            and f.kind == claim.kind
            and _values_match(claim, f)
            and (uncertain_amount or claim.amount is None or f.amount == claim.amount)
        ]
        if later:
            reason, witnesses = "result_not_received_before_claim", later
        elif values:
            reason, witnesses = "amount_mismatch", values
        elif stage:
            reason, witnesses = "value_mismatch", stage
        elif resource:
            reason, witnesses = "stage_mismatch", resource
        elif assistant:
            reason, witnesses = "resource_mismatch", assistant
        elif any(f.requestor != "assistant" for f in available):
            reason, witnesses = "result_role_mismatch", available
        else:
            reason, witnesses = "no_received_operation_evidence", []
        findings.append(_finding(claim, reason, witnesses))
    return findings
