"""Bank33 exact closure sources and substantive protected paths, HALF/FULL."""

import copy
import json
import os
from pathlib import Path

import pytest

from tests.test_domains.test_banking_ru.helpers import execute, task
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize(
    "route,expected",
    [
        ("reference", 1),
        ("accounts_only", 1),
        ("kb_only", 1),
        ("noop", 0),
        ("unrelated", 0),
        ("partial", 0),
        ("late", 0),
        ("missing_signature", 0),
        ("missing_readiness", 0),
        ("early_failed_attempt", 0),
    ],
)
def test_bank33_source_repair(route, expected):
    actions = copy.deepcopy(task(33).evaluation_criteria.actions)
    paths = {"/policies/accounts.md", "/knowledge/kb_128.md"}
    if route in {
        "accounts_only",
        "kb_only",
        "unrelated",
        "partial",
        "late",
        "early_failed_attempt",
    }:
        keep = "/knowledge/kb_128.md" if route == "kb_only" else "/policies/accounts.md"
        actions = [
            a
            for a in actions
            if not (a.name == "file_read" and a.arguments.get("path") in paths - {keep})
        ]
        source = next(
            a
            for a in actions
            if a.name == "file_read" and a.arguments.get("path") == keep
        )
        if route == "unrelated":
            source.arguments = {"path": "/policies/cards.md"}
        if route == "partial":
            source.arguments.update(start_line=1, end_line=1)
        if route == "late":
            actions.remove(source)
            actions.append(source)
        if route == "early_failed_attempt":
            early = copy.deepcopy(
                next(a for a in actions if a.name == "request_account_closure")
            )
            early.action_id = "early-failed"
            actions.insert(0, early)
    if route == "noop":
        actions = []
    if route == "missing_signature":
        actions = [a for a in actions if a.action_id != "sign_Bank 33"]
    if route == "missing_readiness":
        actions = [a for a in actions if a.name != "check_account_closure"]
    s = execute(33, actions)
    h = s.grade()
    f = grade_full_session(s).reward_info
    if os.getenv("BANK33_RECEIPTS"):
        p = Path(os.environ["BANK33_RECEIPTS"])
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{route}.json").write_text(
            json.dumps(
                {
                    "route": route,
                    "expected": expected,
                    "pid": os.getpid(),
                    "session": s.token,
                    "half": h.model_dump(mode="json"),
                    "full": f.model_dump(mode="json"),
                    "errors": s.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert h.reward == expected, h.info
    assert f.reward == expected, f.info


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
def test_bank33_exact_receipt(mutation):
    """OR changes the source choice, never provenance or first-attempt timing."""
    import json

    from tau2.domains.banking_ru.environment import get_environment
    from tau2.evaluator.evaluator_action import _result_evidence_calls
    from tau2.evaluator.trace_evidence import evidence_from_messages
    from tau2.platform.session import PlatformSession

    t = task(33)
    s = PlatformSession(t, get_environment)
    sign = next(
        a for a in t.evaluation_criteria.actions if a.action_id == "sign_Bank 33"
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
        and e.before_action_id == "sign_Bank 33"
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


def test_bank33_delayed_receipt():
    from tau2.data_model.message import AssistantMessage, Tick, ToolMessage

    actions = copy.deepcopy(task(33).evaluation_criteria.actions)
    actions = [
        a
        for a in actions
        if not (
            a.name == "file_read" and a.arguments.get("path") == "/knowledge/kb_128.md"
        )
    ]
    s = execute(33, actions)
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
    from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
    from tau2.orchestrator.modes import CommunicationMode

    simulation = grade_full_session(s).simulation.model_copy(update={"ticks": ticks})
    full = evaluate_simulation(
        simulation,
        s.task,
        EvaluationType.ALL,
        True,
        "banking_ru",
        mode=CommunicationMode.FULL_DUPLEX,
        strict_replay=True,
    )
    assert full.reward == 0
