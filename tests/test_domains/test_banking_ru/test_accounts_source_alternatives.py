"""Exact applicable account process sources are alternatives, with unchanged timing."""

import copy

import pytest

from tau2.data_model.tasks import Action
from tests.test_domains.test_banking_ru.helpers import execute, task
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("number", [30, 31])
@pytest.mark.parametrize(
    "variant,expected",
    [
        ("kb_only", 1),
        ("accounts_only", 1),
        ("neither", 0),
        ("late_kb", 0),
        ("failed_kb_then_late_retry", 0),
        ("partial_kb", 0),
    ],
)
def test_exact_source_alternatives_whole(number, variant, expected):
    actions = copy.deepcopy(task(number).evaluation_criteria.actions)
    paths = {"/knowledge/kb_128.md", "/policies/accounts.md"}
    source = (
        "/policies/accounts.md"
        if variant == "accounts_only"
        else "/knowledge/kb_128.md"
    )
    actions = [
        a
        for a in actions
        if not (
            a.name == "file_read"
            and a.arguments.get("path") in paths
            and a.arguments.get("path") != source
        )
    ]
    if variant == "neither":
        actions = [
            a
            for a in actions
            if not (a.name == "file_read" and a.arguments.get("path") in paths)
        ]
    if variant in ["late_kb", "failed_kb_then_late_retry"]:
        a = next(
            a
            for a in actions
            if a.name == "file_read" and a.arguments.get("path") == source
        )
        actions.remove(a)
        i = next(
            i for i, a in enumerate(actions) if a.action_id == f"sign_Bank {number}"
        )
        actions.insert(i + 1, a)
        if variant == "failed_kb_then_late_retry":
            actions.insert(
                i,
                Action(
                    action_id="failed-source",
                    name="file_read",
                    arguments={"path": source, "start_line": 201, "end_line": 201},
                ),
            )
    if variant == "partial_kb":
        next(
            a
            for a in actions
            if a.name == "file_read" and a.arguments.get("path") == source
        ).arguments.update(start_line=1, end_line=1)
    if number == 30:
        i = next(
            i
            for i, a in enumerate(actions)
            if a.action_id == "closure_operations_Bank 30"
        )
        actions[i] = Action(
            action_id=actions[i].action_id,
            name="check_account_closure",
            arguments={"account_id": "acc_9034", "payout_account_id": "acc_1180"},
        )
    s = execute(number, actions)
    half = s.grade()
    full = grade_full_session(s).reward_info
    import json
    import os
    import pathlib

    if os.environ.get("SOURCE_OR_LOG_DIR"):
        p = pathlib.Path(os.environ["SOURCE_OR_LOG_DIR"])
        p.mkdir(exist_ok=True, parents=True)
        payload = json.dumps(
            {
                "number": number,
                "variant": variant,
                "expected": expected,
                "pid": os.getpid(),
                "session_id": s.token,
                "half": half.model_dump(mode="json"),
                "full": full.model_dump(mode="json"),
                "errors": s.errors,
                "messages": [m.model_dump(mode="json") for m in s.trajectory()],
            },
            ensure_ascii=False,
            indent=2,
        )
        for a in actions:
            if a.name == "verify_identity":
                payload = payload.replace(a.arguments["credential"], "[MASKED]")
        (p / f"Bank-{number}-{variant}.json").write_text(payload)
    assert half.reward == expected
    assert full.reward == expected


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "hash",
        "body",
        "incomplete",
        "error",
        "id",
        "role",
        "late",
        "earlier_failed_target",
    ],
)
def test_source_or_keeps_exact_receipt_and_first_attempt(mutation):
    """OR changes the source choice, never provenance or first-attempt timing."""
    import json

    from tau2.domains.banking_ru.environment import get_environment
    from tau2.evaluator.evaluator_action import _result_evidence_calls
    from tau2.evaluator.trace_evidence import evidence_from_messages
    from tau2.platform.session import PlatformSession

    t = task(31)
    s = PlatformSession(t, get_environment)
    sign = next(
        a for a in t.evaluation_criteria.actions if a.action_id == "sign_Bank 31"
    )
    if mutation == "earlier_failed_target":
        s.call(sign.name, sign.arguments)
    s.call("file_read", {"path": "/policies/accounts.md"})
    s.call(sign.name, sign.arguments)
    observed = evidence_from_messages(s.trajectory()).observed
    evidence = next(
        e
        for e in t.evaluation_criteria.result_evidence
        if e.expected_fields.get("path") == "/knowledge/kb_128.md"
        and e.before_action_id == "sign_Bank 31"
    )
    source = next(i for i, x in enumerate(observed) if x.call.name == "file_read")
    item = observed[source]
    result = item.result
    if mutation in ["hash", "body", "incomplete"]:
        payload = json.loads(result.content)
        # actual file-read wrapper is the alternative exact receipt
        key = {"hash": "sha256", "body": "content", "incomplete": "complete"}[mutation]
        payload[key] = False if mutation == "incomplete" else "wrong"
        result = result.model_copy(
            update={"content": json.dumps(payload, ensure_ascii=False)}
        )
    if mutation == "error":
        result = result.model_copy(update={"error": True})
    if mutation == "id":
        result = result.model_copy(update={"id": "unrelated-result"})
    if mutation == "role":
        result = result.model_copy(update={"requestor": "user"})
    observed[source] = item.model_copy(
        update={
            "result": result,
            "result_position": 999 if mutation == "late" else item.result_position,
        }
    )
    assert bool(
        _result_evidence_calls(observed, t.evaluation_criteria.actions, evidence)
    ) is (mutation == "none")


def test_accounts_alternative_full_received_after_signature_formation_is_zero():
    from tau2.data_model.message import AssistantMessage, Tick, ToolMessage

    actions = copy.deepcopy(task(31).evaluation_criteria.actions)
    actions = [
        a
        for a in actions
        if not (
            a.name == "file_read" and a.arguments.get("path") == "/knowledge/kb_128.md"
        )
    ]
    source = next(
        a
        for a in actions
        if a.name == "file_read" and a.arguments.get("path") == "/policies/accounts.md"
    )
    actions.remove(source)
    i = next(i for i, a in enumerate(actions) if a.action_id == "sign_Bank 31")
    actions.insert(i, source)
    s = execute(31, actions)
    assert s.grade().reward == 1
    calls = {
        c.id: c
        for m in s.trajectory()
        if isinstance(m, AssistantMessage)
        for c in m.tool_calls or []
    }
    source_id = next(
        c.id
        for c in calls.values()
        if c.name == "file_read" and c.arguments["path"] == "/policies/accounts.md"
    )
    ticks = []
    pending = None
    for i, m in enumerate(s.trajectory()):
        if isinstance(m, ToolMessage) and m.id == source_id:
            pending = m
            continue
        results = []
        if isinstance(m, AssistantMessage) and any(
            c.name == "ask_client"
            and c.arguments.get("request", {}).get("type")
            == "account_closure_signature"
            for c in m.tool_calls or []
        ):
            assert pending is not None
            results = [pending]
            pending = None
        elif isinstance(m, ToolMessage):
            results = [m]
        ticks.append(
            Tick(
                tick_id=i,
                timestamp=s.created_at.isoformat(),
                agent_chunk=m if isinstance(m, AssistantMessage) else None,
                agent_tool_calls=m.tool_calls or []
                if isinstance(m, AssistantMessage)
                else [],
                agent_tool_results=results,
            )
        )
    assert pending is None
    full = grade_full_session(s, ticks=ticks).reward_info
    assert full.reward == 0
