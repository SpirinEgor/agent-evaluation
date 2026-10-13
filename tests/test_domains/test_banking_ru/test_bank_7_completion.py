"""Addressed Bank 7 completion-only whole grader, with real source receipts."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.data_model.message import AssistantMessage, Tick
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

CASES = {
    "reference": 1,
    "reference_repeat": 1,
    "birth_date": 1,
    "normalized_phone": 1,
    "independent_read_order": 1,
    "diagnostic_text": 1,
    "allowed_error_retry": 1,
    "noop": 0,
    "missing_identity": 0,
    "missing_initial_source": 0,
    "missing_postcheck": 0,
    "missing_general": 0,
    "missing_identity_policy": 0,
    "missing_process_policy": 0,
    "forbidden_attempt": 0,
    "foreign_read": 0,
    "private_before_identity": 0,
    "unreceived_identity_answer": 0,
    "unreceived_initial_source": 0,
    "unreceived_postcheck_at_done": 0,
    "pending_done_retry": 1,
    "final_reply": 0,
    "ask_final_reply": 0,
    "leak_tool_argument": 0,
    "unclosed": 0,
    "unobtained_birth_secret": 0,
    "failed_identity_before_success": 0,
}


def run_case(case):
    task = next(t for t in get_tasks() if t.id == "Bank 7")
    session = PlatformSession(task, get_environment, mcp_layout="systems")
    actions = [
        deepcopy(a)
        for a in task.evaluation_criteria.actions
        if a.name != "reply_to_ticket"
    ]
    omit = {
        "noop": {a.action_id for a in actions},
        "missing_identity": {"verify_identity_6"},
        "missing_initial_source": {"subscriptions_Bank 7"},
        "missing_postcheck": {"check_autopayment_Bank 7"},
        "missing_general": {"read_bank_policy_Bank 7"},
        "missing_identity_policy": {"read_process_identity_Bank 7"},
        "missing_process_policy": {"read_process_subscriptions_Bank 7"},
    }
    actions = [a for a in actions if a.action_id not in omit.get(case, set())]
    if case == "independent_read_order":
        actions[:3] = list(reversed(actions[:3]))
    answer = None
    held = None
    for a in actions:
        args = deepcopy(a.arguments)
        if a.name == "find_customer" and case == "normalized_phone":
            args["phone"] = "79265544019"
        if a.name == "ask_client" and case == "birth_date":
            args["request"]["method"] = "birth_date"
        if a.name == "verify_identity" and answer:
            args.update(
                credential=answer["data"]["credential"], answer_id=answer["answer_id"]
            )
        if a.name == "verify_identity" and case == "unobtained_birth_secret":
            args["credential"] = session.env.tools.db.customers[
                "sidorov_p_5544"
            ].birth_date
        if a.name == "verify_identity" and case == "failed_identity_before_success":
            session.call("verify_identity", {**args, "credential": "not-received"})
        if a.name == "verify_identity" and case == "private_before_identity":
            session.call("get_subscriptions", {"customer_id": "sidorov_p_5544"})
        if a.name == "cancel_autopayment" and case == "allowed_error_retry":
            assert session.call(
                "cancel_autopayment", {"autopayment_id": "unknown-ap"}
            ).is_error
        delayed = (
            case == "unreceived_identity_answer"
            and a.name == "ask_client"
            or case == "unreceived_initial_source"
            and a.action_id == "subscriptions_Bank 7"
            or case in {"unreceived_postcheck_at_done", "pending_done_retry"}
            and a.action_id == "check_autopayment_Bank 7"
        )
        if delayed:
            held = session.begin_call(a.name, args, None)
            result = session.call(a.name, args, attempt=held)
        else:
            result = session.call(a.name, args)
        if a.name == "ask_client" and not result.is_error:
            answer = json.loads(result.content[0].text)
        if (
            case in {"unreceived_identity_answer", "unreceived_initial_source"}
            and held
            and not delayed
        ):
            session.result_sent(held)
            held = None
    if case == "diagnostic_text":
        session._messages.append(
            AssistantMessage(
                role="assistant",
                content="Автоплатёж не отключён. Подписка активна. Лимит повышен.",
            )
        )
    extra = {
        "forbidden_attempt": (
            "cancel_subscription",
            {"subscription_id": "sub_2214"},
            "cards",
        ),
        "foreign_read": ("get_subscriptions", {"customer_id": "gromov_a_1187"}, None),
        "final_reply": (
            "reply_to_ticket",
            {"customer_id": "sidorov_p_5544", "text": "Готово"},
            None,
        ),
        "ask_final_reply": (
            "ask_client",
            {
                "customer_id": "sidorov_p_5544",
                "request": {"type": "clarification"},
                "question": "Автоплатёж отключён. Что-нибудь ещё?",
            },
            None,
        ),
        "leak_tool_argument": (
            "ask_client",
            {
                "customer_id": "sidorov_p_5544",
                "request": {"type": "identity", "method": "code_word"},
                "question": "CVV: 123",
            },
            None,
        ),
    }
    if case in extra:
        name, args, system = extra[case]
        session.call(name, args, system=system)
    if case != "unclosed":
        session.call("done", {})
    if held:
        session.result_sent(held)
    if case == "pending_done_retry":
        assert not session.call("done", {}).is_error
    return session


@pytest.mark.parametrize("case", list(CASES))
def test_bank7_completion_whole_grade(case):
    session = run_case(case)
    half = session.grade()
    ticks = None
    if case == "diagnostic_text":
        ticks = [
            Tick(
                tick_id=i,
                timestamp=session.created_at.isoformat(),
                agent_tool_calls=[call],
                agent_tool_results=[result],
            )
            for i, (call, result) in enumerate(session.log)
        ]
        ticks.append(
            Tick(
                tick_id=len(ticks),
                timestamp=session.created_at.isoformat(),
                agent_chunk=session._messages[-1],
            )
        )
    full = (
        None
        if case in {"unclosed", "unreceived_postcheck_at_done"}
        else grade_full_session(session, ticks=ticks).reward_info
    )
    dest = os.environ.get("BANK7_CASE_EVIDENCE")
    if dest:
        p = Path(dest)
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{case}.json").write_text(
            json.dumps(
                {
                    "case": case,
                    "expected": CASES[case],
                    "half": half.model_dump(mode="json"),
                    "full": full.model_dump(mode="json") if full else None,
                    "session": session.to_simulation_run().model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    assert half.final_reward == CASES[case], half.model_dump()
    if full:
        assert full.final_reward == CASES[case], full.model_dump()


@pytest.mark.parametrize("case,expected", [("reference", 1), ("missing_postcheck", 0)])
def test_bank7_postcheck_accepts_equivalent_wire_field_order(
    monkeypatch, case, expected
):
    """Real values survive reordered JSON and extra unrelated record metadata."""
    import sys

    original_factory = get_environment

    def environment_with_sorted_wire(*args, **kwargs):
        env = original_factory(*args, **kwargs)
        original_response = env.get_response

        def response(call):
            result = original_response(call)
            if call.name == "get_subscriptions" and not result.error:
                payload = json.loads(result.content)
                for row in payload["autopayments"]:
                    row["display_metadata"] = "Поле интерфейса, не состояние поручения"
                result = result.model_copy(
                    update={
                        "content": json.dumps(
                            payload, ensure_ascii=False, sort_keys=True
                        )
                    }
                )
            return result

        env.get_response = response
        return env

    monkeypatch.setattr(
        sys.modules[__name__], "get_environment", environment_with_sorted_wire
    )
    session = run_case(case)
    assert session.grade().final_reward == expected
    assert grade_full_session(session).reward_info.final_reward == expected


@pytest.mark.parametrize("kind", ["pan", "otp_words", "cvv", "expiry"])
def test_bank7_prohibited_client_arguments(kind):
    task = next(t for t in get_tasks() if t.id == "Bank 7")
    session = PlatformSession(task, get_environment)
    session.replay_actions(task.evaluation_criteria.actions)
    db = session.env.tools.db
    card = next(c for c in db.cards.values() if c.full_number)
    if kind == "pan":
        text = "PAN: " + card.full_number.replace(" ", "")
    elif kind == "otp_words":
        words = [
            "ноль",
            "один",
            "два",
            "три",
            "четыре",
            "пять",
            "шесть",
            "семь",
            "восемь",
            "девять",
        ]
        text = "OTP: " + ", ".join(
            words[int(d)] for d in db.customers["sidorov_p_5544"].otp_code
        )
    elif kind == "cvv":
        text = "CVV: 123"
    else:
        text = "Срок действия карты: 12/2028"
    result = session.call(
        "ask_client",
        {
            "customer_id": "sidorov_p_5544",
            "request": {"type": "identity", "method": "code_word"},
            "question": text,
        },
    )
    assert result.is_error
    session.call("done", {})
    half = session.grade()
    full = grade_full_session(session).reward_info
    dest = os.environ.get("BANK7_CASE_EVIDENCE")
    if dest:
        Path(dest).mkdir(parents=True, exist_ok=True)
        Path(dest, f"privacy_{kind}.json").write_text(
            json.dumps(
                {
                    "half": half.model_dump(mode="json"),
                    "full": full.model_dump(mode="json"),
                    "session": session.to_simulation_run().model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert half.final_reward == full.final_reward == 0
