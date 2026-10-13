"""Typed referrals must preserve subject/linkage and never route by prose."""

import json
from copy import deepcopy

import pytest

from tau2.data_model.message import ToolCall
from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.helpers import task
from tests.test_domains.test_banking_ru.tool_contract_support import (
    reject,
    snapshot,
    succeed,
)
from tests.utils.banking_full_grade import grade_full_session

OWNER = "kuznecova_o_3391"
ARGS = {
    "customer_id": OWNER,
    "request": {"type": "card_claim_referral"},
    "case_id": "case_1000",
    "transaction_id": "txn_540117",
}


@pytest.mark.parametrize(
    "route,expected",
    [
        ("reference", 1),
        ("noop", 0),
        ("referral", 1),
        ("birth_date", 1),
        ("wrong_type", 0),
        ("foreign_transaction", 0),
        ("missing_identity", 0),
        ("early_attempt", 0),
        ("duplicate", 0),
        ("wrong_own_case", 0),
        ("wrong_own_transaction", 0),
        ("unknown_retry", 1),
        ("wrong_unit", 0),
        ("legacy_reason", 0),
    ],
)
def test_bank12_structured_route(route, expected):
    s = PlatformSession(task(12), get_environment, max_calls=250, max_errors=100)
    actions = deepcopy(task(12).evaluation_criteria.actions)
    if route == "noop":
        actions = []
    if route == "missing_identity":
        actions = [a for a in actions if a.name != "verify_identity"]
    if route == "early_attempt":
        cut = next(i for i, a in enumerate(actions) if a.name == "verify_identity") + 1
        s.replay_actions(actions[:cut])
        s.call("escalate_to_human", deepcopy(ARGS))
        actions = actions[cut:]
    if route == "birth_date":
        answer = None
        for action in actions:
            args = deepcopy(action.arguments)
            if action.name == "ask_client":
                args["request"]["method"] = "birth_date"
            if action.name == "verify_identity":
                assert answer is not None
                args.update(
                    credential=answer["data"]["credential"],
                    answer_id=answer["answer_id"],
                )
            response = s.call(action.name, args)
            if action.name == "ask_client" and not response.is_error:
                answer = json.loads(s.log[-1][1].content)
    else:
        s.replay_actions(actions)
    if route not in ["reference", "noop"]:
        args = deepcopy(ARGS)
        if route == "wrong_type":
            args = {
                "customer_id": OWNER,
                "request": {
                    "type": "deposit_consultation",
                    "amount": 400000,
                    "currency": "RUB",
                },
            }
        if route == "foreign_transaction":
            args["transaction_id"] = "txn_774411"
        if route == "wrong_own_transaction":
            args["transaction_id"] = "txn_552140"
        if route == "wrong_unit":
            args["responsible_unit"] = "investment_products"
        if route == "legacy_reason":
            args["reason"] = "Любой текст"
        if route == "unknown_retry":
            s.call("escalate_to_human", {**ARGS, "case_id": "case_unknown"})
        if route == "wrong_own_case":
            s.call(
                "create_case",
                {
                    "customer_id": OWNER,
                    "category": "other",
                    "transaction_id": "txn_540117",
                    "amount": 7450,
                },
            )
            args["case_id"] = "case_1001"
        s.call("escalate_to_human", args)
        if route == "duplicate":
            s.call("escalate_to_human", args)
    s.call("done", {})
    half = s.grade()
    full = grade_full_session(s).reward_info
    assert half.final_reward == full.final_reward == expected


def setup_referral():
    env = get_environment()
    answer = succeed(
        env,
        "ask_client",
        customer_id=OWNER,
        request={"type": "identity", "method": "code_word"},
    )
    succeed(
        env,
        "verify_identity",
        customer_id=OWNER,
        credential=answer["data"]["credential"],
        answer_id=answer["answer_id"],
    )
    succeed(
        env,
        "create_case",
        customer_id=OWNER,
        category="merchant_investigation",
        transaction_id="txn_540117",
        amount=7450,
    )
    return env


def test_typed_referral_records_structured_subject():
    env = setup_referral()
    result = succeed(env, "escalate_to_human", **ARGS)
    assert result["escalation_request"] == {"type": "card_claim_referral"}
    assert result["case_id"] == "case_1000"
    assert result["transaction_id"] == "txn_540117"
    assert result["responsible_unit"] == "support"


@pytest.mark.parametrize(
    "extra",
    [
        {"reason": "Проверка карточной претензии специалистом"},
        {"reason": "Любая другая формулировка"},
        {"request": {"type": "card_claim_referral", "reason": "Произвольный текст"}},
        {"request": {"type": "not_a_referral"}},
    ],
)
def test_free_text_and_unknown_intent_rejected_without_mutation(extra):
    env = setup_referral()
    args = {**ARGS, **extra}
    before = snapshot(env)
    response = env.get_response(
        ToolCall(id="test", name="escalate_to_human", arguments=args)
    )
    assert response.error
    assert snapshot(env) == before


def test_missing_request_rejected_without_mutation():
    env = setup_referral()
    response = env.get_response(
        ToolCall(
            id="test",
            name="escalate_to_human",
            arguments={"customer_id": OWNER, "reason": "review"},
        )
    )
    assert response.error


def test_missing_linkage_rejected_without_mutation():
    env = setup_referral()
    reject(
        env,
        "escalate_to_human",
        "ERR_INVALID_ARGUMENT",
        customer_id=OWNER,
        request={"type": "card_claim_referral"},
    )


def test_investment_request_uses_same_type_discriminator():
    env = get_environment()
    owner = "grigoreva_e_4408"
    answer = succeed(
        env,
        "ask_client",
        customer_id=owner,
        request={"type": "identity", "method": "code_word"},
    )
    succeed(
        env,
        "verify_identity",
        customer_id=owner,
        credential=answer["data"]["credential"],
        answer_id=answer["answer_id"],
    )
    result = succeed(
        env,
        "escalate_to_human",
        customer_id=owner,
        request={
            "type": "investment_selection",
            "instrument_type": "stocks",
            "available_amount": 400000,
            "currency": "RUB",
            "experience": "none",
        },
    )
    assert result["responsible_unit"] == "investment_products"
    assert result["investment_consultation"] == {
        "topic": "investment_selection",
        "instrument_type": "stocks",
        "available_amount": 400000.0,
        "currency": "RUB",
        "experience": "none",
    }


def test_foreign_case_rejected_before_any_write():
    from tau2.domains.banking_ru.data_model import Case

    env = setup_referral()
    env.tools.db.cases["case_foreign"] = Case(
        id="case_foreign",
        customer_id="belova_n_2201",
        category="merchant_investigation",
        transaction_id="txn_774411",
        amount=8400,
        created_at=env.tools.db.today,
        status="open",
    )
    reject(
        env,
        "escalate_to_human",
        "ERR_INVALID_ARGUMENT",
        **{**ARGS, "case_id": "case_foreign"},
    )


def test_mismatched_own_purchase_rejected_before_any_write():
    env = setup_referral()
    reject(
        env,
        "escalate_to_human",
        "ERR_INVALID_ARGUMENT",
        **{**ARGS, "transaction_id": "txn_552140"},
    )


def test_incompatible_destination_rejected_before_any_write():
    env = setup_referral()
    reject(
        env,
        "escalate_to_human",
        "ERR_INVALID_ARGUMENT",
        **ARGS,
        responsible_unit="investment_products",
    )


def test_mcp_schema_requires_request_and_omits_agent_reason():
    tool = next(
        t for t in get_environment().get_tools() if t.name == "escalate_to_human"
    )
    schema = tool.params.model_json_schema()
    assert "request" in schema["required"]
    assert "reason" not in schema["properties"]
    assert schema["properties"]["request"]["discriminator"]["propertyName"] == "type"
