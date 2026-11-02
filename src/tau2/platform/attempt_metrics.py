"""Versioned, diagnostic banking metrics; never match a reference trajectory."""

import json
import re
from collections import Counter

FORMAT_CODES = {"INVALID_ARGUMENT", "UNKNOWN_SYSTEM", "UNKNOWN_TOOL"}


def tool_metrics(events, *, read_tools, mutating_tools):
    """Count host-observed calls. Repeats/recovery are conservative indicators."""
    categories = Counter()
    codes = Counter()
    pending = {}
    recovered = []
    reads = set()
    repeats = risk = invalid = errors = discovery = eligible = 0
    for event in events:
        name = event.get("canonical_name", event.get("original_name"))
        args = event.get("canonical_arguments", event.get("arguments", {}))
        # Directory gateway retains outer arguments in some event versions.
        if name != "bank_directory" and isinstance(args, dict) and "tool_name" in args:
            args = args.get("arguments", {})
        owner = event.get("system")
        key = (owner, name)
        discovery += event.get("budget") == "discovery"
        failed = bool(event.get("error"))
        origin = event.get("error_origin")
        if origin not in {"corpus", "infrastructure"}:
            eligible += 1
        code = event.get("diagnostic_code")
        if failed:
            errors += 1
            if not code:
                match = re.search(r"\bERR_[A-Z_]+\b", str(event.get("response", "")))
                code = match[0] if match else "UNCLASSIFIED"
            codes[code] += 1
            if origin in {"corpus", "infrastructure"}:
                category = "unclassified"  # excluded from format accuracy
            elif code in FORMAT_CODES:
                category = "format"
                invalid += 1
            elif code == "TOOL_SCHEMA_NOT_OPENED":
                category = "schema_not_opened"
            elif code == "INVALID_CURSOR":
                category = "cursor"
            elif code == "INVALID_COMMAND":
                category = "command"
            elif code.endswith("NOT_FOUND") or code.endswith("UNKNOWN_ID"):
                category = "unknown_id"
            elif "ACCESS" in code or "FORBIDDEN" in code:
                category = "access_denied"
            elif code.startswith("ERR_"):
                category = "business_rejection"
            else:
                category = "unclassified"
            categories[category] += 1
            # One unresolved episode per operation, even after several retries.
            pending.setdefault(key, {"calls": 0, "sequence": event.get("sequence")})
        for episode in pending.values():
            episode["calls"] += 1
        if not failed and key in pending:
            recovered.append(pending.pop(key)["calls"] - 1)
        if name in mutating_tools:
            risk += 1
            # Even a failed mutation may change state: invalidate conservatively.
            reads.clear()
        if name in read_tools and not failed and "response" in event:
            fingerprint = (
                name,
                json.dumps(args, sort_keys=True, ensure_ascii=False),
                json.dumps(event["response"], sort_keys=True, ensure_ascii=False),
            )
            repeats += fingerprint in reads
            reads.add(fingerprint)
    episodes = len(recovered) + len(pending)
    return {
        "calls": {
            "total": len(events),
            "discovery": discovery,
            "other": len(events) - discovery,
            "state_mutating_attempts": risk,
        },
        "format_accuracy": {
            "eligible_calls": eligible,
            "invalid_calls": invalid,
            "rate": (eligible - invalid) / eligible if eligible else None,
        },
        "errors": {
            "total": errors,
            "by_category": dict(categories),
            "by_code": dict(codes),
        },
        "recovery": {
            "episodes": episodes,
            "recovered": len(recovered),
            "rate": len(recovered) / episodes if episodes else None,
            "calls_until_recovery": recovered,
            "scope": "later_success_same_system_and_tool_not_business_completion",
        },
        "repeated_reads": repeats,
    }


def model_metrics(run):
    """Only observed compute; absent usage/pricing must never become zero."""
    evidence = (run or {}).get("broker_evidence", {})
    completions = [
        e for e in evidence.get("events", []) if e.get("event") == "response_completed"
    ]
    usages = [e.get("usage") for e in completions]
    complete = bool(usages) and all(isinstance(u, dict) for u in usages)
    tokens = None
    if complete:
        tokens = {
            k: sum(u[k] for u in usages)
            for k in ("input_tokens", "output_tokens", "total_tokens")
            if all(
                isinstance(u.get(k), int) and not isinstance(u[k], bool) for u in usages
            )
        }
        if len(tokens) != 3:
            tokens = None
    return {
        "model_requests": evidence.get("upstream_requests"),
        "model_responses": len(completions) if evidence else None,
        "tokens": tokens,
        "usage_complete": tokens is not None,
        "cost": {
            "amount": None,
            "currency": "USD",
            "status": "unavailable_subscription_pricing",
        },
    }
