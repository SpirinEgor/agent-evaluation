"""Verified full-source aliases for ACTION only; never rewrite the raw journal."""

import json
import shlex
from copy import deepcopy

CARD_FIELDS = (
    "document_id",
    "revision_id",
    "document_type",
    "title",
    "created_at",
    "updated_at",
)


def equivalent_source_reads(criteria, observed, corpus):
    if criteria.banking_source_read_contract is None:
        return observed
    if corpus is None:
        raise ValueError(
            "Source read equivalence requires the scenario corpus snapshot"
        )
    # FileCorpus derives embedded/equivalent receipts from actually received full
    # texts. Keep this state private to grading, in result-delivery order.
    snapshot = deepcopy(corpus)
    snapshot._read_paths = set()
    normalized = list(observed)
    for index in sorted(
        range(len(observed)),
        key=lambda i: observed[i].result_position
        if observed[i].result_position is not None
        else float("inf"),
    ):
        item = observed[index]
        call, result = item.call, item.result
        if call.requestor != "assistant" or call.name not in {
            "file_read",
            "read_document",
            "corpus_command",
        }:
            continue
        legacy = call.name == "file_read"
        path = None
        try:
            if (
                result is None
                or result.error
                or result.execution_rejected
                or result.id != call.id
                or result.requestor != "assistant"
            ):
                raise ValueError()
            payload = json.loads(result.content or "")
            if not isinstance(payload, dict):
                raise ValueError()
            path = payload.get("path")
            if not isinstance(path, str) or not path.startswith(
                ("/knowledge/", "/policies/")
            ):
                raise ValueError()
            doc = snapshot.documents[path]
            meta = doc["metadata"]
            if legacy:
                if call.arguments.get("path") != path or any(
                    payload.get(k) != value
                    for k, value in {
                        "id": meta["id"],
                        "path": path,
                        "sha256": doc["sha256"],
                        "content": doc["content"],
                        "complete": True,
                        "metadata": meta,
                        "snapshot_sha256": snapshot.sha256,
                    }.items()
                ):
                    raise ValueError()
            else:
                expected = {
                    **{key: meta[key] for key in CARD_FIELDS},
                    "path": path,
                    "content": doc["content"],
                    "content_sha256": doc["sha256"],
                }
                if payload != expected:
                    raise ValueError()
                if call.name == "read_document":
                    if call.arguments != {
                        "document_id": meta["document_id"],
                        "revision_id": meta["revision_id"],
                    }:
                        raise ValueError()
                elif (
                    set(call.arguments) - {"command", "cursor"}
                    or call.arguments.get("cursor") is not None
                    or shlex.split(call.arguments.get("command", "")) != ["cat", path]
                ):
                    raise ValueError()
            receipt = snapshot.read(path)
        except (ValueError, TypeError, KeyError, AttributeError):
            # Invalid full-read results cannot witness a required source. Safety
            # checks still receive the untouched original call and result.
            if legacy:
                normalized[index] = item.model_copy(
                    update={
                        "call": call.model_copy(
                            update={"name": "unverified_source_read"}
                        )
                    }
                )
            continue
        normalized[index] = item.model_copy(
            update={
                "call": call.model_copy(
                    update={"name": "file_read", "arguments": {"path": path}}
                ),
                "result": result.model_copy(
                    update={"content": json.dumps(receipt, ensure_ascii=False)}
                ),
            }
        )
    return normalized
