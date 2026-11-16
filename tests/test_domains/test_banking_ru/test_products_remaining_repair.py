"""Whole grader gates for explicitly selected product repairs."""

import copy

import pytest

from tau2.data_model.tasks import Action, RewardType
from tests.test_domains.test_banking_ru.helpers import execute, task
from tests.test_domains.test_banking_ru.tool_contract_support import (
    escalation_arguments,
)
from tests.utils.banking_full_grade import grade_full_session


def route(number, variant):
    actions = copy.deepcopy(task(number).evaluation_criteria.actions)
    if variant == "noop":
        return []
    if variant == "consultation":
        cid = next(
            a.arguments["customer_id"] for a in actions if a.name == "verify_identity"
        )
        actions.append(
            Action(
                action_id="consultation",
                name="escalate_to_human",
                arguments=escalation_arguments(number, cid),
            )
        )
    if variant in ("readiness", "no_readiness"):
        i = next(
            i
            for i, a in enumerate(actions)
            if a.action_id == "closure_operations_Bank 30"
        )
        if variant == "readiness":
            actions[i] = Action(
                action_id=actions[i].action_id,
                name="check_account_closure",
                arguments={"account_id": "acc_9034", "payout_account_id": "acc_1180"},
            )
        else:
            actions.pop(i)
    if variant == "published_retry":
        i = next(i for i, a in enumerate(actions) if a.name == "create_case")
        a = copy.deepcopy(actions[i])
        a.action_id = "first-attempt"
        a.arguments["responsible_unit"] = "Кредитное сопровождение"
        actions.insert(i, a)
    if variant == "metadata":
        a = next(a for a in actions if a.name == "create_case")
        a.arguments.update(responsible_unit="Кредитное сопровождение")
    if variant.startswith("consult_") and variant != "consult_without_calculator":
        actions = route(number, "consultation")
        extra = actions.pop()
        if variant == "consult_wrong_type":
            extra.arguments = {
                "customer_id": extra.arguments["customer_id"],
                "request": {"type": "access_protection"},
            }
        if variant == "consult_foreign":
            extra.arguments["customer_id"] = "belova_n_2201"
        if variant == "consult_failed_early":
            extra.arguments["responsible_unit"] = "unknown"
        if variant in ("consult_early", "consult_failed_early"):
            i = (
                next(i for i, a in enumerate(actions) if a.name == "verify_identity")
                + 1
            )
            actions.insert(i, extra)
        else:
            actions.append(extra)
    if variant == "intermediate_calculation":
        next(a for a in actions if a.name == "calculate").arguments["expression"] = (
            "400000 * 0.14 * 184 / 365"
        )
    if variant in ("without_calculator", "consult_without_calculator"):
        actions = [a for a in actions if a.name != "calculate"]
        if variant == "consult_without_calculator":
            extra = route(number, "consultation")[-1]
            actions.append(extra)
    if variant == "open_product":
        cid = next(
            a.arguments["customer_id"] for a in actions if a.name == "verify_identity"
        )
        actions.append(
            Action(
                action_id="unauthorized_open",
                name="open_deposit",
                arguments={
                    "customer_id": cid,
                    "source_account_id": "acc_2280",
                    "name": "Максимальный",
                    "amount": 400000,
                    "term_months": 6,
                },
            )
        )
    if variant == "unsigned":
        actions = [a for a in actions if a.action_id != "sign_Bank 31"]
        application = next(a for a in actions if a.name == "request_account_closure")
        # The unsigned control has no signature receipt to bind during replay.
        application.action_id = "unsigned_application"
        application.arguments = {
            "account_id": "acc_8891",
            "payout_account_id": "acc_8892",
        }
    if variant == "unknown_route":
        actions = route(number, "published_retry")
        next(a for a in actions if a.action_id == "first-attempt").arguments[
            "responsible_unit"
        ] = "Несуществующее подразделение"
    if variant == "unconfirmed_metadata":
        next(a for a in actions if a.name == "create_case").arguments.update(
            responsible_unit="Кредитное сопровождение",
            next_contact_date="2026-10-19",
            notification_channel="bank_app",
        )
    if variant == "filtered_history":
        next(
            a for a in actions if a.action_id == "closure_operations_Bank 30"
        ).arguments["date_from"] = "2026-08-28"
    return actions


@pytest.mark.parametrize(
    "number,variant,expected",
    [
        (22, "consultation", 1),
        (55, "consultation", 1),
        (30, "no_readiness", 0),
        (39, "published_retry", 1),
        (36, "metadata", 1),
        (55, "open_product", 0),
        (55, "without_calculator", 1),
        (31, "unsigned", 0),
        (36, "unconfirmed_metadata", 0),
        (30, "filtered_history", 0),
    ],
)
def test_whole_product_contract(number, variant, expected):
    session = execute(number, route(number, variant))
    half = session.grade()
    import json
    import os
    import pathlib

    if os.environ.get("PRODUCT_LOG_DIR"):
        p = pathlib.Path(os.environ["PRODUCT_LOG_DIR"])
        p.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "number": number,
                "variant": variant,
                "expected": expected,
                "pid": os.getpid(),
                "session_id": session.token,
                "half": half.model_dump(mode="json"),
                "errors": session.errors,
                "messages": [m.model_dump(mode="json") for m in session.trajectory()],
            },
            ensure_ascii=False,
            indent=2,
        )
        for a in task(number).evaluation_criteria.actions:
            if a.name == "verify_identity":
                payload = payload.replace(a.arguments["credential"], "[MASKED]")
        (p / f"Bank-{number}-{variant}.json").write_text(payload)
    assert half.reward == expected


@pytest.mark.parametrize(
    "contact_date,rejected,expected",
    [
        pytest.param("2026-08-27", True, 1, id="past-date-rejected-then-retried"),
        pytest.param("2026-10-19", False, 0, id="unagreed-date-saved"),
    ],
)
def test_bank39_followup_date(contact_date, rejected, expected):
    actions = route(39, "reference")
    index = next(i for i, action in enumerate(actions) if action.name == "create_case")
    case_action = actions[index]
    dated_action = copy.deepcopy(case_action)
    dated_action.arguments.update(
        responsible_unit="credit_support",
        next_contact_date=contact_date,
        notification_channel="bank_app",
    )
    if rejected:
        dated_action.action_id = "rejected-followup"
        actions.insert(index, dated_action)
    else:
        actions[index] = dated_action

    session = execute(39, actions)
    first, *retries = [
        result for call, result in session.log if call.name == "create_case"
    ]
    assert first.error == rejected
    if rejected:
        assert "Дата следующего сообщения не может быть в прошлом." in first.content
        (retry,) = retries
        assert not retry.error
    else:
        assert not retries

    (case,) = [
        case
        for case in session.env.tools.db.cases.values()
        if case.customer_id == case_action.arguments["customer_id"]
        and case.category == "restructuring"
        and case.loan_id == case_action.arguments["loan_id"]
    ]
    assert case.status == "documents_requested"
    assert case.next_contact_date == (None if rejected else contact_date)

    for reward in (session.grade(), grade_full_session(session).reward_info):
        assert reward.reward_breakdown == {
            RewardType.DB: expected,
            RewardType.ACTION: expected,
        }
        assert reward.reward == expected
        if not rejected:
            assert (
                "bank39:argument:create_case:next_contact_date"
                in reward.info["action"]["forbidden_argument_patterns"]
            )
