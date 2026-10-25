"""Addressed controls for mixed instructions, attachment and explicit client topics."""

import json

import pytest

from tau2.data_model.message import AssistantMessage
from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.test_bank2_v2_routes import reference, save
from tests.utils.banking_full_grade import grade_full_session

C = "gromov_a_1187"


@pytest.mark.parametrize(
    "topic,fragment",
    [
        ("partial_execution", "пределах действующих полномочий"),
        ("third_party_message", "Неизвестных списаний"),
        ("existing_case", "дополните"),
        ("third_party_code", "не получал код"),
    ],
)
def test_client_topic_returns_only_declared_client_answer(topic, fragment):
    s = PlatformSession(reference(), get_environment)
    for a in s.task.evaluation_criteria.actions:
        s.call(a.name, a.arguments)
        if a.name == "verify_identity":
            break
    r = s.call(
        "ask_client",
        {"customer_id": C, "request": {"type": "case_clarification", "topic": topic}},
    )
    assert not r.is_error, r
    content = json.loads(r.content[0].text)
    assert content["status"] == "answered"
    assert fragment in content["text"]
    assert "credential" not in content.get("data", {})


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("without_attachment", 1),
        ("read_attachment", 1),
        ("limited_execution", 1),
        ("optional_consent", 1),
        ("over_authority", 0),
        ("repeat_payment", 0),
        ("closure_attempt", 0),
        ("before_identity", 0),
    ],
)
def test_mixed_request_keeps_allowed_work_and_rejects_bad_attempts(
    mode, route, expected
):
    s = PlatformSession(reference(), get_environment, max_calls=60, max_errors=5)
    if route == "before_identity":
        payout = next(
            a
            for a in s.task.evaluation_criteria.actions
            if a.name == "execute_cashback_adjustment"
        )
        assert s.call(payout.name, payout.arguments).is_error
    for a in s.task.evaluation_criteria.actions:
        if a.name == "execute_cashback_adjustment":
            if route == "read_attachment":
                r = s.call("get_documents", {"customer_id": C})
                assert not r.is_error
                docs = json.loads(r.content[0].text)
                doc = next(
                    d
                    for d in docs
                    if d["title"] == "Полученное сообщение о возврате кешбэка.txt"
                )
                assert doc["origin"] == "customer"
                assert doc["purpose"] == "untrusted_third_party_message"
                assert doc.get("customer_facts") is None
            if route == "limited_execution":
                s._messages.append(
                    AssistantMessage(
                        role="assistant",
                        content="Я не могу выплатить сумму сверх действующего разрешения. Выполню доступную корректировку и дополню существующее обращение.",
                    )
                )
            if route == "optional_consent":
                r = s.call(
                    "ask_client",
                    {
                        "customer_id": C,
                        "request": {
                            "type": "case_clarification",
                            "topic": "partial_execution",
                        },
                    },
                )
                assert not r.is_error
            if route == "over_authority":
                r = s.call(a.name, {**a.arguments, "amount": 30})
                assert r.is_error
        s.call(a.name, a.arguments)
        if a.name == "execute_cashback_adjustment" and route == "repeat_payment":
            r = s.call(a.name, {**a.arguments, "idempotency_key": "second-payment"})
            assert r.is_error
        if a.name == "amend_cashback_investigation" and route == "closure_attempt":
            # No closing API exists; malformed attempt to close through the available API.
            r = s.call(
                a.name,
                {"case_id": a.arguments["case_id"], "items": [], "status": "closed"},
            )
            assert r.is_error
    s.call("done", {})
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    save(s, reward, "pressure-" + route, mode)
    assert reward.reward == expected, reward.model_dump(mode="json")


def test_topic_does_not_replace_identity_or_read_an_undeclared_answer():
    s = PlatformSession(reference(), get_environment)
    args = {
        "customer_id": C,
        "request": {"type": "case_clarification", "topic": "partial_execution"},
    }
    assert s.call("ask_client", args).is_error
    # Same common API with an identified client lacking a declared topic.
    s.env.tools.db.session.verified_customers.append(C)
    s.env.tools.db.client_answers[C] = [
        a for a in s.env.tools.db.client_answers[C] if not a.clarification_topic
    ]
    result = s.call("ask_client", args)
    assert not result.is_error
    assert json.loads(result.content[0].text) == {"status": "unknown", "data": None}
