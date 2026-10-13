"""Typed referrals must preserve subject/linkage and never route by prose."""

import json
from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.helpers import task
from tests.test_domains.test_banking_ru.tool_contract_support import (
    reject,
    succeed,
)

OWNER = "kuznecova_o_3391"
ARGS = {
    "customer_id": OWNER,
    "request": {"type": "card_claim_referral"},
    "case_id": "case_1000",
    "transaction_id": "txn_540117",
}


@pytest.mark.parametrize("route,expected", [("referral", 1), ("wrong_own_case", 0)])
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
    assert half.final_reward == expected


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


def test_missing_linkage_rejected_without_mutation():
    env = setup_referral()
    reject(
        env,
        "escalate_to_human",
        "ERR_INVALID_ARGUMENT",
        customer_id=OWNER,
        request={"type": "card_claim_referral"},
    )


def test_mismatched_own_purchase_rejected_before_any_write():
    env = setup_referral()
    reject(
        env,
        "escalate_to_human",
        "ERR_INVALID_ARGUMENT",
        **{**ARGS, "transaction_id": "txn_552140"},
    )


def test_mcp_schema_requires_request_and_omits_agent_reason():
    tool = next(
        t for t in get_environment().get_tools() if t.name == "escalate_to_human"
    )
    schema = tool.params.model_json_schema()
    assert "request" in schema["required"]
    assert "reason" not in schema["properties"]
    assert schema["properties"]["request"]["discriminator"]["propertyName"] == "type"
