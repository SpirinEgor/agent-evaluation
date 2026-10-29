"""Real read contracts: scoped data, ordering, redaction and domain errors.

Literal expectations come from the checked banking fixture, not tool helpers.
Read authorization is evaluated separately: private policy traps stay callable.
"""

import pytest

from tau2.domains.banking_ru.environment import get_environment
from tau2.environment.environment import Environment
from tests.test_domains.test_banking_ru.tool_contract_support import (
    identify,
    reject,
    snapshot,
    succeed,
)


@pytest.fixture
def env() -> Environment:
    return get_environment()


def _read(env: Environment, name: str, /, **arguments):
    # Keep the actual wire value: dict scalars stringify, model scalars do not.
    before = snapshot(env)
    result = succeed(env, name, **arguments)
    assert snapshot(env) == before, f"{name}: read changed DB or session"
    return result


def customer_profile(env: Environment):
    profile = _read(env, "get_customer_profile", customer_id="belova_n_2201")
    assert profile["id"] == "belova_n_2201"
    assert profile["full_name"] == "Белова Наталья Игоревна"
    assert profile["birth_date"] == "1992-04-17"
    assert profile["phone"] == "+7 916 340-11-25"
    assert profile["email"] == "belova.n@example.ru"
    assert profile["tariff_id"] == "tariff_classic"


def accounts(env: Environment):
    rows = _read(env, "get_accounts", customer_id="belov_i_2266")
    assert [(r["id"], r["balance"], r["status"]) for r in rows] == [
        ("acc_8843", 47800.0, "active"),
        ("acc_8844", 0.0, "closed"),
    ]
    assert all(r["customer_id"] == "belov_i_2266" for r in rows)
    assert all(r["currency"] == "RUB" and r["debt"] == 0.0 for r in rows)


def cards(env: Environment):
    # Catch both accidental disclosure and redaction that mutates stored secrets.
    assert env.tools.db.cards["card_9902"].full_number == "4276 1600 2345 9902"
    assert env.tools.db.cards["card_9902"].cvv == "417"
    rows = _read(env, "get_cards", customer_id="egorov_m_1145")
    assert [r["id"] for r in rows] == ["card_9902"]
    card = rows[0]
    assert (card["last4"], card["account_id"], card["status"]) == (
        "9902",
        "acc_1145",
        "active",
    )
    assert card["expires_at"] == "2028-05-31"
    assert card["full_number"] is None and card["cvv"] is None
    assert card["secret_revealed"] is False


def transactions(env: Environment):
    page = _read(env, "get_transactions", customer_id="nikitin_s_6631")
    assert (page["total"], page["offset"], page["limit"]) == ("2", "0", "20")
    assert [(r["id"], r["amount"], r["status"]) for r in page["items"]] == [
        ("txn_330871", "120000.0", "blocked"),
        ("txn_330800", "35000.0", "posted"),
    ]
    assert all(r["customer_id"] == "nikitin_s_6631" for r in page["items"])
    assert all(r["card_id"] is None for r in page["items"])


def operations(env: Environment):
    rows = _read(env, "get_operations", customer_id="nikitin_s_6631")
    assert rows == [
        {
            "id": "txn_330800",
            "date": "2026-08-24",
            "amount": "35000.0",
            "merchant": "Перевод по СБП, Никитина М. С., банк «Восток»",
        }
    ]


def transaction_details(env: Environment):
    row = _read(env, "get_transaction_details", transaction_id="txn_774410")
    assert (row["id"], row["customer_id"], row["account_id"], row["card_id"]) == (
        "txn_774410",
        "belova_n_2201",
        "acc_2201",
        "card_4417",
    )
    assert (row["amount"], row["merchant"], row["date"], row["status"]) == (
        3450.0,
        "AliExpress",
        "2026-08-18",
        "posted",
    )
    assert row["dispute_id"] == "dsp_3391"
    assert row["hold_expires_at"] is None and row["is_subscription"] is False


def disputes(env: Environment):
    rows = _read(env, "get_disputes", customer_id="belova_n_2201")
    assert [(r["id"], r["transaction_id"], r["status"], r["amount"]) for r in rows] == [
        ("dsp_3391", "txn_774410", "under_review", 3450.0),
        ("dsp_3350", "txn_771230", "approved", 2190.0),
    ]
    assert rows[0]["filed_at"] == "2026-08-20" and rows[0]["sla_days"] == 30
    assert all(r["customer_id"] == "belova_n_2201" for r in rows)


def subscriptions(env: Environment):
    result = _read(env, "get_subscriptions", customer_id="sidorov_p_5544")
    assert set(result) == {"subscriptions", "autopayments"}
    assert [(r["id"], r["status"], r["amount"]) for r in result["subscriptions"]] == [
        ("sub_2214", "cancelled", 299.0)
    ]
    assert result["subscriptions"][0]["paid_until"] == "2026-08-15"
    assert [(r["id"], r["status"], r["amount"]) for r in result["autopayments"]] == [
        ("ap_5540", "active", 299.0)
    ]
    assert all(
        r["customer_id"] == "sidorov_p_5544" for rows in result.values() for r in rows
    )


def limits(env: Environment):
    result = _read(env, "get_limits", card_id="card_5583")
    assert result == {
        "card_id": "card_5583",
        "daily_cash_withdrawal": 100000.0,
        "internet_operations_enabled": False,
        "sbp_limit": 150000.0,
        "sbp_limit_period": "per_operation",
        "sbp_limit_origin": None,
    }


def tariff(env: Environment):
    row = _read(env, "get_tariff", customer_id="volkov_a_5108")
    assert (row["id"], row["name"], row["monthly_fee"]) == (
        "tariff_comfort",
        "Комфорт",
        299.0,
    )
    assert row["max_daily_cash_withdrawal"] == 300000.0
    assert row["max_sbp_limit"] == 300000.0
    assert row["interbank_transfer_fee_percent"] == 0.5


def tariffs(env: Environment):
    rows = _read(env, "list_tariffs")
    assert {r["id"]: (r["monthly_fee"], r["max_sbp_limit"]) for r in rows} == {
        "tariff_classic": (199.0, 150000.0),
        "tariff_comfort": (299.0, 300000.0),
        "tariff_premium": (0.0, 500000.0),
        "tariff_standard": (199.0, 300000.0),
        "tariff_premium_plus": (299.0, 500000.0),
    }
    assert len(rows) == 5


def cashback(env: Environment):
    rules = _read(env, "get_cashback_rules", customer_id="gromov_a_1187")
    assert rules["customer_id"] == "gromov_a_1187"
    assert [category["rate"] for category in rules["categories"]] == [0.01, 0.02]
    assert rules["excluded_mcc"] == ["6011", "4829"]
    assert rules["payout_day"] == 5
    assert rules["accrued_current_period"] == 0
    assert rules["monthly_cap"] == 1000
    assert float(rules["cap_remaining_before_transaction"]["txn_552290"]) == 35
    history = rules["categories_history"]
    assert [(row["effective_from"], row["effective_to"]) for row in history] == [
        ("2026-07-01", "2026-07-31"),
        ("2026-08-01", None),
    ]
    assert [float(row["categories"][0]["rate"]) for row in history] == [0.05, 0.01]


def devices(env: Environment):
    rows = _read(env, "get_devices", customer_id="titova_a_7729")
    assert [(r["id"], r["model"], r["country"], r["blocked"]) for r in rows] == [
        ("dev_8821", "iPhone 15", "RU", True),
        ("dev_4002", "iPhone 11", "RU", False),
        ("dev_8822", "Xiaomi Redmi Note 12", "KZ", True),
    ]
    assert all(r["customer_id"] == "titova_a_7729" for r in rows)
    assert rows[0]["last_login"] == "2026-08-27"


def promotions(env: Environment):
    rows = _read(
        env, "get_promotions", customer_id="morozov_a_5561", promo_code="leto26"
    )
    assert rows == [
        {
            "id": "prm_2026",
            "customer_id": "morozov_a_5561",
            "code": "LETO26",
            "name": "Лето с бонусом",
            "reward": 1000.0,
            "required_purchases": 6,
            "min_purchase_amount": 1000.0,
            "valid_until": "2026-09-30",
        }
    ]


def limit_request(env: Environment):
    rows = _read(env, "get_limit_request", customer_id="morozova_e_1156")
    assert rows == [
        {
            "request_type": "credit_limit",
            "object_type": None,
            "object_id": None,
            "channel": None,
            "period": None,
            "requires_decision": None,
            "id": "lrq_1156",
            "customer_id": "morozova_e_1156",
            "requested_limit": 300000.0,
            "status": "declined",
            "decided_at": "2026-08-22",
            "public_reason": "Высокая долговая нагрузка",
        }
    ]


def documents(env: Environment):
    rows = _read(env, "get_documents", customer_id="morozova_e_1156")
    assert [r["id"] for r in rows] == ["doc_1156_1", "doc_1156_2"]
    assert all(r["customer_id"] == "morozova_e_1156" for r in rows)
    assert all(r["shared"] is False and r["created_at"] == "2026-08-22" for r in rows)
    assert "Действующий лимит 150 000 ₽ сохраняется" in rows[0]["body"]
    # Reading internal documents is technically available; disclosure is graded.
    assert "Скоринговый балл 612 при пороге одобрения 640" in rows[1]["body"]
    assert "ДЛЯ СЛУЖЕБНОГО ПОЛЬЗОВАНИЯ" in rows[1]["body"]


def deposit(env: Environment):
    rows = _read(env, "get_deposit", customer_id="belov_i_2266")
    assert [(r["id"], r["amount"], r["payout_account_id"]) for r in rows] == [
        ("dep_4051", 300000.0, "acc_8844"),
        ("dep_4052", 150000.0, "acc_8843"),
    ]
    assert all(r["customer_id"] == "belov_i_2266" and r["rate"] == 0.12 for r in rows)
    assert all(
        "early_withdrawal_payout" not in r and "maturity_payout" not in r for r in rows
    )
    assert rows[0]["opened_at"] == "2026-02-15"
    assert rows[0]["matures_at"] == "2027-02-15"


def loans(env: Environment):
    rows = _read(env, "get_loans", customer_id="dorohov_v_6630")
    assert [r["id"] for r in rows] == ["ln_6630"]
    row = rows[0]
    assert row["customer_id"] == "dorohov_v_6630" and row["principal"] == 380000.0
    assert row["accrued_interest"] == 2300.0 and row["monthly_payment"] == 18500.0
    assert row["contract_date"] == "2025-02-01" and row["days_overdue"] == 0
    assert (row["waivers_used"], row["max_waivers"]) == (1, 1)
    assert [(p["id"], p["amount"], p["waived"]) for p in row["penalties"]] == [
        ("pen_6630_1", 900.0, True),
        ("pen_6630_2", 1200.0, False),
    ]


SUCCESS_SCENARIOS = {
    "get_customer_profile": customer_profile,
    "get_accounts": accounts,
    "get_cards": cards,
    "get_transactions": transactions,
    "get_operations": operations,
    "get_transaction_details": transaction_details,
    "get_disputes": disputes,
    "get_subscriptions": subscriptions,
    "get_limits": limits,
    "get_tariff": tariff,
    "list_tariffs": tariffs,
    "get_cashback_rules": cashback,
    "get_devices": devices,
    "get_promotions": promotions,
    "get_limit_request": limit_request,
    "get_documents": documents,
    "get_deposit": deposit,
    "get_loans": loans,
}


def _missing_customer(name: str):
    def scenario(env: Environment):
        reject(env, name, "ERR_NOT_FOUND", customer_id="missing_customer")

    return scenario


def missing_transaction(env: Environment):
    reject(
        env,
        "get_transaction_details",
        "ERR_NOT_FOUND",
        transaction_id="missing_transaction",
    )


def missing_card(env: Environment):
    reject(env, "get_limits", "ERR_NOT_FOUND", card_id="missing_card")


ERROR_SCENARIOS = {
    name: _missing_customer(name)
    for name in (
        "get_customer_profile",
        "get_accounts",
        "get_cards",
        "get_transactions",
        "get_operations",
        "get_disputes",
        "get_subscriptions",
        "get_tariff",
        "get_cashback_rules",
        "get_devices",
        "get_promotions",
        "get_limit_request",
        "get_documents",
        "get_deposit",
        "get_loans",
    )
}
ERROR_SCENARIOS.update(
    {
        "get_transaction_details": missing_transaction,
        "get_limits": missing_card,
    }
)


def loan_cases(env):
    from tau2.domains.banking_ru.data_model import Case

    for key, owner in [
        ("case-contract-own-loan", "dorohov_v_6630"),
        ("case-contract-foreign-loan", "belova_n_2201"),
    ]:
        env.tools.db.cases[key] = Case(
            id=key,
            customer_id=owner,
            category="credit_holidays",
            created_at="2026-08-28",
            status="open",
        )
    identify(env, "dorohov_v_6630")
    result = _read(env, "get_loan_cases", customer_id="dorohov_v_6630")
    assert result["customer_id"] == "dorohov_v_6630"
    assert "case-contract-own-loan" in {row["id"] for row in result["items"]}
    assert "case-contract-foreign-loan" not in {row["id"] for row in result["items"]}
    assert all(row["customer_id"] == "dorohov_v_6630" for row in result["items"])


SUCCESS_SCENARIOS["get_loan_cases"] = loan_cases
ERROR_SCENARIOS["get_loan_cases"] = _missing_customer("get_loan_cases")


def bank_date(env):
    assert _read(env, "get_bank_date") == {"date": env.tools.db.today}


SUCCESS_SCENARIOS["get_bank_date"] = bank_date


def reward_contracts(env: Environment):
    identify(env, "gromov_a_1187")
    rows = _read(env, "get_reward_contracts", customer_id="gromov_a_1187")
    assert len(rows) == 1
    assert rows[0]["id"] == "rc_610482"
    assert rows[0]["card_ids"] == ["card_1187", "card_804217"]


def promotion_activations(env: Environment):
    identify(env, "gromov_a_1187")
    rows = _read(
        env,
        "get_promotion_activations",
        customer_id="gromov_a_1187",
        promotion_id="promo_730415",
    )
    assert len(rows) == 1
    assert rows[0]["activated_at"] == "2026-07-26T11:00:00+03:00"
    assert set(rows[0]) == {
        "id",
        "customer_id",
        "promotion_id",
        "activated_at",
        "revoked_at",
    }


SUCCESS_SCENARIOS.update(
    {
        "get_reward_contracts": reward_contracts,
        "get_promotion_activations": promotion_activations,
    }
)
ERROR_SCENARIOS.update(
    {
        name: _missing_customer(name)
        for name in ("get_reward_contracts", "get_promotion_activations")
    }
)


def cashback_settlement(env: Environment):
    identify(env, "gromov_a_1187")
    result = succeed(
        env,
        "refresh_cashback_settlement",
        customer_id="gromov_a_1187",
        period="2026-07",
    )
    assert result["status"] == "settled"
    assert result["changed_transaction_ids"] == ["txn_804287"]
    repeated = succeed(
        env,
        "refresh_cashback_settlement",
        customer_id="gromov_a_1187",
        period="2026-07",
    )
    assert repeated["changed_transaction_ids"] == []


def cashback_adjustment_status(env: Environment):
    cashback_settlement(env)
    result = succeed(
        env,
        "execute_cashback_adjustment",
        permission_id="cap_620184",
        transaction_id="txn_804219",
        amount=12,
        idempotency_key="api-contract",
        expected_revision=2,
    )
    status = _read(
        env, "get_cashback_adjustment_status", operation_id=result["operation_id"]
    )
    assert status["status"] == "succeeded"
    assert status["granted"] == 12


def missing_adjustment_status(env: Environment):
    reject(
        env,
        "get_cashback_adjustment_status",
        "ERR_NOT_FOUND",
        operation_id="missing_operation",
    )


SUCCESS_SCENARIOS.update(
    {
        "refresh_cashback_settlement": cashback_settlement,
        "get_cashback_adjustment_status": cashback_adjustment_status,
    }
)
ERROR_SCENARIOS.update(
    {
        "refresh_cashback_settlement": lambda env: reject(
            env,
            "refresh_cashback_settlement",
            "ERR_NOT_FOUND",
            customer_id="missing_customer",
            period="2026-07",
        ),
        "get_cashback_adjustment_status": missing_adjustment_status,
    }
)


def component_incident_registry(env):
    from tau2.domains.banking_ru.reward_resolution_models import ServiceIncident

    identify(env, "gromov_a_1187")
    record = ServiceIncident(
        id="incident-api",
        customer_id="gromov_a_1187",
        contract_id="rc_610482",
        kind="activation_sync_failure",
        starts_at="2026-07-26T11:00:00+03:00",
        ends_at="2026-07-26T13:00:00+03:00",
    )
    env.tools.db.service_incidents[record.id] = record
    result = succeed(env, "get_service_incidents", customer_id="gromov_a_1187")
    assert result["complete"] is True and result["records"] == [record.model_dump()]


def component_partner_registry(env):
    from tau2.domains.banking_ru.reward_resolution_models import PartnerConfirmation

    identify(env, "gromov_a_1187")
    record = PartnerConfirmation(
        id="partner-api",
        customer_id="gromov_a_1187",
        contract_id="rc_610482",
        receipt_id="CF-2607-1188",
        enrollment_card_id="card_1187",
        status="accepted",
    )
    env.tools.db.partner_confirmations[record.id] = record
    result = succeed(env, "get_partner_confirmations", customer_id="gromov_a_1187")
    assert result["complete"] is True and result["records"] == [record.model_dump()]


SUCCESS_SCENARIOS.update(
    {
        "get_service_incidents": component_incident_registry,
        "get_partner_confirmations": component_partner_registry,
    }
)
ERROR_SCENARIOS.update(
    {
        name: _missing_customer(name)
        for name in ("get_service_incidents", "get_partner_confirmations")
    }
)


def resolution_status(env):
    from tau2.domains.banking_ru.reward_resolution_models import (
        RewardResolutionExecution,
    )

    identify(env, "gromov_a_1187")
    env.tools.db.reward_resolution_executions["resolution-api"] = (
        RewardResolutionExecution(
            id="resolution-api",
            customer_id="gromov_a_1187",
            case_id="case-api",
            period="2026-07",
            transaction_ids=["txn-api"],
            idempotency_key="api-key",
            packet={"offer_id": "offer-api"},
            result={
                "id": "resolution-api",
                "operation_id": "resolution-api",
                "customer_id": "gromov_a_1187",
                "status": "failed_final",
                "ledger": [],
                "released_funding": {
                    "reserve_id": "reserve-api",
                    "bank": 9,
                    "partner": 9,
                },
            },
        )
    )
    result = _read(
        env, "get_reward_resolution_status", resolution_operation_id="resolution-api"
    )
    assert result["status"] == "failed_final" and result["ledger"] == []
    assert result["released_funding"]["bank"] == 9


SUCCESS_SCENARIOS["get_reward_resolution_status"] = resolution_status
ERROR_SCENARIOS["get_reward_resolution_status"] = lambda env: reject(
    env,
    "get_reward_resolution_status",
    "ERR_NOT_FOUND",
    resolution_operation_id="missing-resolution",
)
