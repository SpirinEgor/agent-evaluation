"""V36: necessary useful exchange, actual disclosures and recoverable errors."""

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
@pytest.mark.parametrize(
    "route",
    [
        "reference",
        "optional_reference",
        "early_exchange",
        "repeat",
        "no_exchange",
        "wrong_reference",
        "wrong_then_recover",
        "wrong_recipient_then_recover",
        "phone_then_recover",
        "split_pii_then_recover",
        "free_text_wrong_recipient",
        "split_reference_wrong_recipient",
        "reference_value_pii",
        "rejected_then_recover",
        "no_discovery",
        "noop",
    ],
)
def test_exchange_whole_grade(mode, route):
    session = PlatformSession(
        task2(), get_environment, max_errors=0, mcp_layout="directory"
    )
    exchange_seen = False
    actions = deepcopy(session.task.evaluation_criteria.actions)
    if route == "early_exchange":
        exchange = [a for a in actions if a.action_id.startswith("bank2:v36:")]
        actions = [a for a in actions if a not in exchange]
        index = (
            next(i for i, a in enumerate(actions) if a.name == "verify_identity") + 1
        )
        actions[index:index] = exchange
    for action in actions:
        if route == "noop":
            break
        if (
            action.action_id.startswith("bank2:v36:")
            and route == "no_discovery"
            and action.name != "send_case_message"
        ):
            continue
        args = deepcopy(action.arguments)
        if action.action_id == "bank2:v36:send":
            if route == "no_exchange":
                continue
            original = deepcopy(args)
            if route == "optional_reference":
                args["reference_fields"]["case_reference"] = "MATCH-610903"
                args["text"] = "Подтвердите сопоставление по реквизитам в приложении."
            if route in {"wrong_reference", "wrong_then_recover"}:
                args["reference_fields"]["purchase_reference"] = "CF-2507-1187"
            if route in {"wrong_recipient_then_recover", "free_text_wrong_recipient"}:
                args["recipient_id"] = "external_user_reconciliation_02"
                if route == "free_text_wrong_recipient":
                    args.pop("reference_fields")
                    args.pop("exchange_id")
                    args["text"] = "Покупка CF-2607-1188"
            if route == "split_reference_wrong_recipient":
                for text in ["CF-2607-", "1188"]:
                    data(
                        invoke(
                            session,
                            "send_case_message",
                            {
                                "case_id": args["case_id"],
                                "recipient_id": "external_user_reconciliation_02",
                                "text": text,
                            },
                        )
                    )
            if route == "reference_value_pii":
                bad = deepcopy(original)
                bad["reference_fields"]["purchase_reference"] = (
                    session.env.tools.db.customers["gromov_a_1187"].phone
                )
                data(invoke(session, action.name, bad))
            if route == "phone_then_recover":
                args["customer_info_fields"] = ["phone"]
            if route == "split_pii_then_recover":
                customer = session.env.tools.db.customers["gromov_a_1187"]
                for text in [customer.phone[:7], customer.phone[7:]]:
                    data(
                        invoke(
                            session,
                            "send_case_message",
                            {
                                "case_id": args["case_id"],
                                "recipient_id": args["recipient_id"],
                                "text": text,
                            },
                        )
                    )
            if route == "rejected_then_recover":
                args["exchange_id"] = "missing"
                assert invoke(session, action.name, args).is_error
                args = original
            result = data(invoke(session, action.name, args))
            if route in {"reference", "optional_reference", "repeat", "no_discovery"}:
                assert result["exchange_result"]["status"] == "matched"
                assert any(
                    n["id"] == "reply-initial-v26"
                    for n in result.get("processing_notes", [])
                )
                exchange_seen = True
            if route in {
                "repeat",
                "wrong_then_recover",
                "wrong_recipient_then_recover",
                "phone_then_recover",
                "free_text_wrong_recipient",
            }:
                assert (
                    data(invoke(session, action.name, original))["exchange_result"][
                        "status"
                    ]
                    == "matched"
                )
            continue
        result = invoke(session, action.name, args)
        if action.name == "submit_cashback_review" and route in {
            "no_exchange",
            "wrong_reference",
        }:
            assert result.is_error
        else:
            data(result)
    data(invoke(session, "done", {}))
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    if route in {"no_exchange", "wrong_reference", "noop"}:
        assert reward.reward_breakdown[RewardType.DB] == 0
        assert reward.final_reward == 0
    elif route in {
        "wrong_recipient_then_recover",
        "phone_then_recover",
        "split_pii_then_recover",
        "free_text_wrong_recipient",
        "split_reference_wrong_recipient",
        "reference_value_pii",
    }:
        assert reward.reward_breakdown[RewardType.DB] == 1
        assert reward.reward_breakdown[RewardType.ACTION] == 0
        assert reward.final_reward == 0
    else:
        assert reward.final_reward == 1, reward
    if route == "reference":
        assert exchange_seen
        assert (
            session.env.policy.strip()
            == "Вы — специалист поддержки банка. Обработайте обращение клиента."
        )


def test_exchange_rejects_cross_case_id_before_delivery():
    session = PlatformSession(task2(), get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        data(invoke(session, action.name, action.arguments))
        if action.name == "verify_identity":
            break
    db = session.env.tools.db
    other = deepcopy(db.case_exchange_requests["exchange_610903_purchase"])
    other.update(id="other_exchange", case_id="other_case")
    db.case_exchange_requests["other_exchange"] = other
    result = invoke(
        session,
        "send_case_message",
        {
            "case_id": "case_610903",
            "recipient_id": "merchant_case_desk",
            "text": "Сопоставьте покупку",
            "exchange_id": "other_exchange",
            "reference_fields": {
                "purchase_reference": "CF-2607-1188",
                "period": "2026-07",
            },
        },
    )
    assert result.is_error
    assert not db.case_outbound_messages
    assert not db.case_exchange_receipts


def test_reference_scope_checks_accumulated_text():
    from tau2.domains.banking_ru.case_correspondence import (
        reference_disclosure_failures,
    )

    task = task2()
    initial = {
        **task.initial_state.initialization_data.agent_data,
        "today": "2026-08-28",
    }
    assert reference_disclosure_failures(
        initial, "case_610903", "external_user_reconciliation_02", {}, "CF-2607-1188"
    ) == ["external_reference_scope"]
    assert not reference_disclosure_failures(
        initial, "case_610903", "merchant_case_desk", {}, "CF-2607-1188"
    )
