"""Declared process sources for the existing banking tasks; no new bank rules."""

GENERAL_PATH = "/policies/general.md"
IDENTITY_PATH = "/policies/identity.md"

# Explicit task IDs prevent a shared text block from patching the wrong task.
PROCESS_SOURCES = {
    1: ("cards", "transfers_fraud"),
    2: ("fees_rewards",),
    3: ("cards",),
    4: ("cards",),
    5: ("cards",),
    6: ("fees_rewards",),
    7: ("subscriptions",),
    8: ("deposits",),
    9: ("cards",),
    10: ("loans",),
    11: ("disputes",),
    12: ("disputes",),
    13: ("disputes",),
    14: ("disputes", "subscriptions"),
    15: ("cards", "transfers_fraud"),
    16: ("cards", "disputes", "transfers_fraud"),
    17: ("disputes",),
    18: ("disputes", "transfers_fraud"),
    19: ("cards", "transfers_fraud"),
    20: ("disputes", "transfers_fraud"),
    21: ("cards",),
    22: ("cards",),
    23: ("cards",),
    24: ("cards",),
    25: ("disputes",),
    26: ("transfers_fraud",),
    27: ("subscriptions",),
    28: ("security_escalation",),
    29: ("accounts",),
    30: ("accounts",),
    31: ("accounts",),
    32: ("deposits",),
    33: ("accounts",),
    34: ("loans",),
    35: ("loans",),
    36: ("loans",),
    37: ("loans",),
    38: ("loans",),
    39: ("loans",),
    40: ("fees_rewards",),
    41: ("fees_rewards",),
    42: ("fees_rewards",),
    43: ("fees_rewards",),
    44: ("fees_rewards",),
    45: ("cards",),
    46: ("security_escalation",),
    47: ("security_escalation",),
    48: ("security_escalation",),
    49: ("cards", "transfers_fraud"),
    50: ("security_escalation",),
    51: ("security_escalation",),
    52: ("fees_rewards",),
    53: ("deposits", "loans"),
    54: ("subscriptions", "disputes"),
    55: ("deposits",),
    56: ("disputes",),
    57: ("subscriptions",),
    58: ("accounts",),
    59: ("cards", "security_escalation"),
    60: ("cards",),
    61: ("cards",),
}


def policy_paths(task_id: str) -> tuple[str, ...]:
    number = int(task_id.removeprefix("Bank "))
    if task_id != f"Bank {number}":
        raise ValueError(f"Unknown banking task: {task_id}")
    return (GENERAL_PATH, IDENTITY_PATH) + tuple(
        f"/policies/{name}.md" for name in PROCESS_SOURCES[number]
    )


def refresh_policy_sources(tasks: list[dict], corpus) -> None:
    """Refresh reference reads and received-source checks, preserving outcomes.

    General-policy action/evidence IDs remain stable. Identity procedures are
    needed for identity/OTP checks; process sources do not gate identification.
    Existing general-policy targets define the state-changing process attempts
    and final reply. Knowledge documents and their dependent targets stay intact.
    """
    receipts = {path: corpus.read(path) for path in corpus.documents}
    for task in tasks:
        paths = policy_paths(task["id"])
        criteria = task["evaluation_criteria"]
        actions = criteria["actions"]
        general = next(
            a
            for a in actions
            if a["name"] == "file_read"
            and a["arguments"].get("path") in {"/policies/bank.md", GENERAL_PATH}
        )
        general["arguments"]["path"] = GENERAL_PATH
        if general.get("result_contains"):
            # The older file pilot pinned the monolith's SHA in this action too.
            general["result_contains"] = [
                receipts[GENERAL_PATH]["sha256"],
                '"complete": true',
            ]
        optional_reads = {
            a["arguments"].get("path")
            for a in actions
            if a["name"] == "file_read" and a.get("required_for_reward") is False
        }
        # Replace only generated process reads, leaving all business actions intact.
        actions[:] = [
            a for a in actions if not a["action_id"].startswith("read_process_")
        ]
        offset = actions.index(general) + 1
        generated = [
            {
                "action_id": f"read_process_{path.rsplit('/', 1)[-1][:-3]}_{task['id']}",
                "name": "file_read",
                "arguments": {"path": path},
                "compare_args": ["path"],
            }
            for path in paths[1:]
        ]
        for read in generated:
            if read["arguments"]["path"] in optional_reads:
                read["required_for_reward"] = False
        actions[offset:offset] = generated
        by_id = {a["action_id"]: a for a in actions}
        evidence = criteria.get("result_evidence", [])
        document_pinned_ids = {
            e["evidence_id"]
            for e in evidence
            if "file_read" in e.get("tool_names", [])
            and "sha256" in e.get("expected_fields", {})
            and "snapshot_sha256" not in e.get("expected_fields", {})
        }
        old_general = [
            e
            for e in evidence
            if e.get("expected_fields", {}).get("path")
            in {"/policies/bank.md", GENERAL_PATH}
        ]
        targets = {
            e["before_action_id"] for e in old_general if e.get("before_action_id")
        }
        identity_targets = {
            a["action_id"]
            for a in actions
            if a["name"] in {"verify_identity", "send_otp", "check_otp"}
        }
        process_targets = {
            target
            for target in targets
            if by_id[target]["name"]
            not in {
                "verify_identity",
                "send_otp",
                "check_otp",
                "ask_client",
                "calculate",
            }
            and not by_id[target]["name"].startswith(
                ("get_", "list_", "find_", "file_")
            )
        }
        # A builder may have only the common-policy sources, or be run repeatedly.
        evidence[:] = [
            e for e in evidence if not e["evidence_id"].startswith("process-source:")
        ]
        for item in evidence:
            fields = item.get("expected_fields", {})
            path = fields.get("path")
            if path == "/policies/bank.md":
                path = GENERAL_PATH
            if "file_read" in item.get("tool_names", []) and path in receipts:
                receipt = receipts[path]
                item["record_id"] = receipt["id"]
                item["expected_fields"] = {
                    key: receipt[key]
                    for key in (
                        "path",
                        "sha256",
                        "snapshot_sha256",
                        "complete",
                        "content",
                    )
                    if key != "snapshot_sha256"
                    or item["evidence_id"] not in document_pinned_ids
                }
        for path in paths[1:]:
            receipt = receipts[path]
            required_targets = (
                identity_targets if path == IDENTITY_PATH else process_targets
            )
            overrides = criteria.get("process_source_targets", {})
            if path in overrides:
                required_targets = set(overrides[path])
                if any(target not in by_id for target in required_targets):
                    raise ValueError(f"Unknown source target for {path}: {task['id']}")
            for target in sorted(required_targets):
                evidence.append(
                    {
                        "evidence_id": f"process-source:{task['id']}:{receipt['id']}:{target}",
                        "tool_names": ["file_read"],
                        "record_id": receipt["id"],
                        "expected_fields": {
                            key: receipt[key]
                            for key in (
                                "path",
                                "sha256",
                                "snapshot_sha256",
                                "complete",
                                "content",
                            )
                            if key != "snapshot_sha256"
                            or f"process-source:{task['id']}:{receipt['id']}:{target}"
                            not in document_pinned_ids
                        },
                        "before_action_id": target,
                    }
                )
        # Keep the discovery generator's canonical placement (it appends these).
        criteria["result_evidence"] = [
            e for e in evidence if not e["evidence_id"].startswith("common_discovery_")
        ] + [e for e in evidence if e["evidence_id"].startswith("common_discovery_")]
        # Evidence already proves receipt timing; do not demand a particular search.
        criteria["action_order"] = [
            pair
            for pair in criteria.get("action_order", [])
            if not pair["before_action_id"].startswith("read_process_")
        ]
        description = task.get("description", {})
        original = description.get("relevant_policies", "")
        marker = "\nПрименимые регламенты: "
        description["relevant_policies"] = (
            original.split(marker)[0].replace("/policies/bank.md", GENERAL_PATH)
            + marker
            + ", ".join(paths)
        )
