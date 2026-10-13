"""Identity, OTP and message contracts through the real tool boundary."""

import pytest

from tau2.domains.banking_ru.environment import get_environment

from .tool_contract_support import reject, snapshot, succeed

OWNER = "belova_n_2201"


@pytest.fixture
def env():
    return get_environment()


def find_customer_success(env):
    before = snapshot(env)
    assert succeed(env, "find_customer", phone="8 (916) 340 11 25") == [
        {"id": OWNER, "full_name": "Белова Наталья Игоревна"}
    ]
    assert snapshot(env) == before


def verify_identity_success(env):
    answer = succeed(
        env,
        "ask_client",
        customer_id=OWNER,
        request={"type": "identity", "method": "code_word"},
    )
    assert answer["data"]["credential"] == "капучино"
    before_hash = env.get_db_hash()
    result = succeed(
        env,
        "verify_identity",
        customer_id=OWNER,
        credential=" КАПУЧИНО ",
        answer_id=answer["answer_id"],
    )
    assert "Белова Наталья Игоревна" in result
    assert env.tools.db.session.verified_customers == [OWNER]
    assert env.get_db_hash() == before_hash


def calculate_success(env):
    before = snapshot(env)
    assert succeed(env, "calculate", expression="(125.50 - 25.50) * 3 / 2") == "150.0"
    assert snapshot(env) == before


def send_otp_success(env):
    before_hash = env.get_db_hash()
    result = succeed(env, "send_otp", customer_id=OWNER)
    assert result == "Код отправлен на номер +7 916 340-11-25 (sms)."
    assert "104582" not in result
    assert env.tools.db.session.otp_sent == [OWNER]
    assert env.tools.db.session.otp_verified == []
    assert env.tools.db.session.verified_customers == []
    assert env.get_db_hash() == before_hash


def check_otp_success(env):
    succeed(env, "send_otp", customer_id=OWNER)
    result = succeed(env, "check_otp", customer_id=OWNER, code="104582")
    assert result == "Код подтверждения принят."
    assert env.tools.db.session.otp_verified == [OWNER]
    assert env.tools.db.session.verified_customers == []


def ask_client_success(env):
    before = snapshot(env)
    result = succeed(
        env,
        "ask_client",
        customer_id=OWNER,
        request={"type": "identity", "method": "birth_date"},
    )
    assert result["customer_id"] == OWNER and result["status"] == "answered"
    assert result["request_type"] == "identity" and result["answer_id"]
    assert result["data"]["credential"] == "1992-04-17"
    assert snapshot(env) == before


def reply_to_ticket_success(env):
    before_hash = env.get_db_hash()
    text = "Для оформления спора требуется уточнить сумму возврата продавца."
    assert succeed(env, "reply_to_ticket", customer_id=OWNER, text=text) == (
        "Ответ отправлен клиенту."
    )
    assert env.tools.db.ticket_replies == {OWNER: text}
    assert env.tools.db.session.verified_customers == []
    assert env.get_db_hash() == before_hash


def find_customer_error(env):
    reject(env, "find_customer", "ERR_INVALID_ARGUMENT")


def verify_identity_error(env):
    reject(
        env,
        "verify_identity",
        "ERR_IDENTITY_MISMATCH",
        customer_id=OWNER,
        credential="неверно",
        answer_id="not-received",
    )


def calculate_error(env):
    reject(env, "calculate", "ERR_INVALID_ARGUMENT", expression="__import__('os')")


def send_otp_error(env):
    reject(env, "send_otp", "ERR_NOT_FOUND", customer_id="missing-customer")


def check_otp_error(env):
    succeed(env, "send_otp", customer_id=OWNER)
    reject(env, "check_otp", "ERR_OTP_INVALID", customer_id=OWNER, code="000000")


def ask_client_error(env):
    reject(
        env,
        "ask_client",
        "ERR_INVALID_ARGUMENT",
        customer_id=OWNER,
        request={"type": "identity", "method": "code_word"},
        question="free text",
    )


def reply_to_ticket_error(env):
    reject(
        env, "reply_to_ticket", "ERR_INVALID_ARGUMENT", customer_id=OWNER, text=" \n "
    )


SUCCESS_SCENARIOS = {
    "find_customer": find_customer_success,
    "verify_identity": verify_identity_success,
    "calculate": calculate_success,
    "send_otp": send_otp_success,
    "check_otp": check_otp_success,
    "ask_client": ask_client_success,
    "reply_to_ticket": reply_to_ticket_success,
}

ERROR_SCENARIOS = {
    "find_customer": find_customer_error,
    "verify_identity": verify_identity_error,
    "calculate": calculate_error,
    "send_otp": send_otp_error,
    "check_otp": check_otp_error,
    "ask_client": ask_client_error,
    "reply_to_ticket": reply_to_ticket_error,
}
