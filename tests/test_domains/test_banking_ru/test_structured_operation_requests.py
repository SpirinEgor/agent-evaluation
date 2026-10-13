"""Operation grounds are categories, never narrative strings."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.environment import get_environment
from tests.test_domains.test_banking_ru.finance_tool_contracts import seed_ledger
from tests.test_domains.test_banking_ru.tool_contract_support import succeed


def identify(env, owner):
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


@pytest.mark.parametrize(
    "name,kind",
    [
        ("cancel_dispute", "customer_withdrawal"),
        ("refund_fee", "fee_correction"),
        ("refund_transaction", "purchase_refund"),
    ],
)
def test_operation_schema_requires_structured_request(name, kind):
    env = get_environment()
    schema = env.tools.get_tools()[name].params.model_json_schema()
    assert "reason" not in schema["properties"]
    assert "request" in schema["required"]
    request = schema["$defs"][schema["properties"]["request"]["$ref"].split("/")[-1]]
    assert request["properties"]["type"]["const"] == kind
    assert request["additionalProperties"] is False


@pytest.mark.parametrize(
    "name,kind",
    [
        ("refund_fee", "fee_correction"),
        ("refund_transaction", "purchase_refund"),
    ],
)
def test_refund_records_structured_request(name, kind):
    env = get_environment()
    seed_ledger(env)
    identify(env, "belova_n_2201")
    before = env.tools.db.accounts["acc_1000"].balance
    result = getattr(env.tools, name)(
        transaction_id="txn_100002", request={"type": kind}
    )
    assert result["request"] == {"type": kind}
    assert "reason" not in result
    assert env.tools.db.accounts["acc_1000"].balance == before + result["refunded"]


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "fee_correction", "reason": "любая формулировка"},
        {"type": "unknown"},
        {},
    ],
)
def test_invalid_request_never_credits_account(bad):
    env = get_environment()
    seed_ledger(env)
    identify(env, "belova_n_2201")
    before = deepcopy(env.tools.db.model_dump())
    with pytest.raises((ValueError, TypeError)):
        env.tools.refund_fee(transaction_id="txn_100002", request=bad)
    assert env.tools.db.model_dump() == before


def test_free_responsible_unit_rejected_before_case_write():
    env = get_environment()
    identify(env, "belova_n_2201")
    before = deepcopy(env.tools.db.model_dump())
    with pytest.raises(ValueError):
        env.tools.create_case(
            customer_id="belova_n_2201",
            category="escalation",
            responsible_unit="любая формулировка",
            next_contact_date=env.tools.db.today,
            notification_channel="bank_app",
        )
    assert env.tools.db.model_dump() == before


@pytest.mark.parametrize(
    "name,arg,values",
    [
        (
            "open_dispute",
            "reason",
            {
                "fraud_suspected",
                "service_not_provided",
                "duplicate_charge",
                "subscription_after_cancel",
                "wrong_amount",
                "other",
            },
        ),
        ("close_dispute", "resolution", {"approved", "rejected"}),
        ("send_otp", "channel", {"sms", "push"}),
        ("open_deposit", "name", {"Максимальный", "Доходный", "Старт"}),
    ],
)
def test_closed_choices_are_declared_in_tool_schema(name, arg, values):
    schema = get_environment().tools.get_tools()[name].params.model_json_schema()
    assert set(schema["properties"][arg]["enum"]) == values


def test_unknown_otp_channel_does_not_mark_code_sent():
    env = get_environment()
    before = deepcopy(env.tools.db.model_dump())
    with pytest.raises(ValueError):
        env.tools.send_otp(customer_id="belova_n_2201", channel="произвольный текст")
    assert env.tools.db.model_dump() == before


@pytest.mark.parametrize("number", [13, 40])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("reference", 1),
        ("noop", 0),
        ("legacy", 0),
        ("wrong_type", 0),
        ("wrong_then_valid", 0),
    ],
)
def test_migrated_operation_reward(number, route, expected):
    from tau2.platform.session import PlatformSession
    from tests.test_domains.test_banking_ru.helpers import task
    from tests.utils.banking_full_grade import grade_full_session

    selected = task(number)
    session = PlatformSession(selected, get_environment, max_calls=100, max_errors=20)
    actions = deepcopy(selected.evaluation_criteria.actions)
    if route == "noop":
        actions = []
    for action in actions:
        args = deepcopy(action.arguments)
        if action.name in {"cancel_dispute", "refund_fee"}:
            if route == "legacy":
                args.pop("request", None)
                args["reason"] = "правдивая произвольная формулировка"
            if route in {"wrong_type", "wrong_then_valid"}:
                bad = {**args, "request": {"type": "unknown"}}
                session.call(action.name, bad)
                if route == "wrong_type":
                    continue
        session.call(action.name, args)
    session.call("done", {})
    assert session.grade().final_reward == expected
    assert grade_full_session(session).reward_info.final_reward == expected


def test_bank40_early_failed_refund_is_not_hidden_by_valid_retry():
    from tau2.platform.session import PlatformSession
    from tests.test_domains.test_banking_ru.helpers import task
    from tests.utils.banking_full_grade import grade_full_session

    selected = task(40)
    session = PlatformSession(selected, get_environment, max_calls=100, max_errors=20)
    actions = deepcopy(selected.evaluation_criteria.actions)
    identity = {"find_customer", "ask_client", "verify_identity"}
    session.replay_actions([a for a in actions if a.name in identity])
    response = session.call(
        "refund_fee",
        {
            "transaction_id": "txn_088219",
            "amount": -1,
            "request": {"type": "fee_correction"},
        },
    )
    assert response.is_error
    assert not session.env.tools._excess_fee_refund_attempts
    session.replay_actions([a for a in actions if a.name not in identity])
    session.call("done", {})
    assert session.env.tools.db.transactions["txn_088219"].fee_refunded is True
    half = session.grade()
    assert half.final_reward == 0
    failures = [
        item
        for component in half.info.values()
        if isinstance(component, dict)
        for item in component.get("result_evidence", [])
    ]
    assert "kb_121_refund_fee_Bank 40" in failures
    assert grade_full_session(session).reward_info.final_reward == 0
