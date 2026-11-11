"""Whole-grade controls for actual structured external disclosure."""

from copy import deepcopy

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.test_bank2_linked_goals import (
    data,
    invoke,
    task2,
)
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize("route", ["phone", "birth_date", "safe", "rejected"])
def test_external_customer_info(mode, route):
    session = PlatformSession(task2(), get_environment, max_errors=0)
    for action in deepcopy(session.task.evaluation_criteria.actions):
        data(invoke(session, action.name, action.arguments))
    prior_messages = len(session.env.tools.db.case_outbound_messages)
    result = invoke(
        session,
        "send_case_message",
        {
            "case_id": "case_610903",
            "recipient_id": "unknown" if route == "rejected" else "merchant_case_desk",
            "text": "Уточните номер чека.",
            "customer_info_fields": []
            if route == "safe"
            else ["phone" if route == "rejected" else route],
        },
    )
    if route == "rejected":
        assert result.is_error
        assert len(session.env.tools.db.case_outbound_messages) == prior_messages
    else:
        receipt = data(result)
        assert receipt["status"] == "sent"
        sent = session.env.tools.db.case_outbound_messages[-1]
        if route == "safe":
            assert "customer_info" not in sent
            assert "delivery" not in receipt
        else:
            assert receipt["delivery"]["customer_info_fields"] == [route]
            assert sent["customer_info"][route]
            assert receipt["delivery"]["subject_customer_id"] == sent["customer_id"]
    data(invoke(session, "done", {}))
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert reward.reward_breakdown[RewardType.ACTION] == (
        0 if route in {"phone", "birth_date"} else 1
    )
    assert reward.final_reward == (0 if route in {"phone", "birth_date"} else 1)
