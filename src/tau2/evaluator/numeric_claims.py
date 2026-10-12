"""Deterministic, declared outgoing numeric claims and received state updates.

Only configured text forms are interpreted. This is not a general semantic
judge; domain generators declare the language and the resource mapping.
"""

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from tau2.data_model.message import AssistantMessage
from tau2.data_model.tasks import NumericMessageClaim
from tau2.evaluator.trace_evidence import TraceEvidence, outgoing_entries


def outgoing_utterance_entries(
    messages: list[AssistantMessage],
    positions: list[int],
    tool_arguments: dict[str, list[str]],
) -> list[tuple[str, list[tuple[int, int]]]]:
    """Compatibility tuples backed by the shared outgoing trace entries."""
    return [
        (entry.text, entry.segments)
        for entry in outgoing_entries(
            TraceEvidence(messages=messages, positions=positions), tool_arguments
        )
    ]


def _normalise(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", text)
        if unicodedata.category(c) != "Cf"
    )


def _number(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("A boolean is not a monetary value")
    result = Decimal(re.sub(r"\s+", "", str(value)).replace(",", "."))
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid numeric state")
    return result


def numeric_claim_failures(
    observed,
    messages: list[AssistantMessage],
    positions: list[int],
    rules: list[NumericMessageClaim],
) -> list[str]:
    """Check claims at emission, never against a later repaired final state."""
    failures = []
    for rule in rules:
        values = {key: _number(value) for key, value in rule.initial_values.items()}
        updates = []
        for item in observed:
            call, result = item.call, item.result
            if (
                call.requestor != "assistant"
                or call.name != rule.update_tool
                or result is None
                or result.error
                or item.result_position is None
            ):
                continue
            if any(
                call.arguments.get(key) != value
                for key, value in rule.update_arguments.items()
            ):
                continue
            resource = call.arguments.get(rule.resource_argument)
            if resource not in values:
                continue
            try:
                content = json.loads(result.content)
                value = _number(call.arguments[rule.value_argument])
                if (
                    not isinstance(content, dict)
                    or content.get(rule.result_resource_field) != resource
                    or _number(content[rule.result_value_field]) != value
                ):
                    continue
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
            updates.append((item.result_position, resource, value))
        updates.sort(key=lambda entry: entry[0])
        next_update = 0
        failed = False
        for raw_text, raw_segments in outgoing_utterance_entries(
            messages, positions, rule.tool_arguments
        ):
            # Unicode normalization changes character counts: preserve the
            # receipt position for each normalized fragment boundary too.
            text = _normalise(raw_text)
            segments = [
                (len(_normalise(raw_text[:end])), position)
                for end, position in raw_segments
            ]
            for match in re.finditer(rule.pattern, text, re.IGNORECASE):
                position = next(
                    position for end, position in segments if end >= match.end()
                )
                while next_update < len(updates) and updates[next_update][0] < position:
                    _, resource, value = updates[next_update]
                    values[resource] = value
                    next_update += 1
                candidates = set(values)
                selectors = (
                    list(
                        re.finditer(
                            rule.resource_pattern, match.group(0), re.IGNORECASE
                        )
                    )
                    if rule.resource_pattern
                    else []
                )
                if selectors:
                    candidates = set()
                    for selector in selectors:
                        candidates.update(
                            rule.resource_aliases.get(selector.group("resource"), [])
                        )
                if rule.require_update:
                    cutoff = position
                    if rule.witness_capture is not None:
                        witness_end = match.end(rule.witness_capture)
                        if witness_end < 0:
                            candidates = set()
                        else:
                            cutoff = next(
                                position
                                for end, position in segments
                                if end >= witness_end
                            )
                    candidates &= {
                        resource
                        for update_position, resource, _ in updates
                        if update_position < cutoff
                    }
                token = match.group("value")
                try:
                    value = (
                        _number(rule.value_aliases.get(token.casefold(), token))
                        if token is not None
                        else None
                    )
                except (ValueError, InvalidOperation):
                    candidates = set()
                    value = None
                if not candidates or (
                    value is not None
                    and not any(values[key] == value for key in candidates)
                ):
                    failed = True
                    break
            if failed:
                break
        if failed:
            failures.append(rule.rule_id)
    return failures
