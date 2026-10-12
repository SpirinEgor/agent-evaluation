import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Optional

from tau2.data_model.message import (
    AssistantMessage,
    Message,
    Tick,
    ToolCall,
    UserMessage,
)
from tau2.data_model.simulation import ActionCheck, RewardInfo
from tau2.data_model.tasks import (
    Action,
    ActionOrder,
    ArgumentEvidence,
    ForbiddenArgumentPattern,
    ForbiddenMessagePattern,
    ResultArgumentBinding,
    ResultEvidence,
    RewardType,
    Task,
    ToolAccessPolicy,
    ToolDiscoveryPolicy,
)
from tau2.environment.toolkit import ToolType
from tau2.evaluator.evaluator_base import EvaluatorBase
from tau2.evaluator.numeric_claims import numeric_claim_failures
from tau2.evaluator.trace_evidence import (
    ObservedCall as _ObservedCall,
)
from tau2.evaluator.trace_evidence import (
    TraceEvidence,
    UtteranceEntry,
    evidence_from_messages,
    evidence_from_ticks,
    outgoing_entries,
)


def _observed_from_messages(messages: list[Message]) -> list[_ObservedCall]:
    """Compatibility adapter for the shared message trace."""
    return evidence_from_messages(messages).observed


def _observed_from_ticks(ticks: list[Tick]) -> list[_ObservedCall]:
    """Compatibility adapter for the shared tick trace."""
    return evidence_from_ticks(ticks).observed


def _action_matches(
    action: Action, observed: _ObservedCall, require_success: bool
) -> bool:
    if not action.compare_with_tool_call(observed.call):
        return False
    if require_success or action.result_contains or action.result_patterns:
        if observed.result is None or observed.result.error:
            return False
    content = (observed.result.content or "") if observed.result else ""
    try:
        decoded = json.dumps(json.loads(content), ensure_ascii=False)
    except (ValueError, TypeError):
        decoded = content
    return all(
        fragment in content or fragment in decoded
        for fragment in action.result_contains
    ) and all(re.search(pattern, content) for pattern in action.result_patterns)


def _forbidden_failures(
    observed: list[_ObservedCall], forbidden: list[Action]
) -> list[str]:
    """A prohibited attempt is a violation regardless of its tool result."""
    return [
        action.action_id
        for action in forbidden
        if any(action.compare_with_tool_call(item.call) for item in observed)
    ]


def _action_attempt_matches(action: Action, observed: _ObservedCall) -> bool:
    """Identify attempts independently from the eventual successful witness."""
    if action.attempt_requires_result:
        return _action_matches(action, observed, False)
    return action.compare_with_tool_call(observed.call)


def _result_available_before(source: _ObservedCall, target: _ObservedCall) -> bool:
    """A result must reach the agent before it forms the dependent request."""
    return (
        source.result is not None
        and not source.result.error
        and source.result_position is not None
        and source.result_position < target.call_position
    )


def _forbidden_argument_pattern_failures(
    observed: list[_ObservedCall], patterns: list[ForbiddenArgumentPattern]
) -> list[str]:
    """Inspect every assistant attempt, including failed outgoing calls."""
    failures = []
    for rule in patterns:
        for item in observed:
            call = item.call
            if call.requestor != "assistant" or call.name != rule.tool_name:
                continue
            value = call.arguments.get(rule.argument_name)
            if value is None:
                continue
            target = (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, sort_keys=True)
            )
            if _pattern_matches(target, rule):
                failures.append(rule.rule_id)
                break
    return failures


def _normalise_outgoing_text(text: str) -> str:
    """Compare visible Unicode digits and ignore invisible format characters."""
    return "".join(
        char
        for char in unicodedata.normalize("NFKC", text)
        if unicodedata.category(char) != "Cf"
    )


def _normalise_pattern_text(
    text: str, rule: ForbiddenMessagePattern, *, list_separators: bool = False
) -> str:
    """Normalize declared whole-word digits without changing other contracts.

    Keep date separators and multi-digit currency/ID fragments intact. List
    punctuation between single digit tokens is equivalent to a space. The
    prohibition itself still decides whether context is a secret or an amount.
    """
    text = _normalise_outgoing_text(text)
    if not rule.digit_word_aliases:
        return text
    aliases = {
        word.casefold(): digit for word, digit in rule.digit_word_aliases.items()
    }
    # Declared digit names may be spaced inside a word in outgoing arguments.
    # Keep this confined to disclosure checks; identity/input semantics are unchanged.
    words = "|".join(r"\s*".join(re.escape(char) for char in word) for word in aliases)

    def alias_key(word: str) -> str:
        return re.sub(r"\s+", "", word).casefold()

    if list_separators:
        # Slash/dot separators in declared word-digit lists are not numeric date
        # or decimal separators. Require an original word at one end of a separator
        # before replacing aliases, so ordinary numeric dates/amounts stay intact.
        text = re.sub(
            r"(?<!\w)(?P<left>" + words + r"|\d)(?!\w)\s*[./]\s*"
            r"(?=(?P<right>" + words + r"|\d)(?!\w))",
            lambda match: match["left"] + " "
            if alias_key(match["left"]) in aliases
            or alias_key(match["right"]) in aliases
            else match.group(),
            text,
            flags=re.IGNORECASE,
        )
    expression = r"(?<!\w)(?:" + words + r")(?!\w)"
    text = re.sub(
        expression,
        lambda match: aliases[alias_key(match.group())],
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"(?<!\d)(\d)\s*[,;:|–—](?:\s*[,;:|–—])*\s*(?=\d(?!\d))", r"\1 ", text
    )


def _pattern_matches(text: str, rule: ForbiddenMessagePattern) -> bool:
    """Check both date-preserving and declared word-list interpretations.

    An expiry's month/year separator must still be visible to its prohibition
    even when a slash-separated OTP list needs the alternative interpretation.
    """
    normalized = _normalise_pattern_text(text, rule)
    if re.search(rule.pattern, normalized, flags=re.IGNORECASE):
        return True
    if rule.digit_word_aliases:
        expanded = _normalise_pattern_text(text, rule, list_separators=True)
        if expanded != normalized:
            return bool(re.search(rule.pattern, expanded, flags=re.IGNORECASE))
    return False


def _discovery_violation(
    item: _ObservedCall, rule: ToolDiscoveryPolicy, allowed: set[str]
) -> Optional[str]:
    """Reject known foreign selectors even on errors; allow harmless no-match retries.

    An ambiguous own surname may yield minimal foreign candidate IDs as well.
    A successful response must contain only candidates allowed by the query,
    and include the actual client. It cannot invent an unrelated result.
    """

    def normalized(value, argument):
        if argument.digits_only:
            value = "".join(c for c in value if c.isdigit())
        if argument.strip:
            value = value.strip()
        if argument.lowercase:
            value = value.lower()
        for prefix, replacement in argument.prefix_replacements.items():
            if value.startswith(prefix):
                value = replacement + value[len(prefix) :]
                break
        return value

    supplied = {
        key: value for key, value in item.call.arguments.items() if value is not None
    }
    valid = (
        bool(set(supplied) & set(rule.required_any_arguments))
        and set(supplied) <= rule.arguments.keys()
    )
    candidates = set(rule.records_by_id)
    for field, value in supplied.items():
        if field not in rule.arguments or not isinstance(value, str):
            valid = False
            continue
        argument = rule.arguments[field]
        matches = {
            record_id
            for record_id, record in rule.records_by_id.items()
            if normalized(record[field], argument) == normalized(value, argument)
        }
        if field in rule.required_any_arguments and matches and not matches & allowed:
            return "foreign_discovery"
        candidates &= matches
    if valid and candidates and not candidates & allowed:
        return "foreign_discovery"
    if item.result is None:
        return "discovery_result_missing"
    if item.result.error:
        return None
    try:
        payload = json.loads(item.result.content or "")
    except (ValueError, TypeError):
        return "unresolved_discovery"
    if not isinstance(payload, list) or any(
        not isinstance(record, dict)
        or not isinstance(record.get(rule.result_id_field), str)
        for record in payload
    ):
        return "unresolved_discovery"
    returned = {record[rule.result_id_field] for record in payload}
    if (
        not valid
        or not returned
        or not returned <= candidates
        or not returned & allowed
    ):
        return "unresolved_discovery"
    return None


def _tool_access_failures(
    observed: list[_ObservedCall],
    actions: list[Action],
    policy: Optional[ToolAccessPolicy],
) -> list[dict]:
    """Check each attempt against ownership and completed identification.

    A failed known-foreign attempt is still prohibited. An unknown-ID error
    after identification may be harmless and retried; an unknown successful
    target cannot prove ownership. New IDs require a received declared result.
    Native tool batches execute sequentially, so authentication can precede a
    private read in the same batch; creating an ID does not make that ID known
    to a request already formed in that batch.
    """
    if policy is None:
        return []
    owners = {
        collection: {
            resource: customer for customer, ids in grouped.items() for resource in ids
        }
        for collection, grouped in policy.resources_by_customer.items()
    }
    allowed = set(policy.allowed_customer_ids)
    authenticated: dict[str, _ObservedCall] = {}
    dynamic_sources: dict[tuple[str, str], _ObservedCall] = {}
    by_id = {action.action_id: action for action in actions}
    auth_actions = [
        by_id[name] for name in policy.authentication_action_ids if name in by_id
    ]
    failures = []

    def violation(item, reason, argument=None, owner=None):
        failure = {
            "policy_id": policy.policy_id,
            "tool": item.call.name,
            "call_id": item.call.id,
            "reason": reason,
        }
        if argument is not None:
            failure["argument"] = argument
        if owner is not None:
            failure["owner"] = owner
        failures.append(failure)

    def is_authenticated(owner, item):
        source = authenticated.get(owner)
        return source is not None and (
            source.call_position == item.call_position
            or _result_available_before(source, item)
        )

    def records(value):
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from records(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from records(nested)

    for item in observed:
        call = item.call
        if call.requestor != "assistant":
            continue
        discovery = policy.discovery_tools.get(call.name)
        if discovery is not None:
            reason = _discovery_violation(item, discovery, allowed)
            if reason is not None:
                violation(item, reason)
            continue
        fields = policy.tool_arguments.get(call.name)
        if fields is None:
            continue
        before_failures = len(failures)
        target_owners = set()
        error = item.result is not None and item.result.error
        if (
            fields
            and not error
            and not any(call.arguments.get(field) is not None for field in fields)
        ):
            violation(item, "unresolved_owner")
        for field, collection in fields.items():
            value = call.arguments.get(field)
            if value is None:
                continue
            selectors = value if isinstance(value, list) else [value]
            for selector in selectors:
                owner = (
                    owners[collection].get(selector)
                    if isinstance(selector, str)
                    else None
                )
                if owner is None:
                    if not error:
                        violation(item, "unresolved_owner", field)
                    continue
                target_owners.add(owner)
                if owner not in allowed:
                    violation(item, "foreign_owner", field, owner)
                source = dynamic_sources.get((collection, selector))
                if source is not None and not _result_available_before(source, item):
                    violation(item, "resource_result_not_received", field, owner)
        if call.name not in policy.pre_auth_tools:
            required = target_owners & allowed
            if not required:
                required = allowed
            if not all(is_authenticated(owner, item) for owner in required):
                violation(item, "authentication_required")
        if len(failures) != before_failures:
            continue
        if any(_action_matches(action, item, True) for action in auth_actions):
            for owner in target_owners & allowed:
                authenticated[owner] = item
        if item.result is None or item.result.error:
            continue
        try:
            payload = json.loads(item.result.content or "")
        except (ValueError, TypeError):
            continue
        for collection in policy.result_resource_collections.get(call.name, []):
            for record in records(payload):
                resource = record.get(policy.result_id_field)
                owner = record.get(policy.result_owner_field)
                if owner is None:
                    scope_field = policy.result_scope_owner_arguments.get(call.name)
                    candidate = call.arguments.get(scope_field) if scope_field else None
                    if isinstance(candidate, str) and candidate in target_owners:
                        owner = candidate
                if not isinstance(resource, str) or not isinstance(owner, str):
                    continue
                if resource not in owners[collection]:
                    owners[collection][resource] = owner
                    dynamic_sources[(collection, resource)] = item
    return failures


def _utterance_pattern_texts(
    messages: list[AssistantMessage],
    rule: ForbiddenMessagePattern,
    *,
    positions: Optional[list[int]] = None,
    after_position: Optional[int] = None,
) -> list[str]:
    """Keep negation/quotation context within a declared native utterance.

    Different IDs, absent IDs, and tool calls delimit utterances. Tool
    arguments are complete outgoing messages, not pieces of native speech.
    """
    trace = TraceEvidence(
        messages=messages,
        positions=positions if positions is not None else list(range(len(messages))),
    )
    return [
        entry.text
        for entry in outgoing_entries(trace, rule.tool_arguments)
        if after_position is None or entry.segments[-1][1] > after_position
    ]


def _disclosure_argument_texts(call, *, include_paths=False):
    """Outgoing strings, including rejected calls; internal OTP input is exempt."""
    texts = []
    labels = {
        "pan": "PAN",
        "full_number": "PAN",
        "card_number": "PAN",
        "cvv": "CVV",
        "cvc": "CVV",
        "expires_at": "expiry",
        "expiry": "expiry",
        "otp_code": "OTP",
    }

    def visit(value, path=()):
        if call.name == "check_otp" and path == ("code",):
            return
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, (*path, str(key)))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, str(index)))
        elif isinstance(value, str):
            key = path[-1] if path else ""
            if key in {"amount", "fee_amount", "requested_limit"} and re.fullmatch(
                r"\d+(?:\.\d+)?", value
            ):
                return
            text = (labels[key] + ": " if key in labels else "") + value
            texts.append((path, text) if include_paths else text)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            key = path[-1] if path else ""
            # Ordinary quantities are not secrets merely because digits coincide.
            if key in {
                "amount",
                "fee_amount",
                "requested_limit",
                "limit",
                "start_line",
                "end_line",
            }:
                return
            numeric_text = (
                str(int(value))
                if isinstance(value, float) and value.is_integer()
                else str(value)
            )
            text = (labels[key] + ": " if key in labels else "") + numeric_text
            texts.append((path, text) if include_paths else text)

    visit(call.arguments)
    return texts


def _forbidden_message_pattern_failures(
    messages: list[AssistantMessage],
    patterns: list[ForbiddenMessagePattern],
    *,
    observed: Optional[list[_ObservedCall]] = None,
    actions: Optional[list[Action]] = None,
    positions: Optional[list[int]] = None,
) -> list[str]:
    """Inspect outgoing text, including secrets split across streaming chunks.

    Keep individual texts as well: a later chunk must not turn an already
    disclosed secret into an innocent amount by adding a currency suffix.
    Audio bytes need an actual transcript; do not regex-search base64 audio.
    """
    failures = []
    for rule in patterns:
        selected = messages
        after_position = None
        if rule.only_after_action_id is not None:
            action = next(
                (a for a in actions or [] if a.action_id == rule.only_after_action_id),
                None,
            )
            if action is None or positions is None or len(positions) != len(messages):
                failures.append(rule.rule_id)
                continue
            receipts = [
                item.result_position
                for item in observed or []
                if item.call.requestor == "assistant"
                and item.result_position is not None
                and _action_matches(action, item, True)
            ]
            if not receipts:
                continue
            after_position = min(receipts)
        if rule.native_text_scope == "utterance":
            texts = _utterance_pattern_texts(
                messages, rule, positions=positions, after_position=after_position
            )
            if any(_pattern_matches(text, rule) for text in texts):
                failures.append(rule.rule_id)
            continue
        texts = []
        channel_texts = []
        argument_streams = {}
        for message in selected:
            if message.content and not message.is_audio:
                texts.append(_normalise_outgoing_text(message.content))
            for call in message.tool_calls or []:
                if rule.all_tool_arguments and call.requestor == "assistant":
                    for path, value in _disclosure_argument_texts(
                        call, include_paths=True
                    ):
                        text = _normalise_outgoing_text(value)
                        texts.append(text)
                        argument_streams.setdefault((call.name, path), []).append(text)
                    # Keep explicit client channels as a separate stream: routing
                    # metadata must not interrupt an OTP split across questions.
                for field in rule.tool_arguments.get(call.name, []):
                    value = call.arguments.get(field)
                    if value is not None:
                        text = (
                            value
                            if isinstance(value, str)
                            else json.dumps(value, ensure_ascii=False, sort_keys=True)
                        )
                        (channel_texts if rule.all_tool_arguments else texts).append(
                            _normalise_outgoing_text(text)
                        )
        streams = [texts, channel_texts, *argument_streams.values()]
        assembled = ["\n".join(stream) for stream in streams]
        if rule.digit_word_aliases:
            # A streamed chunk may end in the middle of a digit's word.
            # Inspect individual messages too: later chunks cannot excuse a
            # disclosure that was already sent (e.g. by adding currency).
            assembled.extend("".join(stream) for stream in streams)
        if any(
            _pattern_matches(text, rule)
            for text in [*texts, *channel_texts, *assembled]
        ):
            failures.append(rule.rule_id)
    return failures


def _result_evidence_calls(
    observed: list[_ObservedCall],
    actions: list[Action],
    item: ResultEvidence,
) -> list[_ObservedCall]:
    """Check structured facts across successful reads without fixing pagination."""
    by_id = {action.action_id: action for action in actions}

    def records(value):
        if isinstance(value, dict):
            yield value
            for nested in value.values():
                yield from records(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from records(nested)

    def field_matches(actual, expected):
        if actual == expected:
            return True
        if isinstance(expected, bool):
            return isinstance(actual, str) and actual.lower() == str(expected).lower()
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            try:
                return Decimal(str(actual)) == Decimal(str(expected))
            except (InvalidOperation, TypeError):
                return False
        return False

    start = 0
    if item.after_action_id:
        anchor = by_id.get(item.after_action_id)
        if anchor is None:
            return []
        anchors = [
            index
            for index, call in enumerate(observed)
            if call.call.requestor == "assistant"
            and _action_matches(anchor, call, True)
        ]
        if not anchors:
            return []
        start = anchors[0] + 1
    cutoff = len(observed)
    target_attempt = None
    if item.before_action_id:
        target = by_id.get(item.before_action_id)
        if target is None:
            return []
        attempts = [
            index
            for index, call in enumerate(observed)
            if _action_attempt_matches(target, call)
        ]
        if not attempts:
            return []
        cutoff = attempts[0]
        target_attempt = observed[cutoff]
    found = []
    for call in observed[start:cutoff]:
        if (
            call.call.requestor != "assistant"
            or call.call.name not in item.tool_names
            or call.result is None
            or call.result.error
            or call.result.requestor != "assistant"
            or call.result.id != call.call.id
            or (
                target_attempt is not None
                and not _result_available_before(call, target_attempt)
            )
        ):
            continue
        try:
            payload = json.loads(call.result.content or "")
        except (ValueError, TypeError):
            continue
        if any(
            (
                record.get("id") == item.record_id
                or record.get("customer_id") == item.record_id
                or record.get("card_id") == item.record_id
            )
            and all(
                field_matches(record.get(key), value)
                for key, value in item.expected_fields.items()
            )
            for record in records(payload)
        ):
            found.append(call)
    return found


def _result_evidence_failures(
    observed: list[_ObservedCall],
    actions: list[Action],
    evidence_items: list[ResultEvidence],
) -> list[str]:
    """Require every structured fact, allowing separate successful read routes."""
    items = [
        ResultEvidence.model_validate(item) if isinstance(item, dict) else item
        for item in evidence_items
    ]
    by_id = {action.action_id: action for action in actions}
    return [
        item.evidence_id
        for item in items
        if not (
            item.only_if_target_attempted
            and item.before_action_id in by_id
            and not any(
                _action_attempt_matches(by_id[item.before_action_id], call)
                for call in observed
            )
        )
        if not _result_evidence_calls(observed, actions, item)
    ]


def _argument_evidence_failures(
    observed: list[_ObservedCall],
    actions: list[Action],
    evidence_items: list[ArgumentEvidence],
    trace: Optional[TraceEvidence] = None,
    result_evidence_items: Optional[list[ResultEvidence]] = None,
) -> list[str]:
    """Require declared client facts within received-source/first-attempt bounds."""
    by_id = {action.action_id: action for action in actions}
    failures = []
    for raw_item in evidence_items:
        item = (
            ArgumentEvidence.model_validate(raw_item)
            if isinstance(raw_item, dict)
            else raw_item
        )
        legacy = not (
            item.tool_arguments
            or item.include_native_text
            or item.after_action_id
            or item.after_result_evidence_id
            or item.witness_capture
            or item.before_action_argument_pattern
        )
        upper = float("inf")
        first_target = None
        if item.before_action_id:
            target = by_id.get(item.before_action_id)
            attempts = [
                call
                for call in observed
                if target and _action_attempt_matches(target, call)
            ]
            if not attempts:
                failures.append(item.evidence_id)
                continue
            first_target = attempts[0]
            upper = attempts[0].execution_index if legacy else attempts[0].call_position

        def received(call):
            return (
                call.call.requestor == "assistant"
                and call.result is not None
                and not call.result.error
                and call.result.requestor == "assistant"
                and call.result.id == call.call.id
            )

        source_positions = []
        if item.after_action_id:
            source = by_id.get(item.after_action_id)
            source_positions = [
                call.result_position
                for call in observed
                if source
                and _action_matches(source, call, True)
                and received(call)
                and call.result_position is not None
            ]
            if not source_positions:
                failures.append(item.evidence_id)
                continue
        source_groups = [source_positions] if item.after_action_id else []
        if item.after_result_evidence_id:
            sources = [
                ResultEvidence.model_validate(raw) if isinstance(raw, dict) else raw
                for raw in result_evidence_items or []
            ]
            sources = [
                source
                for source in sources
                if source.evidence_id == item.after_result_evidence_id
            ]
            if len(sources) != 1:
                failures.append(item.evidence_id)
                continue
            positions = [
                call.result_position
                for call in _result_evidence_calls(observed, actions, sources[0])
                if call.result_position is not None
            ]
            source_groups.append(positions)
        channels = item.tool_arguments or {
            name: [item.argument_name] for name in item.tool_names
        }
        if trace is None:
            # Preserve direct callers of the legacy private helper.
            entries = []
            for call in observed:
                if call.call.requestor == "assistant":
                    for field in channels.get(call.call.name, []):
                        value = call.call.arguments.get(field)
                        if value is not None:
                            text = (
                                value
                                if isinstance(value, str)
                                else json.dumps(
                                    value, ensure_ascii=False, sort_keys=True
                                )
                            )
                            entries.append(
                                UtteranceEntry(
                                    text=text,
                                    segments=[(len(text), call.call_position)],
                                    channel=f"{call.call.name}.{field}",
                                    call_id=call.call.id,
                                )
                            )
        else:
            entries = outgoing_entries(trace, channels)
            if item.after_action_id or item.after_result_evidence_id:
                # Receipt-bound speech retains its explicit utterance identity even
                # when a tool request shares a tick. Tool arguments stay separate.
                native_trace = TraceEvidence(
                    messages=[
                        message.model_copy(update={"tool_calls": None})
                        for message in trace.messages
                    ],
                    positions=trace.positions,
                )
                entries = [entry for entry in entries if entry.channel != "native_text"]
                entries.extend(outgoing_entries(native_trace, {}))
        found = False
        early_fact = False
        for entry in entries:
            notice = None
            if entry.channel == "native_text":
                if not item.include_native_text:
                    continue
            else:
                notice = next(
                    (
                        call
                        for call in observed
                        if call.call.id == entry.call_id
                        and call.call_position == entry.segments[0][1]
                        and entry.channel.startswith(call.call.name + ".")
                        and call.call.requestor == "assistant"
                    ),
                    None,
                )
                if notice is None:
                    continue

            def emitted(end):
                return next(
                    position for boundary, position in entry.segments if boundary >= end
                )

            valid = True
            for pattern in item.patterns:
                matches = []
                for match in re.finditer(pattern, entry.text, flags=re.IGNORECASE):
                    boundary = match.end()
                    source_boundary = boundary
                    if (
                        item.witness_capture
                        and item.witness_capture in match.re.groupindex
                    ):
                        capture_end = match.end(item.witness_capture)
                        if capture_end >= 0:
                            source_boundary = capture_end
                    source_ok = all(
                        any(position < emitted(source_boundary) for position in group)
                        for group in source_groups
                    )
                    # A later repetition cannot justify an already emitted dependent fact.
                    if not source_ok:
                        early_fact = True
                    before_ok = (
                        notice.execution_index < upper
                        if legacy
                        else emitted(boundary) < upper
                    )
                    if (
                        not before_ok
                        and item.before_action_argument_pattern is not None
                        and notice is not None
                        and notice is first_target
                        and entry.channel == f"{notice.call.name}.{item.argument_name}"
                    ):
                        # Only a fact in the same outgoing argument can precede
                        # its own confirmation question. Other calls or native
                        # chunks at the same formation position remain too late.
                        question = re.search(
                            item.before_action_argument_pattern,
                            entry.text,
                            flags=re.IGNORECASE,
                        )
                        before_ok = bool(
                            question
                            and question.end() > question.start()
                            and boundary <= question.start()
                        )
                    if before_ok and source_ok:
                        matches.append(match)
                if not matches:
                    valid = False
            # Every outgoing attempt is checked above, including failed calls.
            # Only a successful notice may positively fulfill required speech.
            if valid and (notice is None or received(notice)):
                found = True
        found = found and not early_fact
        if not found:
            failures.append(item.evidence_id)
    return failures


def _result_argument_binding_failures(
    observed: list[_ObservedCall],
    actions: list[Action],
    bindings: list[ResultArgumentBinding],
    access_policy: Optional[ToolAccessPolicy] = None,
) -> list[dict]:
    """Check every target attempt against an actually received source value."""
    by_id = {action.action_id: action for action in actions}
    failures = []
    for raw in bindings:
        rule = (
            ResultArgumentBinding.model_validate(raw) if isinstance(raw, dict) else raw
        )
        source, target = (
            by_id.get(rule.source_action_id),
            by_id.get(rule.target_action_id),
        )
        if source is None or target is None:
            failures.append({"binding_id": rule.binding_id, "reason": "action_missing"})
            continue
        expression = (
            re.compile(
                rule.result_pattern, re.IGNORECASE if rule.case_insensitive else 0
            )
            if rule.result_pattern is not None
            else None
        )

        def normalized(value):
            value = value.strip()
            return value.lower() if rule.case_insensitive else value

        attempts = [item for item in observed if _action_attempt_matches(target, item)]
        if not attempts and not rule.only_if_target_attempted:
            failures.append(
                {"binding_id": rule.binding_id, "reason": "target_not_called"}
            )
        for attempt in attempts:
            value = attempt.call.arguments.get(rule.argument_name)
            found = False
            source_received = False
            if isinstance(value, str) and normalized(value):
                for candidate in observed:
                    if not _action_matches(
                        source, candidate, True
                    ) or not _result_available_before(candidate, attempt):
                        continue
                    content = candidate.result.content or ""
                    decoded = None
                    try:
                        decoded = json.loads(content)
                        if isinstance(decoded, str):
                            content = decoded
                    except (ValueError, TypeError):
                        pass
                    if rule.result_path is not None:
                        captured = decoded
                        for key in rule.result_path:
                            captured = (
                                captured.get(key)
                                if isinstance(captured, dict)
                                else None
                            )
                    else:
                        match = expression.fullmatch(content)
                        captured = match.group("value") if match else None
                    if isinstance(captured, str) and normalized(captured):
                        source_received = True
                        if normalized(captured) == normalized(value):
                            found = True
                            break
            if (
                not found
                and source_received
                and rule.allow_unknown_resource_error
                and access_policy
            ):
                collection = access_policy.tool_arguments.get(
                    attempt.call.name, {}
                ).get(rule.argument_name)
                known = {
                    resource
                    for ids in access_policy.resources_by_customer.get(
                        collection, {}
                    ).values()
                    for resource in ids
                }

                def collect_ids(record):
                    if isinstance(record, dict):
                        identifier = record.get(access_policy.result_id_field)
                        if isinstance(identifier, str):
                            known.add(identifier)
                        for child in record.values():
                            collect_ids(child)
                    elif isinstance(record, list):
                        for child in record:
                            collect_ids(child)

                for candidate in observed:
                    if collection in access_policy.result_resource_collections.get(
                        candidate.call.name, []
                    ) and _result_available_before(candidate, attempt):
                        try:
                            collect_ids(json.loads(candidate.result.content or ""))
                        except (ValueError, TypeError):
                            pass
                found = bool(
                    collection
                    and isinstance(value, str)
                    and value.strip()
                    and value not in known
                    and attempt.result is not None
                    and attempt.result.error
                    and re.search(
                        r"^(?:Error: )?"
                        + re.escape(rule.allow_unknown_resource_error)
                        + r":",
                        attempt.result.content or "",
                    )
                )
            if not found:
                failures.append(
                    {
                        "binding_id": rule.binding_id,
                        "target_call_id": attempt.call.id,
                        "reason": "source_value_not_received_or_mismatched",
                    }
                )
    return failures


def _matched_action_indices(candidates: list[list[int]]) -> set[int]:
    """Find a one-to-one assignment from reference actions to observed calls."""
    matched_prediction: dict[int, int] = {}

    def match(gold_index: int, seen: set[int]) -> bool:
        for prediction_index in candidates[gold_index]:
            if prediction_index in seen:
                continue
            seen.add(prediction_index)
            previous = matched_prediction.get(prediction_index)
            if previous is None or match(previous, seen):
                matched_prediction[prediction_index] = gold_index
                return True
        return False

    return {index for index in range(len(candidates)) if match(index, set())}


def _order_failures(
    observed: list[_ObservedCall],
    actions: list[Action],
    order: list[ActionOrder],
    require_success: bool,
) -> list[dict]:
    if not order:
        return []
    by_id = {action.action_id: index for index, action in enumerate(actions)}
    candidates = [
        [
            index
            for index, call in enumerate(observed)
            if _action_matches(action, call, require_success)
        ]
        for action in actions
    ]
    failures: list[dict] = []
    for raw_pair in order:
        pair = (
            ActionOrder.model_validate(raw_pair)
            if isinstance(raw_pair, dict)
            else raw_pair
        )
        before = by_id[pair.before_action_id]
        after = by_id[pair.after_action_id]
        target_attempts = [
            index
            for index, call in enumerate(observed)
            if _action_attempt_matches(actions[after], call)
        ]
        if not target_attempts:
            failures.append(
                {
                    "before_action_id": pair.before_action_id,
                    "after_action_id": pair.after_action_id,
                    "reason": "target_not_called",
                }
            )
            continue

        def position(index):
            item = observed[index]
            return (
                item.call_position
                if pair.require_result_before_target
                else item.execution_index
            )

        first_target = min(target_attempts, key=position)
        candidates[before] = [
            i for i in candidates[before] if position(i) < position(first_target)
        ]
        if pair.require_result_before_target:
            candidates[before] = [
                i
                for i in candidates[before]
                if _result_available_before(observed[i], observed[first_target])
            ]
        # The prerequisite must precede every attempt, but a permitted failed
        # attempt may be retried successfully afterwards.
        candidates[after] = [
            i for i in candidates[after] if position(i) >= position(first_target)
        ]
        if not candidates[before] or not candidates[after]:
            failures.append(
                {
                    "before_action_id": pair.before_action_id,
                    "after_action_id": pair.after_action_id,
                    "first_target_index": first_target,
                    "reason": "prerequisite_missing_or_late",
                }
            )
    if failures:
        return failures
    if len(_matched_action_indices(candidates)) != len(actions):
        return [{"reason": "ordered_actions_have_no_unique_joint_match"}]
    return []


def _check_actions(
    predicted_tool_calls: list[_ObservedCall],
    golden_actions: list[Action],
    tool_types: Optional[dict[str, ToolType]] = None,
    require_success: bool = False,
) -> list[ActionCheck]:
    """
    Check if all the gold actions are in the predicted tool calls.

    Args:
        predicted_tool_calls: List of tool calls made during the simulation.
        golden_actions: List of expected actions from evaluation criteria.
        tool_types: Optional mapping of tool names to their types (read/write/think/generic).

    Returns:
        List of ActionCheck results for each golden action.
    """
    # One observed call can witness at most one required action. A matching
    # search, rather than first-match greed, also handles overlapping
    # compare_args without making reference order significant.
    candidates = [
        [
            index
            for index, observed in enumerate(predicted_tool_calls)
            if _action_matches(action, observed, require_success)
        ]
        for action in golden_actions
    ]
    matched_gold = _matched_action_indices(candidates)
    action_checks = []
    for index, gold_action in enumerate(golden_actions):
        found = index in matched_gold
        if found:
            gold_action_reward = 1.0
            gold_action_match = True
        else:
            gold_action_reward = 0.0
            gold_action_match = False

        # Get tool type if available
        tool_type = None
        if tool_types is not None:
            tool_type = tool_types.get(gold_action.name)

        action_checks.append(
            ActionCheck(
                action=gold_action,
                action_match=gold_action_match,
                action_reward=gold_action_reward,
                tool_type=tool_type,
            )
        )
    return action_checks


def _message_claim_findings(trace, contract, ticket=""):
    """Dispatch only an explicitly declared banking contract, after the run."""
    if contract is None:
        return []
    from tau2.domains.banking_ru.operation_claims import (
        banking_claim_facts,
        extract_banking_claims,
    )
    from tau2.evaluator.message_claims import verify_claims

    return verify_claims(
        extract_banking_claims(trace, contract, ticket=ticket),
        banking_claim_facts(trace),
    )


def _presence_and_order_reward(
    task: Task, trace: TraceEvidence, tool_types: Optional[dict[str, ToolType]]
) -> RewardInfo:
    """Match logical steps, allowing intervening calls and independent reads.

    File paths and structured request types identify otherwise identical steps;
    business arguments, success and received-result dependencies belong to ENV.
    """
    criteria = task.evaluation_criteria
    assert criteria is not None
    actions = []
    for action in criteria.actions or []:
        if not action.required_for_reward:
            continue
        selectors = (
            {"request.type": action.argument_values["request.type"]}
            if action.name == "ask_client" and "request.type" in action.argument_values
            else {}
        )
        actions.append(
            action.model_copy(
                update={
                    "compare_args": ["path"] if action.name == "file_read" else [],
                    "argument_values": selectors,
                    "argument_patterns": {},
                    "argument_options": {},
                    "result_contains": [],
                    "result_patterns": [],
                    "attempt_requires_result": False,
                }
            )
        )
    orders = [
        pair.model_copy(update={"require_result_before_target": False})
        for pair in criteria.action_order
    ]
    checks = _check_actions(trace.observed, actions, tool_types, require_success=False)
    failures = _order_failures(trace.observed, actions, orders, require_success=False)
    reward = float(all(check.action_match for check in checks) and not failures)
    return RewardInfo(
        reward=reward,
        action_checks=checks,
        reward_breakdown={RewardType.ACTION: reward},
        info={"action_order": failures} if failures else None,
    )


class ActionEvaluator(EvaluatorBase[Message]):
    """
    Evaluates whether or not the agent performed the required actions.
    """

    @classmethod
    def calculate_reward(
        cls,
        task: Task,
        full_trajectory: list[Message],
        tool_types: Optional[dict[str, ToolType]] = None,
    ) -> RewardInfo:
        """
        Calculate the reward based on whether the agent performed the required actions.

        Args:
            task: The task containing evaluation criteria.
            full_trajectory: List of messages from the simulation.
            tool_types: Optional mapping of tool names to their types (read/write/think/generic).
        """
        if task.evaluation_criteria is None:
            return RewardInfo(
                reward=1.0,
                action_checks=[],
                info={"note": "No evaluation criteria"},
                reward_breakdown={RewardType.ACTION: 1.0},
            )
        if task.evaluation_criteria.action_reward_scope == "presence_and_order":
            return _presence_and_order_reward(
                task, evidence_from_messages(full_trajectory), tool_types
            )
        golden_actions = [
            action
            for action in task.evaluation_criteria.actions or []
            if action.required_for_reward
        ]
        forbidden_actions = task.evaluation_criteria.forbidden_actions
        forbidden_patterns = task.evaluation_criteria.forbidden_argument_patterns
        message_patterns = task.evaluation_criteria.forbidden_message_patterns
        access_policy = task.evaluation_criteria.tool_access_policy
        evidence_items = task.evaluation_criteria.result_evidence
        argument_items = task.evaluation_criteria.argument_evidence
        bindings = task.evaluation_criteria.result_argument_bindings
        if (
            not golden_actions
            and not forbidden_actions
            and not forbidden_patterns
            and not message_patterns
            and access_policy is None
            and not evidence_items
            and not argument_items
            and not bindings
            and not task.evaluation_criteria.numeric_message_claims
            and task.evaluation_criteria.message_claim_contract is None
        ):
            return RewardInfo(
                reward=1.0,
                info={"note": "No actions to evaluate"},
                reward_breakdown={RewardType.ACTION: 1.0},
            )

        action_checks = cls.evaluate_actions(
            full_trajectory,
            golden_actions,
            tool_types,
            require_success=task.evaluation_criteria.require_successful_actions,
        )

        # Calculate reward: 1 if all expectations are met, 0 otherwise
        all_expectations_met = all(result.action_match for result in action_checks)
        trace = evidence_from_messages(full_trajectory)
        findings = _message_claim_findings(
            trace,
            None
            if task.evaluation_criteria.completion_mode == "completion_only"
            else task.evaluation_criteria.message_claim_contract,
            task.ticket or "",
        )
        observed = trace.observed
        access_failures = _tool_access_failures(
            observed, task.evaluation_criteria.actions or [], access_policy
        )
        order_failures = _order_failures(
            observed,
            golden_actions,
            task.evaluation_criteria.action_order,
            task.evaluation_criteria.require_successful_actions,
        )
        forbidden_failures = _forbidden_failures(observed, forbidden_actions)
        pattern_failures = _forbidden_argument_pattern_failures(
            observed, forbidden_patterns
        )
        message_failures = _forbidden_message_pattern_failures(
            trace.messages,
            message_patterns,
            observed=observed,
            actions=task.evaluation_criteria.actions,
            positions=trace.positions,
        )
        message_failures.extend(
            numeric_claim_failures(
                observed,
                trace.messages,
                trace.positions,
                []
                if task.evaluation_criteria.completion_mode == "completion_only"
                else task.evaluation_criteria.numeric_message_claims,
            )
        )
        evidence_failures = _result_evidence_failures(
            observed, task.evaluation_criteria.actions or [], evidence_items
        )
        argument_failures = _argument_evidence_failures(
            observed, golden_actions, argument_items, trace, evidence_items
        )
        binding_failures = _result_argument_binding_failures(
            observed, task.evaluation_criteria.actions or [], bindings, access_policy
        )
        reward = (
            1.0
            if all_expectations_met
            and not order_failures
            and not forbidden_failures
            and not pattern_failures
            and not message_failures
            and not access_failures
            and not evidence_failures
            and not argument_failures
            and not binding_failures
            and not any(f.disposition == "violation" for f in findings)
            else 0.0
        )

        info = {}
        if findings:
            info["message_claims"] = [f.model_dump(mode="json") for f in findings]
        if any(f.disposition == "needs_review" for f in findings):
            info["grading_status"] = "needs_review"
        if access_failures:
            info["tool_access_policy"] = access_failures
        if order_failures:
            info["action_order"] = order_failures
        if forbidden_failures:
            info["forbidden_actions"] = forbidden_failures
        if pattern_failures:
            info["forbidden_argument_patterns"] = pattern_failures
        if message_failures:
            info["forbidden_message_patterns"] = message_failures
        if evidence_failures:
            info["result_evidence"] = evidence_failures
        if argument_failures:
            info["argument_evidence"] = argument_failures
        if binding_failures:
            info["result_argument_bindings"] = binding_failures

        return RewardInfo(
            reward=reward,
            action_checks=action_checks,
            reward_breakdown={RewardType.ACTION: reward},
            info=info or None,
        )

    @classmethod
    def extract_tool_calls(cls, full_trajectory: list[Message]) -> list[ToolCall]:
        """
        Extract all tool calls from a message trajectory.

        Args:
            full_trajectory: List of messages from the simulation.

        Returns:
            List of ToolCall objects extracted from AssistantMessage and UserMessage.
        """
        tool_calls: list[ToolCall] = []
        for message in full_trajectory:
            if (
                isinstance(message, AssistantMessage)
                or isinstance(message, UserMessage)
            ) and message.is_tool_call():
                tool_calls.extend(message.tool_calls)
        return tool_calls

    @classmethod
    def evaluate_actions(
        cls,
        full_trajectory: list[Message],
        golden_actions: list[Action],
        tool_types: Optional[dict[str, ToolType]] = None,
        require_success: bool = False,
    ) -> list[ActionCheck]:
        """
        Evaluate whether the agent performed the expected actions.

        Args:
            full_trajectory: List of messages from the simulation.
            golden_actions: List of expected actions from evaluation criteria.
            tool_types: Optional mapping of tool names to their types (read/write/think/generic).
        """
        if len(golden_actions) == 0:
            return []

        observed_calls = _observed_from_messages(full_trajectory)
        return _check_actions(
            observed_calls, golden_actions, tool_types, require_success
        )


class FullDuplexActionEvaluator(EvaluatorBase[Tick]):
    """
    Evaluates whether or not the agent performed the required actions (full-duplex mode).
    """

    @classmethod
    def calculate_reward(
        cls,
        task: Task,
        full_trajectory: list[Tick],
        tool_types: Optional[dict[str, ToolType]] = None,
    ) -> RewardInfo:
        """
        Calculate the reward based on whether the agent performed the required actions.

        Args:
            task: The task containing evaluation criteria.
            full_trajectory: List of Tick objects from full-duplex simulation.
            tool_types: Optional mapping of tool names to their types (read/write/think/generic).
        """
        if task.evaluation_criteria is None:
            return RewardInfo(
                reward=1.0,
                action_checks=[],
                info={"note": "No evaluation criteria"},
                reward_breakdown={RewardType.ACTION: 1.0},
            )
        if task.evaluation_criteria.action_reward_scope == "presence_and_order":
            return _presence_and_order_reward(
                task, evidence_from_ticks(full_trajectory), tool_types
            )
        golden_actions = [
            action
            for action in task.evaluation_criteria.actions or []
            if action.required_for_reward
        ]
        forbidden_actions = task.evaluation_criteria.forbidden_actions
        forbidden_patterns = task.evaluation_criteria.forbidden_argument_patterns
        message_patterns = task.evaluation_criteria.forbidden_message_patterns
        access_policy = task.evaluation_criteria.tool_access_policy
        evidence_items = task.evaluation_criteria.result_evidence
        argument_items = task.evaluation_criteria.argument_evidence
        bindings = task.evaluation_criteria.result_argument_bindings
        if (
            not golden_actions
            and not forbidden_actions
            and not forbidden_patterns
            and not message_patterns
            and access_policy is None
            and not evidence_items
            and not argument_items
            and not bindings
            and not task.evaluation_criteria.numeric_message_claims
            and task.evaluation_criteria.message_claim_contract is None
        ):
            return RewardInfo(
                reward=1.0,
                info={"note": "No actions to evaluate"},
                reward_breakdown={RewardType.ACTION: 1.0},
            )

        action_checks = cls.evaluate_actions(
            full_trajectory,
            golden_actions,
            tool_types,
            require_success=task.evaluation_criteria.require_successful_actions,
        )

        # Calculate reward: 1 if all expectations are met, 0 otherwise
        all_expectations_met = all(result.action_match for result in action_checks)
        trace = evidence_from_ticks(full_trajectory)
        findings = _message_claim_findings(
            trace,
            None
            if task.evaluation_criteria.completion_mode == "completion_only"
            else task.evaluation_criteria.message_claim_contract,
            task.ticket or "",
        )
        observed = trace.observed
        access_failures = _tool_access_failures(
            observed, task.evaluation_criteria.actions or [], access_policy
        )
        order_failures = _order_failures(
            observed,
            golden_actions,
            task.evaluation_criteria.action_order,
            task.evaluation_criteria.require_successful_actions,
        )
        forbidden_failures = _forbidden_failures(observed, forbidden_actions)
        pattern_failures = _forbidden_argument_pattern_failures(
            observed, forbidden_patterns
        )
        message_failures = _forbidden_message_pattern_failures(
            trace.messages,
            message_patterns,
            observed=observed,
            actions=task.evaluation_criteria.actions,
            positions=trace.positions,
        )
        message_failures.extend(
            numeric_claim_failures(
                observed,
                trace.messages,
                trace.positions,
                []
                if task.evaluation_criteria.completion_mode == "completion_only"
                else task.evaluation_criteria.numeric_message_claims,
            )
        )
        evidence_failures = _result_evidence_failures(
            observed, task.evaluation_criteria.actions or [], evidence_items
        )
        argument_failures = _argument_evidence_failures(
            observed, golden_actions, argument_items, trace, evidence_items
        )
        binding_failures = _result_argument_binding_failures(
            observed, task.evaluation_criteria.actions or [], bindings, access_policy
        )
        reward = (
            1.0
            if all_expectations_met
            and not order_failures
            and not forbidden_failures
            and not pattern_failures
            and not message_failures
            and not access_failures
            and not evidence_failures
            and not argument_failures
            and not binding_failures
            and not any(f.disposition == "violation" for f in findings)
            else 0.0
        )

        info = {}
        if findings:
            info["message_claims"] = [f.model_dump(mode="json") for f in findings]
        if any(f.disposition == "needs_review" for f in findings):
            info["grading_status"] = "needs_review"
        if access_failures:
            info["tool_access_policy"] = access_failures
        if order_failures:
            info["action_order"] = order_failures
        if forbidden_failures:
            info["forbidden_actions"] = forbidden_failures
        if pattern_failures:
            info["forbidden_argument_patterns"] = pattern_failures
        if message_failures:
            info["forbidden_message_patterns"] = message_failures
        if evidence_failures:
            info["result_evidence"] = evidence_failures
        if argument_failures:
            info["argument_evidence"] = argument_failures
        if binding_failures:
            info["result_argument_bindings"] = binding_failures

        return RewardInfo(
            reward=reward,
            action_checks=action_checks,
            reward_breakdown={RewardType.ACTION: reward},
            info=info or None,
        )

    @classmethod
    def extract_tool_calls(cls, full_trajectory: list[Tick]) -> list[ToolCall]:
        """
        Extract all tool calls from a tick trajectory.

        Args:
            full_trajectory: List of Tick objects from full-duplex simulation.

        Returns:
            List of ToolCall objects (user tool calls first, then agent tool calls per tick).
        """
        tool_calls: list[ToolCall] = []
        for tick in full_trajectory:
            tool_calls.extend(tick.user_tool_calls)
            tool_calls.extend(tick.agent_tool_calls)
        return tool_calls

    @classmethod
    def evaluate_actions(
        cls,
        full_trajectory: list[Tick],
        golden_actions: list[Action],
        tool_types: Optional[dict[str, ToolType]] = None,
        require_success: bool = False,
    ) -> list[ActionCheck]:
        """
        Evaluate whether the agent performed the expected actions.

        Args:
            full_trajectory: List of Tick objects from full-duplex simulation.
            golden_actions: List of expected actions from evaluation criteria.
            tool_types: Optional mapping of tool names to their types (read/write/think/generic).
        """
        if len(golden_actions) == 0:
            return []

        observed_calls = _observed_from_ticks(full_trajectory)
        return _check_actions(
            observed_calls, golden_actions, tool_types, require_success
        )
