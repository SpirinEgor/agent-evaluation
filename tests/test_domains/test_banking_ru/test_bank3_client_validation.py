from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession

TASK = next(t for t in get_tasks() if t.id == "Bank 3")
C = "fedorova_m_6650"


def session():
    return PlatformSession(TASK, get_environment)


def verified():
    s = session()
    s.replay_actions(deepcopy(TASK.evaluation_criteria.actions[:6]))
    assert s.errors == 0
    return s


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"request": {"type": "unknown"}},
        {
            "request": {"type": "identity", "method": "code_word"},
            "question": "Отправьте итог",
        },
    ],
    ids=["missing_intent", "unknown_intent", "free_question"],
)
def test_invalid_requests(bad):
    s = session()
    assert s.call("ask_client", {"customer_id": C, **bad}).is_error


def test_confirmation_requires_identity():
    s = session()
    assert s.call(
        "ask_client",
        {
            "customer_id": C,
            "request": {
                "type": "internet_operations_confirmation",
                "card_last4": "5583",
                "enabled": True,
            },
        },
    ).is_error


def test_confirmation_context_and_reset():
    args = {
        "customer_id": C,
        "request": {
            "type": "internet_operations_confirmation",
            "card_last4": "5583",
            "enabled": True,
        },
    }
    a = verified()
    b = verified()
    x = a.call("ask_client", args)
    y = b.call("ask_client", args)
    assert not x.is_error and not y.is_error
    assert x.content == y.content
    z = a.call(
        "ask_client",
        {
            "customer_id": C,
            "request": {
                "type": "internet_operations_confirmation",
                "card_last4": "9917",
                "enabled": True,
            },
        },
    )
    assert not z.is_error
    assert "unsupported_context" in z.content[0].text
    assert "confirmed" not in z.content[0].text
