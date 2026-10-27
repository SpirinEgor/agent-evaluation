# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["langfuse==5.0.4"]
# ///
"""Optional post-run export; never part of the bank grader or model tool surface."""

import hashlib
import json
import os
import re
from contextlib import nullcontext


def export_attempt(record, *, model, ticket, metadata=None, client=None):
    """Submit recorded messages, tools and grades without changing the record.

    Spans represent import time; original event times are retained as metadata.
    Codex JSON output does not expose full model requests or per-call timings.
    A submitted result means flush was requested, not independently read back.
    """
    if client is None and os.getenv("BANKING_LANGFUSE_ENABLED", "").lower() != "true":
        return {"status": "disabled"}
    try:
        context = nullcontext()
        attempt = hashlib.sha256(
            str(record.get("token") or metadata).encode()
        ).hexdigest()
        trace_id = attempt[:32]
        secrets = [
            value
            for value in (
                record.get("token"),
                os.getenv("LANGFUSE_SECRET_KEY"),
                os.getenv("LANGFUSE_PUBLIC_KEY"),
            )
            if value
        ]

        def clean(value):
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(v) for v in value]
            if isinstance(value, str):
                for secret in secrets:
                    value = value.replace(secret, "[credential]")
                return re.sub(r"(?:sk|pk)-lf-[A-Za-z0-9-]+", "[credential]", value)
            return value

        if client is None:
            from langfuse import Langfuse, propagate_attributes

            client = Langfuse(
                base_url=os.environ["LANGFUSE_BASE_URL"],
                public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                environment=os.getenv(
                    "LANGFUSE_TRACING_ENVIRONMENT", "banking-development"
                ),
                timeout=5,
            )
            context = propagate_attributes(
                session_id=attempt, tags=["banking_ru"], trace_name="banking.attempt"
            )
        result = record.get("result") or {}
        events = result.get("mcp_events") or []
        run = record.get("run") or {}
        messages = []
        usage = None
        for line in run.get("stdout", "").splitlines():
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item") or {}
            if (
                event.get("type") == "item.completed"
                and item.get("type") == "agent_message"
            ):
                messages.append(item)
            if event.get("type") == "turn.completed":
                usage = event.get("usage")
        summary = {
            key: record.get(key)
            for key in (
                "classification",
                "final_reward",
                "technical_failure",
                "protocol_status",
            )
        }
        with (
            context,
            client.start_as_current_observation(
                name="banking.attempt",
                as_type="agent",
                trace_context={"trace_id": trace_id},
                input=clean({"ticket": ticket}),
                output=clean(summary),
                metadata=clean(
                    {
                        **(metadata or {}),
                        "task_id": record.get("task_id"),
                        "model": model,
                        "attempt_id": attempt,
                        "usage": usage,
                        "capture": "post-run import; not per-generation instrumentation",
                        "timing": "span times are import times; recorded times are metadata",
                    }
                ),
            ),
        ):
            for item in messages:
                with client.start_as_current_observation(
                    name="agent.message",
                    as_type="span",
                    output=clean(item.get("text")),
                    metadata={"codex_item_id": item.get("id")},
                ):
                    pass
            for event in events:
                name = (
                    event.get("canonical_name")
                    or event.get("original_name")
                    or "unknown"
                )
                with client.start_as_current_observation(
                    name=f"{event.get('system', 'bank')}.{name}",
                    as_type="retriever"
                    if name
                    in {
                        "file_read",
                        "file_search",
                        "file_find",
                        "file_list",
                        "get_article",
                        "search_knowledge",
                    }
                    else "tool",
                    input=clean(
                        event.get("canonical_arguments", event.get("arguments"))
                    ),
                    output=clean(event.get("response")),
                    level="ERROR" if event.get("error") else "DEFAULT",
                    metadata=clean(
                        {
                            key: event.get(key)
                            for key in (
                                "sequence",
                                "call_id",
                                "original_name",
                                "request_order",
                                "response_order",
                                "requested_at",
                                "returned_at",
                                "transmitted_at",
                                "execution_rejected",
                            )
                        }
                    ),
                ):
                    pass
            if record.get("technical_failure") or record.get("error"):
                with client.start_as_current_observation(
                    name="harness.error",
                    as_type="span",
                    level="ERROR",
                    output=clean(
                        {
                            "error": record.get("error"),
                            "exit_code": run.get("exit_code"),
                            "timed_out": run.get("timed_out"),
                        }
                    ),
                ):
                    pass
        scores = dict(result.get("reward_breakdown") or {})
        if record.get("final_reward") is not None:
            scores = {"bank_reward": record["final_reward"], **scores}
        for name, value in scores.items():
            if isinstance(value, (int, float)):
                client.create_score(trace_id=trace_id, name=name, value=value)
        client.flush()
        return {"status": "submitted", "trace_id": trace_id}
    except Exception as error:
        return {"status": "failed", "error_type": type(error).__name__}


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--metadata-json", default="{}")
    args = parser.parse_args()
    record = json.loads(args.record.read_text())
    print(
        json.dumps(
            export_attempt(
                record,
                model=args.model,
                ticket=record.get("ticket"),
                metadata=json.loads(args.metadata_json),
            )
        )
    )
