"""Addressed whole-grade subscription consolidation: Bank 14/54/56 only."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def test_shared_startup_publishes_required_sources_and_virtual_paths():
    """Required documents are discovered through MCP, not named at startup."""
    from tau2.domains.banking_ru.mcp_systems import startup_policy

    policy = startup_policy()
    assert "/policies/general.md" not in policy
    assert "/policies/identity.md" not in policy
    assert "MCP" in policy and "Самостоятельно" in policy
    tasks = {t.id: t for t in get_tasks()}
    assert {
        PlatformSession(
            tasks[f"Bank {n}"], get_environment, mcp_layout="systems"
        ).policy
        for n in [14, 54, 56]
    } == {policy}


def test_generic_claim_article_points_to_its_authoritative_process():
    """The lookup article routes to RO-04 without duplicating its full procedure."""
    session = PlatformSession(
        next(t for t in get_tasks() if t.id == "Bank 56"), get_environment
    )
    content = _payload(session.call("file_read", {"path": "/knowledge/kb_112.md"}))[
        "content"
    ]
    assert "/policies/disputes.md" in content


def test_removed_duplicate_cannot_be_read_as_active_rules():
    """A retired duplicate must not remain a competing readable process source."""
    task = next(t for t in get_tasks() if t.id == "Bank 54")
    session = PlatformSession(task, get_environment)
    result = session.call("file_read", {"path": "/knowledge/kb_169.md"})
    assert result.is_error, "Retired kb169 is still accessible to the agent"


OWNERS = {14: "smirnova_o_1123", 54: "kuznetsov_i_3140", 56: "fedorov_k_6610"}
TRANSACTIONS = {14: "txn_055210", 54: "txn_314101", 56: "txn_661001"}
AMOUNTS = {14: 3000.0, 54: 2200.0, 56: 8900.0}
CASES = [
    (n, route, 1)
    for n in [14, 54, 56]
    for route in [
        "reference",
        "canonical_code",
        "canonical_birth",
        "duplicate_source",
        "unknown_error_before_valid",
    ]
]
CASES += [
    (n, route, 0)
    for n in [14, 54, 56]
    for route in [
        "noop",
        "early_done",
        "source_after_target",
        "source_response_after_target",
        "partial_source",
        "wrong_source",
        "pre_auth_read",
        "foreign_read",
        "wrong_transaction",
        "extra_refund",
    ]
]
CASES += [
    (14, "missing_recognition", 0),
    (14, "fact_response_after_question", 0),
    (14, "extra_cancel", 0),
]
CASES += [
    (n, route, 0 if n == 54 and route == "case_default_amount" else 1)
    for n in [54, 56]
    for route in [
        "dispute_other",
        "case_merchant",
        "case_other",
        "case_escalation",
        "case_list",
        "case_default_amount",
    ]
]
CASES += [(56, "no_navigation_article", 1)]
CASES += [(n, "wrong_amount", 0) for n in [54, 56]]
CASES += [(54, "extra_cancel", 0)]
CASES += [(n, "failed_target_before_auth", 0) for n in [14, 54, 56]]
CASES += [(n, "failed_target_before_source", 0) for n in [54, 56]]


def _payload(result):
    assert not result.is_error
    return json.loads(result.content[0].text)


def _actual_identity(session, task, owner, method):
    find = next(
        a for a in task.evaluation_criteria.actions if a.name == "find_customer"
    )
    assert not session.call("find_customer", deepcopy(find.arguments)).is_error
    answer = _payload(
        session.call(
            "ask_client",
            {"customer_id": owner, "request": {"type": "identity", "method": method}},
        )
    )
    assert not session.call(
        "verify_identity",
        {
            "customer_id": owner,
            "credential": answer["data"]["credential"],
            "answer_id": answer["answer_id"],
        },
    ).is_error


def _reference(session, task):
    answer = None
    for action in task.evaluation_criteria.actions:
        args = deepcopy(action.arguments)
        if action.name == "verify_identity":
            assert answer is not None, "Reference must obtain the actual client secret"
            args.update(
                credential=answer["data"]["credential"], answer_id=answer["answer_id"]
            )
        result = session.call(action.name, args)
        assert not result.is_error, action.name
        if (
            action.name == "ask_client"
            and args.get("request", {}).get("type") == "identity"
        ):
            answer = _payload(result)


def _business_source(number):
    return "/policies/disputes.md" if number == 56 else "/policies/subscriptions.md"


def _read_business(session, number, variant):
    path = _business_source(number)
    if variant in [
        "source_after_target",
        "source_response_after_target",
        "failed_target_before_source",
    ]:
        if variant == "source_response_after_target":
            attempt = session.begin_call("file_read", {"path": path}, None)
            assert not session.call(
                "file_read", {"path": path}, attempt=attempt
            ).is_error
            return attempt
        return None
    if variant == "partial_source":
        assert session.call("file_read", {"path": path, "end_line": 12}).is_error
    elif variant == "wrong_source":
        assert not session.call("file_read", {"path": "/policies/loans.md"}).is_error
    elif variant == "duplicate_source":
        body = _payload(session.call("file_read", {"path": path}))
        assert body["complete"]
        complete = _payload(session.call("file_read", {"path": path}))
        assert complete["complete"], (
            "Repeated whole-file reads must retain complete source receipts"
        )
    else:
        assert not session.call("file_read", {"path": path}).is_error
    return None


def _canonical(session, task, number, variant):
    owner, transaction = OWNERS[number], TRANSACTIONS[number]
    if variant == "failed_target_before_auth":
        if number == 14:
            result = session.call(
                "ask_client",
                {"customer_id": owner, "request": {"type": "confirm_operations"}},
            )
        else:
            result = session.call(
                "open_dispute",
                {
                    "transaction_id": transaction,
                    "reason": "subscription_after_cancel"
                    if number == 54
                    else "service_not_provided",
                    "amount": AMOUNTS[number],
                },
            )
        assert result.is_error, (
            "The prerequisite violation must be an actual failed protected attempt"
        )
    if variant == "pre_auth_read":
        session.call("get_customer_profile", {"customer_id": owner})
    for path in ["/policies/general.md", "/policies/identity.md"]:
        assert not session.call("file_read", {"path": path}).is_error
    _actual_identity(
        session,
        task,
        owner,
        "birth_date" if variant == "canonical_birth" else "code_word",
    )
    if variant == "early_done":
        return
    if variant == "foreign_read":
        session.call("get_customer_profile", {"customer_id": "petrova_i_4821"})
    if variant == "unknown_error_before_valid":
        assert session.call(
            "get_transaction_details", {"transaction_id": "unknown-read-id"}
        ).is_error
    source_attempt = _read_business(session, number, variant)
    if number == 54:
        # The current fixture includes two received refunds and one pending
        # refund. Only the received amounts reduce the 2,900 RUB purchase.
        assert not session.call("file_read", {"path": "/knowledge/kb_111.md"}).is_error
        assert not session.call("get_documents", {"customer_id": owner}).is_error
        for refund_id in ("txn_314104", "txn_314105", "txn_314106"):
            assert not session.call(
                "get_transaction_details", {"transaction_id": refund_id}
            ).is_error
    if number == 56:
        for path in (
            ["/knowledge/kb_110.md"]
            if variant == "no_navigation_article"
            else ["/knowledge/kb_110.md", "/knowledge/kb_112.md"]
        ):
            assert not session.call("file_read", {"path": path}).is_error
        # No get_accounts: debt does not make a posted-purchase complaint inadmissible.
    if number in [14, 54]:
        assert not session.call("get_subscriptions", {"customer_id": owner}).is_error
    data_transaction = (
        {54: "txn_314102", 56: "txn_661002"}.get(number, transaction)
        if variant == "wrong_transaction"
        else transaction
    )
    if number == 14 and variant == "wrong_transaction":
        data_transaction = (
            "txn_771204"  # Known private product of a different customer.
        )
    if variant == "fact_response_after_question":
        fact_attempt = session.begin_call(
            "get_transaction_details", {"transaction_id": transaction}, None
        )
        assert not session.call(
            "get_transaction_details",
            {"transaction_id": transaction},
            attempt=fact_attempt,
        ).is_error
    else:
        session.call("get_transaction_details", {"transaction_id": data_transaction})
        fact_attempt = None
    if variant == "failed_target_before_source":
        result = session.call(
            "open_dispute",
            {
                "transaction_id": transaction,
                "reason": "unsupported_test_reason",
                "amount": AMOUNTS[number],
            },
        )
        assert result.is_error, (
            "Invalid declared reason must be an actual failed attempt"
        )
        assert not session.call(
            "file_read", {"path": _business_source(number)}
        ).is_error
    if number == 14:
        if variant != "missing_recognition":
            session.call(
                "ask_client",
                {"customer_id": owner, "request": {"type": "confirm_operations"}},
            )
        if fact_attempt:
            session.result_sent(fact_attempt)
    else:
        amount = 10.0 if variant == "wrong_amount" else AMOUNTS[number]
        if variant.startswith("case_"):
            category = {"case_other": "other", "case_escalation": "escalation"}.get(
                variant, "merchant_investigation"
            )
            args = {"customer_id": owner, "category": category, "amount": amount}
            args["transaction_ids" if variant == "case_list" else "transaction_id"] = (
                [transaction] if variant == "case_list" else transaction
            )
            if variant == "case_default_amount":
                args.pop("amount")
            session.call("create_case", args)
        else:
            target = data_transaction if variant == "wrong_transaction" else transaction
            session.call(
                "open_dispute",
                {
                    "transaction_id": target,
                    "reason": "other"
                    if variant == "dispute_other"
                    else (
                        "subscription_after_cancel"
                        if number == 54
                        else "service_not_provided"
                    ),
                    "amount": amount,
                },
            )
    if source_attempt:
        session.result_sent(source_attempt)
    if variant == "source_after_target":
        session.call("file_read", {"path": _business_source(number)})
    if variant == "extra_refund":
        session.call(
            "refund_transaction",
            {"transaction_id": transaction, "amount": AMOUNTS[number]},
        )
    if variant == "extra_cancel":
        session.call(
            "cancel_subscription",
            {"subscription_id": "sub_0771" if number == 14 else "sub_3142"},
        )


@pytest.mark.parametrize(
    "number,variant,expected", CASES, ids=[f"Bank{n}-{v}" for n, v, _ in CASES]
)
def test_whole_contract(number, variant, expected):
    """Actual HALF/FULL graders preserve valid routes and reject material wrong paths."""
    task = deepcopy(next(t for t in get_tasks() if t.id == f"Bank {number}"))
    session = PlatformSession(
        task, get_environment, max_calls=200, max_errors=100, mcp_layout="systems"
    )
    if variant == "reference":
        _reference(session, task)
    elif variant != "noop":
        _canonical(session, task, number, variant)
    assert not session.call("done", {}).is_error
    if variant == "no_navigation_article":
        assert not any(
            e.get("arguments", {}).get("path") == "/knowledge/kb_112.md"
            for e in session.mcp_events
        ), "Navigation article must actually be absent"
    full = grade_full_session(session).reward_info
    assert session._reward_info is None, "FULL must use the original full graders"
    half = session.grade()
    evidence = os.environ.get("SUBSCRIPTION_CONTRACT_EVIDENCE")
    if evidence:
        out = Path(evidence)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"Bank{number}-{variant}.json").write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "variant": variant,
                    "expected": expected,
                    "half": half.model_dump(mode="json"),
                    "full": full.model_dump(mode="json"),
                    "messages": [
                        m.model_dump(mode="json") for m in session.trajectory()
                    ],
                    "mcp_events": session.mcp_events,
                    "errors": session.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert half.final_reward == full.final_reward == expected
    if expected:
        assert session.errors == (1 if variant == "unknown_error_before_valid" else 0)


def _temporary_migration_data(tmp_path):
    import shutil

    from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR

    out = tmp_path / "banking-data"
    shutil.copytree(BANKING_RU_DATA_DIR, out)
    return out


def _run_migration(data):
    import subprocess
    import sys

    script = (
        Path(__file__).resolve().parents[3]
        / "scripts/banking_ru/build_subscription_regulation_contract.py"
    )
    return subprocess.run(
        [sys.executable, str(script), "--data-dir", str(data), "--apply"],
        capture_output=True,
        text=True,
    )


def _without_shared_source_pins(task):
    out = deepcopy(task)
    for item in out["evaluation_criteria"].get("result_evidence", []):
        fields = item.get("expected_fields", {})
        if "path" in fields:
            for name in ["sha256", "content", "snapshot_sha256"]:
                fields.pop(name, None)
    return out


def test_migration_is_idempotent_and_preserves_unassigned_task_contracts(tmp_path):
    """A source migration cannot rewrite other tasks' subject data or safety rules."""
    data = _temporary_migration_data(tmp_path)
    before = json.loads((data / "tasks.json").read_text())
    # Force a stale receipt, exercising actual application rather than check-only.
    for task in before:
        for item in task["evaluation_criteria"].get("result_evidence", []):
            if (
                item.get("expected_fields", {}).get("path")
                == "/policies/subscriptions.md"
            ):
                item["expected_fields"]["sha256"] = "stale-source-pin"
    (data / "tasks.json").write_text(json.dumps(before, ensure_ascii=False))
    result = _run_migration(data)
    assert result.returncode == 0, result.stderr
    after = json.loads((data / "tasks.json").read_text())
    originals = {t["id"]: t for t in before}
    assert {t["id"] for t in after} == set(originals)
    for task in after:
        if task["id"] not in {"Bank 14", "Bank 54", "Bank 56"}:
            assert _without_shared_source_pins(task) == _without_shared_source_pins(
                originals[task["id"]]
            )
    manifest = json.loads((data / "manifest.json").read_text())
    assert "/knowledge/kb_169.md" not in manifest["documents"]
    assert "kb_169" not in json.loads((data / "db.json").read_text())["articles"]
    import hashlib

    for task in after:
        for item in task["evaluation_criteria"].get("result_evidence", []):
            expected = item.get("expected_fields", {})
            path = expected.get("path")
            if path not in manifest["documents"]:
                continue
            body = (data / "corpus" / path.lstrip("/")).read_bytes()
            if "sha256" in expected:
                assert expected["sha256"] == hashlib.sha256(body).hexdigest()
            if "content" in expected:
                assert expected["content"] == body.decode()
    first = {
        p.name: p.read_bytes()
        for p in [data / "tasks.json", data / "db.json", data / "manifest.json"]
    }
    assert _run_migration(data).returncode == 0
    assert first == {
        p.name: p.read_bytes()
        for p in [data / "tasks.json", data / "db.json", data / "manifest.json"]
    }


@pytest.mark.parametrize("fault", ["missing_id", "duplicate_id"])
def test_migration_rejects_task_id_ambiguity_without_partial_writes(tmp_path, fault):
    """The generator must never apply a similar common block to another task."""
    data = _temporary_migration_data(tmp_path)
    tasks = json.loads((data / "tasks.json").read_text())
    if fault == "missing_id":
        tasks = [t for t in tasks if t["id"] != "Bank 54"]
    else:
        tasks.append(deepcopy(next(t for t in tasks if t["id"] == "Bank 54")))
    (data / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False))
    files = [data / "tasks.json", data / "db.json", data / "manifest.json"]
    before = {p.name: p.read_bytes() for p in files}
    assert _run_migration(data).returncode != 0
    assert before == {p.name: p.read_bytes() for p in files}


def test_migration_detects_concurrent_task_edit_before_any_commit(
    tmp_path, monkeypatch
):
    """A concurrently saved other task must survive instead of stale full-file overwrite."""
    import importlib.util
    import sys

    data = _temporary_migration_data(tmp_path)
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts/banking_ru/build_subscription_regulation_contract.py"
    )
    spec = importlib.util.spec_from_file_location("subscription_guard_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_build = module.build
    original_db = (data / "db.json").read_bytes()
    original_manifest = (data / "manifest.json").read_bytes()
    marker = "concurrent independent edit must survive"

    def concurrent_build(path):
        result = original_build(path)
        tasks = json.loads((path / "tasks.json").read_text())
        next(t for t in tasks if t["id"] == "Bank 1")["ticket"] = marker
        (path / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False))
        return result

    monkeypatch.setattr(module, "build", concurrent_build)
    monkeypatch.setattr(sys, "argv", [str(script), "--data-dir", str(data), "--apply"])
    with pytest.raises(RuntimeError, match="Concurrent"):
        module.main()
    tasks = json.loads((data / "tasks.json").read_text())
    assert next(t for t in tasks if t["id"] == "Bank 1")["ticket"] == marker
    assert (data / "db.json").read_bytes() == original_db
    assert (data / "manifest.json").read_bytes() == original_manifest
