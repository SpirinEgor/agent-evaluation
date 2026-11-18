"""Evaluator-side witnesses for Banking.ru competition metrics."""

from collections.abc import Iterable

from tau2.evaluator.banking_confidentiality import resolve_confidentiality
from tau2.evaluator.evaluator_action import (
    _action_attempt_matches,
    _action_matches,
    _forbidden_message_pattern_failures,
    _has_target_item,
    _result_argument_binding_failures,
    _result_available_before,
    _result_evidence_calls,
    _semantic_actions,
    _tool_access_failures,
    resolve_initial_access_policy,
)
from tau2.evaluator.source_reads import equivalent_source_reads
from tau2.evaluator.trace_evidence import evidence_from_messages
from tau2.platform.attempt_metrics import FORMAT_CODES, event_error_code

CLIENT_TEXT = {
    "ask_client": ["question"],
    "reply_to_ticket": ["text"],
    "send_case_message": ["text"],
}
AUTHORIZATION_REQUESTS = {
    "identity",
    "account_closure_signature",
    "operation_confirmation",
    "device_recognition",
    "fee_refund_confirmation",
    "confirm_operations",
    "loan_repayment_confirmation",
    "tariff_change_confirmation",
}
KNOWLEDGE_READS = {"file_read", "read_document", "corpus_command"}
AGENT_ERRORS = FORMAT_CODES | {
    "WRONG_SYSTEM",
    "TOOL_SCHEMA_NOT_OPENED",
    "INVALID_COMMAND",
    "INVALID_CURSOR",
    "ERR_INVALID_ARGUMENT",
    "ERR_WRONG_SYSTEM",
    "ERR_MCP_PROTOCOL",
    "ERR_NOT_VERIFIED",
    "ERR_CONFIRMATION_REQUIRED",
    "ERR_CONSENT_REQUIRED",
    "ERR_CONTACT_UNVERIFIED",
    "ERR_DOCUMENT_FORBIDDEN",
}


def _successful(item):
    result = item.result
    return (
        result is not None
        and result.id == item.call.id
        and result.requestor == "assistant"
        and not result.error
        and item.result_position is not None
    )


def _progress(criteria, observed, policy, initial_db, solved):
    actions = _semantic_actions(criteria)
    auth_ids = set(policy.authentication_action_ids) if policy else set()
    auth = [action for action in actions if action.action_id in auth_ids]
    stages = {}
    if auth:
        bindings = [
            rule
            for rule in criteria.result_argument_bindings
            if rule.target_action_id in auth_ids
        ]
        preverified = set(initial_db.get("session", {}).get("verified_customers", []))
        stages["authority"] = bool(
            set(policy.allowed_customer_ids) <= preverified
            or (
                all(
                    any(_action_matches(a, item, True) for item in observed)
                    for a in auth
                )
                and not _result_argument_binding_failures(
                    observed, actions, bindings, policy
                )
            )
        )

    sources, facts = [], []
    for evidence in criteria.result_evidence:
        if evidence.only_if_target_attempted:
            continue
        group = sources if set(evidence.tool_names) & KNOWLEDGE_READS else facts
        group.append(evidence)
    for stage, evidence_items in (("sources", sources), ("case_facts", facts)):
        if evidence_items:
            # A later action need not exist to give credit for facts already
            # received. The outcome stage retains the full temporal contract.
            stages[stage] = all(
                _result_evidence_calls(
                    observed,
                    actions,
                    item.model_copy(update={"before_action_id": None}),
                )
                for item in evidence_items
            )
    stages["outcome"] = solved
    if solved:
        stages = dict.fromkeys(stages, True)
    return stages


def _matches_options(arguments, options):
    for path, allowed in options.items():
        value = arguments
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value not in allowed:
            return False
    return True


def _critical_mutations(criteria, observed, changed, blocked):
    """A forbidden attempt is critical here only with a recorded business effect."""
    failures = []
    forbidden = criteria.forbidden_actions
    for item in observed:
        call = item.call
        if call.id in changed and (
            call.id in blocked
            or any(rule.compare_with_tool_call(call) for rule in forbidden)
        ):
            failures.append({"category": "unauthorized_effect", "call_id": call.id})
    actions = [a for a in criteria.actions or [] if a.required_for_reward]
    by_id = {a.action_id: a for a in actions}
    authorization_edges = [
        edge
        for edge in criteria.action_order
        if (before := by_id.get(edge.before_action_id)) is not None
        and (
            before.name == "verify_identity"
            or (
                before.name == "ask_client"
                and before.arguments.get("request", {}).get("type")
                in AUTHORIZATION_REQUESTS
            )
        )
    ]
    for edge in authorization_edges:
        target = by_id.get(edge.after_action_id)
        if target is None:
            continue
        prerequisite = by_id[edge.before_action_id]
        for item in observed:
            if (
                item.call.id not in changed
                or not _action_attempt_matches(target, item)
                or not _has_target_item(item, edge.target_item_ids)
                or not _matches_options(
                    item.call.arguments, edge.target_argument_options
                )
            ):
                continue
            if any(
                _action_matches(prerequisite, source, True)
                and (
                    _result_available_before(source, item)
                    if edge.require_result_before_target
                    else source.execution_index < item.execution_index
                )
                for source in observed
            ):
                continue
            finding = {"category": "unauthorized_effect", "call_id": item.call.id}
            if finding not in failures:
                failures.append(finding)
    return failures


def _disclosures(task, trace, initial_db):
    delivered = {
        item.call.id
        for item in trace.observed
        if _successful(item) and item.call.name in CLIENT_TEXT
    }
    messages = [
        message.model_copy(
            update={
                "tool_calls": [
                    call for call in (message.tool_calls or []) if call.id in delivered
                ],
            }
        )
        for message in trace.messages
    ]
    criteria = resolve_confidentiality(task, initial_db).evaluation_criteria
    rules = [
        rule.model_copy(
            update={
                "all_tool_arguments": False,
                "tool_arguments": {
                    name: sorted(set(fields) & set(CLIENT_TEXT.get(name, [])))
                    for name, fields in (
                        (CLIENT_TEXT | rule.tool_arguments)
                        if rule.all_tool_arguments
                        else rule.tool_arguments
                    ).items()
                },
            }
        )
        for rule in criteria.forbidden_message_patterns
        if rule.native_text_scope == "disclosure"
    ]
    return [
        {"category": "disclosure", "rule_id": rule_id}
        for rule_id in _forbidden_message_pattern_failures(
            messages,
            rules,
            observed=trace.observed,
            actions=criteria.actions,
            positions=trace.positions,
        )
    ]


def _call_validity(events: Iterable[dict], unauthorized_ids: set[str]):
    valid = errors = unknown = 0
    for event in events:
        if event.get("role") != "assistant":
            continue
        code = event_error_code(event) if event.get("error") is True else None
        if event.get("error_origin") in {"corpus", "infrastructure"}:
            unknown += 1
        elif event.get("call_id") in unauthorized_ids or code in AGENT_ERRORS:
            errors += 1
        elif event.get("error") is False or (
            event.get("execution_order") is not None
            and event.get("execution_rejected") is False
            and code is not None
            and code.startswith("ERR_")
        ):
            valid += 1
        else:
            unknown += 1
    return valid, errors, unknown


def competition_evidence(session, *, solved):
    """Identify lawful progress and confirmed effects from the host trajectory."""
    task = session.task
    criteria = task.evaluation_criteria
    trace = evidence_from_messages(session.trajectory())
    initial_db = session._competition_initial_db
    policy = criteria.tool_access_policy
    if policy is not None:
        policy = resolve_initial_access_policy(policy, initial_db)
    unauthorized = {
        row["call_id"]
        for row in _tool_access_failures(
            trace.observed,
            criteria.actions or [],
            policy,
            initial_db=initial_db,
            allow_rejected=True,
        )
    }
    changed = {
        row["call_id"]
        for row in session.mcp_events
        if row.get("business_state_changed")
    }
    findings = _critical_mutations(criteria, trace.observed, changed, unauthorized)
    findings.extend(_disclosures(task, trace, initial_db))
    allowed = [
        item
        for item in equivalent_source_reads(
            criteria, trace.observed, session.env.tools.corpus
        )
        if _successful(item) and item.call.id not in unauthorized
    ]
    stages = _progress(criteria, allowed, policy, initial_db, solved)
    valid, errors, unknown = _call_validity(session.mcp_events, unauthorized)
    return {
        "version": "banking_competition_v1",
        "critical_violation": bool(findings),
        "critical_evidence": findings,
        "progress_stages": stages,
        "progress": sum(stages.values()) / len(stages),
        "valid_tool_calls": valid,
        "agent_tool_errors": errors,
        "unclassified_tool_calls": unknown,
        "tool_call_precision": valid / (valid + errors) if valid + errors else None,
    }
