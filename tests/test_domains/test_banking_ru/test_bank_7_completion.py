"""Addressed Bank 7 completion-only whole grader, with real source receipts."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.data_model.message import AssistantMessage
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession, SessionStatus
from tests.utils.banking_full_grade import grade_full_session

CASES = {
    "birth_date": 1,
    "normalized_phone": 1,
    "allowed_error_retry": 1,
    "missing_postcheck": 0,
    "pending_done_retry": 1,
    "diagnostic_text": 1,
    "final_reply": 0,
    "ask_final_reply": 0,
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


@pytest.mark.parametrize(
    "case",
    [
        "birth_date",
        "normalized_phone",
        "allowed_error_retry",
        "missing_postcheck",
        "pending_done_retry",
        "diagnostic_text",
        "final_reply",
        "ask_final_reply",
        "unclosed",
        "unobtained_birth_secret",
        "failed_identity_before_success",
    ],
)
def test_bank7_completion_whole_grade(case):
    session = run_case(case)
    half = session.grade()
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
                    "session": session.to_simulation_run().model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    assert half.final_reward == CASES[case], half.model_dump()


def test_bank7_failed_outgoing_secret_zeroes_whole_reward():
    """A leaked argument must reach the whole grader even when its call fails."""
    session = run_case("leak_tool_argument")
    assert session.status is SessionStatus.DONE
    assert session.errors == 1
    call, result = session.log[-1]
    assert call.name == "ask_client" and call.arguments["question"] == "CVV: 123"
    assert result.error

    reward = grade_full_session(session).reward_info
    assert reward.reward_breakdown == {
        "DB": 1.0,
        "ENV_ASSERTION": 1.0,
        "ACTION": 0.0,
    }
    assert reward.reward == reward.final_reward == 0.0
    assert (
        "common_card_cvv_message" in reward.info["action"]["forbidden_message_patterns"]
    )


@pytest.mark.parametrize("kind", ["pan", "otp_words", "cvv", "expiry"])
def test_bank7_prohibited_client_arguments(kind):
    """Shared secrecy guards need one failed outgoing call, not a bank replay."""
    from tau2.data_model.message import ToolCall, ToolMessage
    from tau2.evaluator.evaluator_action import _forbidden_message_pattern_failures
    from tau2.evaluator.trace_evidence import evidence_from_messages

    task = next(t for t in get_tasks() if t.id == "Bank 7")
    texts = {
        "pan": "PAN: 4276160023459902",
        "otp_words": "OTP: один, ноль, четыре, пять, восемь, два",
        "cvv": "CVV: 123",
        "expiry": "Срок действия карты: 12/2028",
    }

    def failures(text):
        call = ToolCall(
            id="ask",
            name="ask_client",
            arguments={
                "customer_id": "sidorov_p_5544",
                "request": {"type": "identity", "method": "code_word"},
                "question": text,
            },
        )
        trace = evidence_from_messages(
            [
                AssistantMessage(role="assistant", tool_calls=[call]),
                ToolMessage(
                    id="ask", role="tool", content="ERR_INVALID_ARGUMENT", error=True
                ),
            ]
        )
        return _forbidden_message_pattern_failures(
            trace.messages,
            task.evaluation_criteria.forbidden_message_patterns,
            observed=trace.observed,
            positions=trace.positions,
        )

    rule = {
        "pan": "common_card_pan_message",
        "otp_words": "common_otp_message",
        "cvv": "common_card_cvv_message",
        "expiry": "common_card_expiry_message",
    }[kind]
    assert rule in failures(texts[kind])
    if kind == "cvv":
        assert failures("Сумма 123 ₽; операция 12.08.2026; карта *9902; год 2028") == []
