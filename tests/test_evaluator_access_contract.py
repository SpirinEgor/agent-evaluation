"""Small ownership/authorization traces, independent of banking task fixtures."""

import json

import pytest

from tau2.data_model.message import AssistantMessage, Tick, ToolCall, ToolMessage
from tau2.data_model.tasks import Action, ToolAccessPolicy
from tau2.evaluator.evaluator_action import _tool_access_failures
from tau2.evaluator.trace_evidence import evidence_from_messages, evidence_from_ticks

AUTH = Action(
    action_id="auth", name="verify_identity", arguments={"customer_id": "own"}
)
POLICY = ToolAccessPolicy(
    policy_id="synthetic-access",
    allowed_customer_ids=["own"],
    authentication_action_ids=["auth"],
    resources_by_customer={
        "customers": {"own": ["own"], "other": ["other"]},
        "accounts": {"own": ["a", "b"], "other": ["foreign"]},
    },
    tool_arguments={
        "verify_identity": {"customer_id": "customers"},
        "transfer": {"from_id": "accounts", "to_id": "accounts"},
        "get_accounts": {"customer_id": "customers", "account_id": "accounts"},
        "create_account": {"customer_id": "customers"},
    },
    pre_auth_tools=["verify_identity"],
    result_resource_collections={"create_account": ["accounts"]},
)


def pair(name, arguments, *, id, content="ok", error=False):
    return (
        ToolCall(id=id, name=name, arguments=arguments),
        ToolMessage(id=id, role="tool", content=content, error=error),
    )


def observed(batches, mode):
    if mode == "full":
        return evidence_from_ticks(
            [
                Tick(
                    tick_id=i,
                    timestamp="2026-10-14T00:00:00",
                    agent_tool_calls=calls,
                    agent_tool_results=results,
                )
                for i, (calls, results) in enumerate(batches)
            ]
        ).observed
    messages = []
    for calls, results in batches:
        messages.append(AssistantMessage(role="assistant", tool_calls=calls))
        messages.extend(results)
    return evidence_from_messages(messages).observed


@pytest.mark.parametrize(
    "mode,variant,want_failure",
    [
        ("half", "own", False),
        ("half", "foreign_source_error", True),
        ("half", "foreign_target_error", True),
        ("half", "foreign_optional_filter", True),
        ("half", "before_auth", True),
        ("half", "auth_result_late", True),
        ("full", "auth_result_late", True),
        ("half", "unknown_retry", False),
        ("half", "new_id", False),
        ("half", "new_id_late", True),
        ("full", "new_id_late", True),
        ("half", "known_owner_substitution", True),
    ],
)
def test_access_checks_all_selectors_and_received_owners(mode, variant, want_failure):
    auth, auth_result = pair("verify_identity", {"customer_id": "own"}, id="auth")
    write, write_result = pair("transfer", {"from_id": "a", "to_id": "b"}, id="write")
    batches = [([auth], [auth_result])]
    if variant == "before_auth":
        batches.insert(0, ([write], [write_result.model_copy(update={"error": True})]))
    if variant.startswith("foreign_") or variant == "unknown_retry":
        if variant == "foreign_optional_filter":
            extra, result = pair(
                "get_accounts",
                {"customer_id": "own", "account_id": "foreign"},
                id="bad",
                error=True,
            )
        else:
            args = {"from_id": "a", "to_id": "b"}
            args["from_id" if variant == "foreign_source_error" else "to_id"] = (
                "unknown" if variant == "unknown_retry" else "foreign"
            )
            extra, result = pair(
                "transfer", args, id="bad", content="ERR_NOT_FOUND: missing", error=True
            )
        batches.append(([extra], [result]))
    if variant in {"new_id", "new_id_late", "known_owner_substitution"}:
        identifier = "foreign" if variant == "known_owner_substitution" else "created"
        create, result = pair(
            "create_account",
            {"customer_id": "own"},
            id="create",
            content=json.dumps({"id": identifier, "customer_id": "own"}),
        )
        write.arguments["from_id"] = identifier
        batches.append(([create], [] if variant == "new_id_late" else [result]))
        batches.append(([write], [write_result]))
        if variant == "new_id_late":
            batches.append(([], [result]))
    elif variant == "auth_result_late":
        batches = [([auth], []), ([write], [write_result]), ([], [auth_result])]
    else:
        batches.append(([write], [write_result]))
    failures = _tool_access_failures(observed(batches, mode), [AUTH], POLICY)
    assert bool(failures) is want_failure, failures


@pytest.mark.parametrize(
    "variant",
    [
        "received",
        "foreign_id",
        "wrong_original",
        "substituted_id",
        "undeclared",
        "foreign_source",
        "unknown_source",
    ],
)
def test_refund_link_inherits_only_the_known_original_owner(variant):
    policy = POLICY.model_copy(deep=True)
    policy.resources_by_customer["transactions"] = {
        "own": ["original"],
        "other": ["foreign-txn"],
    }
    policy.tool_arguments.update(
        {
            "refund_fee": {"transaction_id": "transactions"},
            "get_transaction_details": {"transaction_id": "transactions"},
        }
    )
    policy.result_resource_collections["refund_fee"] = ["transactions"]
    auth, auth_result = pair("verify_identity", {"customer_id": "own"}, id="auth")
    original = {"foreign_source": "foreign-txn", "unknown_source": "missing"}.get(
        variant, "original"
    )
    refund_id = "foreign-txn" if variant == "foreign_id" else "refund"
    body = {
        "original_transaction_id": "different"
        if variant == "wrong_original"
        else original,
        "refund_transaction_id": refund_id,
    }
    refund, receipt = pair(
        "refund_fee",
        {"transaction_id": original},
        id="refund-call",
        content=json.dumps(body),
    )
    read, result = pair(
        "get_transaction_details",
        {"transaction_id": "substituted" if variant == "substituted_id" else refund_id},
        id="read",
    )
    if variant == "undeclared":
        policy.result_resource_collections.pop("refund_fee")
    failures = _tool_access_failures(
        observed(
            [([auth], [auth_result]), ([refund], [receipt]), ([read], [result])], "half"
        ),
        [AUTH],
        policy,
    )
    assert bool(failures) is (variant != "received"), failures


@pytest.mark.parametrize(
    "mode,variant",
    [
        ("half", "received"),
        ("half", "hash"),
        ("half", "error"),
        ("half", "first_failed_attempt"),
        ("full", "late"),
    ],
)
def test_alternative_source_keeps_exact_receipt_and_first_attempt(mode, variant):
    from tau2.data_model.tasks import ResultEvidence
    from tau2.evaluator.evaluator_action import _result_evidence_calls

    action = Action(action_id="write", name="write", arguments={})
    evidence = ResultEvidence(
        evidence_id="source-or",
        tool_names=["file_read"],
        record_id="primary",
        expected_fields={
            "sha256": "primary-hash",
            "content": "primary-body",
            "complete": True,
        },
        alternatives=[
            {
                "tool_names": ["file_read"],
                "record_id": "alternative",
                "expected_fields": {
                    "sha256": "alternative-hash",
                    "content": "alternative-body",
                    "complete": True,
                },
            }
        ],
        before_action_id="write",
    )
    body = {
        "id": "alternative",
        "sha256": "wrong" if variant == "hash" else "alternative-hash",
        "content": "alternative-body",
        "complete": True,
        "extra_display_field": "ignored",
    }
    source, result = pair(
        "file_read",
        {"path": "/alternative.md"},
        id="source",
        content=json.dumps(body, sort_keys=True),
        error=variant == "error",
    )
    target, target_result = pair("write", {}, id="target")
    batches = [
        ([source], [] if variant == "late" else [result]),
        ([target], [target_result]),
    ]
    if variant == "late":
        batches.append(([], [result]))
    if variant == "first_failed_attempt":
        failed, error = pair("write", {}, id="failed", error=True)
        batches.insert(0, ([failed], [error]))
    assert bool(
        _result_evidence_calls(observed(batches, mode), [action], evidence)
    ) is (variant == "received")


@pytest.mark.parametrize("mode", ["half", "full"])
def test_nested_foreign_selector_rejected_even_on_error(mode):
    policy = POLICY.model_copy(deep=True)
    policy.tool_arguments["review"] = {"items.*.transaction_id": "accounts"}
    auth, auth_result = pair("verify_identity", {"customer_id": "own"}, id="auth")
    call, result = pair(
        "review",
        {"items": [{"transaction_id": "foreign", "amount": 1}]},
        id="nested",
        error=True,
    )
    failures = _tool_access_failures(
        observed([([auth], [auth_result]), ([call], [result])], mode), [AUTH], policy
    )
    assert any(item["reason"] == "foreign_owner" for item in failures)


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize("name", ["get_service_incidents", "get_partner_confirmations"])
def test_shared_component_reads_enforce_owner_and_identity(mode, name):
    policy = POLICY.model_copy(update={"policy_id": "banking_private_access"})
    auth, result = pair("verify_identity", {"customer_id": "own"}, id="auth")
    own, own_result = pair(
        name, {"customer_id": "own"}, id="read", content='{"records":[]}'
    )
    foreign, foreign_result = pair(
        name, {"customer_id": "other"}, id="bad", content='{"records":[]}'
    )
    assert not _tool_access_failures(
        observed([([auth], [result]), ([own], [own_result])], mode), [AUTH], policy
    )
    assert _tool_access_failures(
        observed([([own], [own_result])], mode), [AUTH], policy
    )
    assert _tool_access_failures(
        observed([([auth], [result]), ([foreign], [foreign_result])], mode),
        [AUTH],
        policy,
    )


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize("foreign", [False, True])
def test_common_resolution_operation_access_learns_received_ids(mode, foreign):
    policy = POLICY.model_copy(deep=True)
    policy.policy_id = "banking_private_access"
    policy.resources_by_customer["reward_resolution_executions"] = {
        "other": ["foreign-op"]
    }
    policy.tool_arguments["execute_reward_resolution"] = {"customer_id": "customers"}
    auth = pair("verify_identity", {"customer_id": "own"}, id="a")
    execution = pair(
        "execute_reward_resolution",
        {"customer_id": "own"},
        id="e",
        content=json.dumps(
            {
                "id": "foreign-op" if foreign else "new-op",
                "customer_id": "own",
                "status": "execution_unknown",
            }
        ),
    )
    status = pair(
        "get_reward_resolution_status",
        {"resolution_operation_id": "foreign-op" if foreign else "new-op"},
        id="s",
        content="{}",
    )
    rows = observed([([c], [r]) for c, r in [auth, execution, status]], mode)
    problems = _tool_access_failures(rows, [AUTH], policy)
    assert any(p["reason"] == "foreign_owner" for p in problems) is foreign
    if not foreign:
        assert problems == []


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "variant",
    [
        "snapshot",
        "replay",
        "status",
        "no_equivalent",
        "guessed",
        "offer_id",
        "wrong_owner",
        "wrong_scope",
        "foreign_known",
    ],
)
def test_resolution_operation_equivalent_receipts_before_fallback(mode, variant):
    policy = POLICY.model_copy(deep=True)
    policy.policy_id = "banking_private_access"
    policy.resources_by_customer["reward_resolution_executions"] = {
        "other": ["foreign-op"]
    }
    policy.tool_arguments.update(
        {
            "execute_reward_resolution": {"customer_id": "customers"},
            "get_reward_resolution_offers": {"customer_id": "customers"},
        }
    )
    auth = pair("verify_identity", {"customer_id": "own"}, id="auth")
    ident = "foreign-op" if variant == "foreign_known" else "new-op"
    operation = {
        "id": ident,
        "customer_id": "own",
        "case_id": "case",
        "period": "period",
        "offer_id": "offer",
        "transaction_ids": ["txn"],
        "status": "execution_unknown",
    }
    execute, original = pair(
        "execute_reward_resolution",
        {"customer_id": "own"},
        id="first",
        content=json.dumps(operation),
    )
    batches = [([auth[0]], [auth[1]])]
    if variant not in {"guessed", "offer_id", "wrong_owner", "wrong_scope"}:
        batches.append(([execute], []))
    if variant in {
        "snapshot",
        "wrong_owner",
        "wrong_scope",
        "offer_id",
        "foreign_known",
    }:
        row = {
            "id": "offer",
            "customer_id": "own",
            "case_id": "case",
            "period": "period",
            "transaction_ids": ["txn"],
        }
        if variant == "wrong_owner":
            operation["customer_id"] = "other"
        if variant == "wrong_scope":
            operation["case_id"] = "another-case"
        row["funding_reserve"] = {"operations": [operation]}
        receipt = pair(
            "get_reward_resolution_offers",
            {"customer_id": "own", "case_id": "case"},
            id="snapshot",
            content=json.dumps([row]),
        )
        batches.append(([receipt[0]], [receipt[1]]))
    if variant in {"status", "replay"}:
        receipt = pair(
            "execute_reward_resolution"
            if variant == "replay"
            else "get_reward_resolution_status",
            {"customer_id": "own"}
            if variant == "replay"
            else {"resolution_operation_id": ident},
            id="equivalent",
            content=json.dumps({**operation, "status": "failed_final"}),
        )
        # The status request needs an earlier ID receipt; this unit isolates
        # whether its result can replace the late original before fallback.
        if variant == "status":
            batches[-1][1].append(original)
        batches.append(([receipt[0]], [receipt[1]]))
    target = pair(
        "ask_client",
        {
            "request": {
                "fallback_operation_id": "offer" if variant == "offer_id" else ident
            }
        },
        id="fallback",
    )
    batches.append(([target[0]], [target[1]]))
    if variant not in {"guessed", "offer_id", "wrong_owner", "wrong_scope", "status"}:
        batches.append(([], [original]))
    failures = _tool_access_failures(observed(batches, mode), [AUTH], policy)
    assert bool(failures) is (variant not in {"snapshot", "replay", "status"}), failures
