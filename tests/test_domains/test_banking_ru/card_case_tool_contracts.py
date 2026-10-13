"""Real backend contracts for card, dispute and case tools.

These tests catch missing authorization, missing writes, incorrect resource
selection and state transitions. Policy-only traps deliberately remain callable.
Credentials read from fixtures establish backend setup, not agent-visible proof.
"""

import pytest

from tau2.domains.banking_ru.data_model import Case
from tau2.domains.banking_ru.environment import get_environment
from tau2.environment.environment import Environment
from tests.test_domains.test_banking_ru.tool_contract_support import (
    authorize_otp,
    identify,
    reject,
    succeed,
)


@pytest.fixture
def env() -> Environment:
    return get_environment()


def scenario_block_card(env: Environment):
    identify(env, "belova_n_2201")
    result = succeed(env, "block_card", card_id="card_4417", reason="lost")
    card = env.tools.db.cards["card_4417"]
    assert result["id"] == "card_4417"
    assert result["status"] == card.status == "blocked"
    assert result["block_reason"] == card.block_reason == "lost"
    assert result["blocked_at"] == card.blocked_at == "2026-08-28"


def scenario_unblock_card(env: Environment):
    identify(env, "belova_n_2201")
    succeed(env, "block_card", card_id="card_4417", reason="temporary")
    result = succeed(env, "unblock_card", card_id="card_4417")
    card = env.tools.db.cards["card_4417"]
    assert result["id"] == "card_4417"
    assert result["status"] == card.status == "active"
    assert result["block_reason"] is card.block_reason is None
    assert result["blocked_at"] is card.blocked_at is None


def scenario_freeze_card(env: Environment):
    identify(env, "belova_n_2201")
    result = succeed(env, "freeze_card", card_id="card_4417")
    card = env.tools.db.cards["card_4417"]
    assert result["id"] == "card_4417"
    assert result["status"] == card.status == "blocked"
    assert result["block_reason"] == card.block_reason == "temporary"
    assert result["blocked_at"] == card.blocked_at == "2026-08-28"


def scenario_reissue_card(env: Environment):
    authorize_otp(env, "orlov_p_8814")
    result = succeed(
        env,
        "reissue_card",
        card_id="card_1145",
        delivery_address="г. Санкт-Петербург, ул. Марата, д. 22, кв. 9",
    )
    card = env.tools.db.cards["card_1145"]
    assert result["id"] == "card_1145"
    assert (
        result["delivery_address"]
        == card.delivery_address
        == "санкт петербург марата 22 9"
    )
    assert result["reissue_status"] == card.reissue_status == "ordered"
    assert card.status == "active"


def scenario_set_limit(env: Environment):
    identify(env, "morozova_e_3305")
    result = succeed(
        env,
        "set_limit",
        card_id="card_8823",
        limit_type="daily_cash_withdrawal",
        amount=300000.0,
    )
    assert result["card_id"] == "card_8823"
    assert result["daily_cash_withdrawal"] == 300000.0
    assert env.tools.db.card_limits["card_8823"].daily_cash_withdrawal == 300000.0


def scenario_open_dispute(env: Environment):
    identify(env, "belova_n_2201")
    count = len(env.tools.db.disputes)
    result = succeed(env, "open_dispute", transaction_id="txn_774402", reason="other")
    dispute = env.tools.db.disputes[result["id"]]
    assert result["transaction_id"] == dispute.transaction_id == "txn_774402"
    assert result["customer_id"] == dispute.customer_id == "belova_n_2201"
    assert result["amount"] == dispute.amount == 1290.0
    assert result["status"] == dispute.status == "under_review"
    assert result["filed_at"] == dispute.filed_at == "2026-08-28"
    assert result["sla_days"] == dispute.sla_days == 30
    assert result["reason"] == dispute.reason == "other"
    assert env.tools.db.transactions["txn_774402"].dispute_id == result["id"]
    assert len(env.tools.db.disputes) == count + 1


def scenario_cancel_dispute(env: Environment):
    identify(env, "belova_n_2201")
    result = succeed(
        env,
        "cancel_dispute",
        dispute_id="dsp_3391",
        request={"type": "customer_withdrawal"},
    )
    assert result["id"] == "dsp_3391"
    assert result["status"] == env.tools.db.disputes["dsp_3391"].status == "cancelled"
    assert env.tools.db.transactions["txn_774410"].dispute_id == "dsp_3391"


def scenario_close_dispute(env: Environment):
    identify(env, "belova_n_2201")
    result = succeed(env, "close_dispute", dispute_id="dsp_3391", resolution="approved")
    assert result["id"] == "dsp_3391"
    assert result["status"] == env.tools.db.disputes["dsp_3391"].status == "approved"


def scenario_release_hold(env: Environment):
    identify(env, "lebedev_i_7729")
    balance = env.tools.db.accounts[
        env.tools.db.transactions["txn_799102"].account_id
    ].balance
    result = succeed(env, "release_hold", transaction_id="txn_799102")
    transaction = env.tools.db.transactions["txn_799102"]
    assert result["id"] == "txn_799102"
    assert result["status"] == transaction.status == "declined"
    assert result["hold_expires_at"] is transaction.hold_expires_at is None
    assert transaction.amount == 4200.0
    assert env.tools.db.accounts[transaction.account_id].balance == balance


def _assert_case(env, result, *, customer, category, count):
    case = env.tools.db.cases[result["id"]]
    assert result["customer_id"] == case.customer_id == customer
    assert result["category"] == case.category == category
    assert result["status"] == case.status == "open"
    assert result["created_at"] == case.created_at == "2026-08-28"
    assert len(env.tools.db.cases) == count + 1
    return case


def scenario_create_case(env: Environment):
    identify(env, "belova_n_2201")
    count = len(env.tools.db.cases)
    result = succeed(
        env,
        "create_case",
        customer_id="belova_n_2201",
        category="merchant_investigation",
        transaction_id="txn_774402",
    )
    case = _assert_case(
        env,
        result,
        customer="belova_n_2201",
        category="merchant_investigation",
        count=count,
    )
    assert result["transaction_id"] == case.transaction_id == "txn_774402"
    assert result["amount"] == case.amount == 1290.0


def _loss_case(env):
    identify(env, "belova_n_2201")
    succeed(env, "block_card", card_id="card_4417", reason="lost")
    return succeed(env, "open_card_loss_case", card_id="card_4417")


def scenario_open_card_loss_case(env: Environment):
    count = len(env.tools.db.cases)
    result = _loss_case(env)
    case = _assert_case(
        env, result, customer="belova_n_2201", category="card_loss", count=count
    )
    assert result["card_id"] == case.card_id == "card_4417"
    assert env.tools.db.cards["card_4417"].block_reason == "lost"


def scenario_notify_card_loss_case(env: Environment):
    case = _loss_case(env)
    result = succeed(env, "notify_card_loss_case", case_id=case["id"])
    assert result["id"] == case["id"]
    assert result["status"] == env.tools.db.cases[case["id"]].status == "notified"
    assert result["card_id"] == "card_4417"


def _restructuring_case(env):
    identify(env, "belova_n_2201")
    return succeed(
        env, "create_case", customer_id="belova_n_2201", category="restructuring"
    )


def scenario_request_restructuring_documents(env: Environment):
    case = _restructuring_case(env)
    result = succeed(
        env,
        "request_restructuring_documents",
        case_id=case["id"],
        documents=["income_certificate", "employer_order"],
    )
    saved = env.tools.db.cases[case["id"]]
    assert result["id"] == case["id"]
    assert result["status"] == saved.status == "documents_requested"
    assert (
        result["requested_documents"]
        == saved.requested_documents
        == ["income_certificate", "employer_order"]
    )


def scenario_cancel_subscription(env: Environment):
    identify(env, "solomina_o_5214")
    subscription = env.tools.db.subscriptions["sub_3302"]
    subscription.paid_until = "2026-09-07"
    result = succeed(env, "cancel_subscription", subscription_id="sub_3302")
    subscription = env.tools.db.subscriptions["sub_3302"]
    assert result["id"] == "sub_3302"
    assert result["status"] == subscription.status == "cancelled"
    assert result["cancelled_at"] == subscription.cancelled_at == "2026-08-28"
    assert result["next_charge_date"] is subscription.next_charge_date is None
    assert result["paid_until"] == subscription.paid_until == "2026-09-07"
    assert env.tools.db.subscriptions["sub_3301"].status == "active"


def scenario_cancel_autopayment(env: Environment):
    identify(env, "sidorov_p_5544")
    result = succeed(env, "cancel_autopayment", autopayment_id="ap_5540")
    autopayment = env.tools.db.autopayments["ap_5540"]
    assert result["id"] == "ap_5540"
    assert result["status"] == autopayment.status == "cancelled"
    assert result["cancelled_at"] == autopayment.cancelled_at == "2026-08-28"
    assert env.tools.db.subscriptions["sub_2214"].cancelled_at == "2026-08-10"


def scenario_unblock_device(env: Environment):
    identify(env, "titova_a_7729")
    result = succeed(env, "unblock_device", device_id="dev_8821")
    assert result["id"] == "dev_8821"
    assert result["blocked"] is env.tools.db.devices["dev_8821"].blocked is False
    assert env.tools.db.devices["dev_8822"].blocked is True


def scenario_unblock_operation(env: Environment):
    authorize_otp(env, "nikitin_s_6631")
    result = succeed(env, "unblock_operation", transaction_id="txn_330871")
    transaction = env.tools.db.transactions["txn_330871"]
    assert result["id"] == "txn_330871"
    assert result["status"] == transaction.status == "posted"
    assert result["amount"] == transaction.amount == 120000.0


def scenario_reveal_card_details(env: Environment):
    identify(env, "egorov_m_1145")
    result = succeed(env, "reveal_card_details", card_id="card_9902")
    card = env.tools.db.cards["card_9902"]
    assert result["card_id"] == "card_9902"
    assert result["full_number"] == card.full_number
    assert result["cvv"] == card.cvv
    assert isinstance(result["full_number"], str)
    assert len(result["full_number"].replace(" ", "")) == 16
    assert isinstance(result["cvv"], str) and len(result["cvv"]) == 3
    assert card.secret_revealed is True


def scenario_request_credit_holidays(env: Environment):
    identify(env, "dorohov_v_6630")
    loan = env.tools.db.loans["ln_6630"].model_dump()
    count = len(env.tools.db.cases)
    result = succeed(env, "request_credit_holidays", loan_id="ln_6630")
    _assert_case(
        env, result, customer="dorohov_v_6630", category="credit_holidays", count=count
    )
    assert env.tools.db.loans["ln_6630"].model_dump() == loan


def scenario_share_document(env: Environment):
    identify(env, "morozova_e_1156")
    result = succeed(env, "share_document", document_id="doc_1156_1")
    document = env.tools.db.documents["doc_1156_1"]
    assert result["id"] == "doc_1156_1"
    assert result["shared"] is document.shared is True
    assert result["body"] == document.body
    assert "150 000" in result["body"]
    assert env.tools.db.documents["doc_1156_2"].shared is False


def scenario_escalate_to_human(env: Environment):
    identify(env, "belova_n_2201")
    count = len(env.tools.db.cases)
    result = succeed(
        env,
        "escalate_to_human",
        customer_id="belova_n_2201",
        request={"type": "access_protection"},
    )
    _assert_case(
        env, result, customer="belova_n_2201", category="escalation", count=count
    )


SUCCESS_SCENARIOS = {
    "block_card": scenario_block_card,
    "unblock_card": scenario_unblock_card,
    "freeze_card": scenario_freeze_card,
    "reissue_card": scenario_reissue_card,
    "set_limit": scenario_set_limit,
    "open_dispute": scenario_open_dispute,
    "cancel_dispute": scenario_cancel_dispute,
    "close_dispute": scenario_close_dispute,
    "release_hold": scenario_release_hold,
    "create_case": scenario_create_case,
    "open_card_loss_case": scenario_open_card_loss_case,
    "notify_card_loss_case": scenario_notify_card_loss_case,
    "request_restructuring_documents": scenario_request_restructuring_documents,
    "cancel_subscription": scenario_cancel_subscription,
    "cancel_autopayment": scenario_cancel_autopayment,
    "unblock_device": scenario_unblock_device,
    "unblock_operation": scenario_unblock_operation,
    "reveal_card_details": scenario_reveal_card_details,
    "request_credit_holidays": scenario_request_credit_holidays,
    "share_document": scenario_share_document,
    "escalate_to_human": scenario_escalate_to_human,
}


# Valid resources make deletion of an identity guard fail independently of
# not-found or state guards. Cases are seeded without a preceding authenticated
# write, so those two guards are tested with a genuinely empty session.
WRITE_ARGUMENTS = {
    "block_card": {"card_id": "card_4417", "reason": "lost"},
    "unblock_card": {"card_id": "card_2290"},
    "freeze_card": {"card_id": "card_4417"},
    "reissue_card": {"card_id": "card_1145"},
    "set_limit": {
        "card_id": "card_8823",
        "limit_type": "daily_cash_withdrawal",
        "amount": 100000.0,
    },
    "open_dispute": {"transaction_id": "txn_774402", "reason": "other"},
    "cancel_dispute": {
        "dispute_id": "dsp_3391",
        "request": {"type": "customer_withdrawal"},
    },
    "close_dispute": {"dispute_id": "dsp_3391", "resolution": "approved"},
    "release_hold": {"transaction_id": "txn_799102"},
    "create_case": {"customer_id": "belova_n_2201", "category": "other"},
    "open_card_loss_case": {"card_id": "card_4417"},
    "notify_card_loss_case": {"case_id": "case_contract_loss"},
    "request_restructuring_documents": {
        "case_id": "case_contract_restructuring",
        "documents": ["income_certificate", "employer_order"],
    },
    "cancel_subscription": {"subscription_id": "sub_3302"},
    "cancel_autopayment": {"autopayment_id": "ap_5540"},
    "unblock_device": {"device_id": "dev_8821"},
    "unblock_operation": {"transaction_id": "txn_330871"},
    "reveal_card_details": {"card_id": "card_4417"},
    "request_credit_holidays": {"loan_id": "ln_6630"},
    "share_document": {"document_id": "doc_1156_1"},
    "escalate_to_human": {
        "customer_id": "belova_n_2201",
        "request": {"type": "access_protection"},
    },
}


def _seed_cases(env):
    for category, key in [
        ("card_loss", "case_contract_loss"),
        ("restructuring", "case_contract_restructuring"),
    ]:
        env.tools.db.cases[key] = Case(
            id=key,
            customer_id="belova_n_2201",
            category=category,
            created_at="2026-08-28",
            status="open",
            card_id="card_4417" if category == "card_loss" else None,
        )


def _missing_identity(name):
    def scenario(env: Environment):
        _seed_cases(env)
        reject(env, name, "ERR_NOT_VERIFIED", **WRITE_ARGUMENTS[name])

    scenario.__name__ = f"scenario_{name}_without_identity"
    return scenario


ERROR_SCENARIOS = {name: _missing_identity(name) for name in WRITE_ARGUMENTS}


# The tool result is kept internal; no outgoing client call receives it.
