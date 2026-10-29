"""Conditional settlement authority uses owned facts across systems."""

from types import SimpleNamespace

import pytest

from tau2.domains.banking_ru import reward_resolution as resolution


def facts():
    offer = SimpleNamespace(
        customer_id="own",
        transaction_ids=["purchase"],
        components=[
            SimpleNamespace(
                id="E",
                eligibility=SimpleNamespace(
                    kind="service_incident",
                    transaction_id="purchase",
                    incident_kind="activation_sync_failure",
                ),
            ),
            SimpleNamespace(
                id="G",
                eligibility=SimpleNamespace(
                    kind="partner_confirmation",
                    transaction_id="purchase",
                    incident_kind="activation_sync_failure",
                ),
            ),
        ],
    )
    data = dict(
        transactions={
            "purchase": dict(
                id="purchase",
                customer_id="own",
                account_id="account",
                card_id="new",
                receipt_id="wanted",
                kind="purchase",
                status="posted",
                purchased_at="2026-07-26T12:00:00+03:00",
            )
        },
        contracts={
            "contract": dict(
                id="contract",
                customer_id="own",
                account_id="account",
                card_ids=["old", "new"],
                effective_from="2026-06-01",
                effective_to=None,
                replaced_card_id="old",
                replacement_card_id="new",
                replacement_effective_from="2026-07-20",
            )
        },
        incidents={
            "incident": dict(
                id="incident",
                customer_id="own",
                contract_id="contract",
                kind="activation_sync_failure",
                starts_at="2026-07-26T11:00:00+03:00",
                ends_at="2026-07-26T13:00:00+03:00",
            )
        },
        confirmations={
            "partner": dict(
                id="partner",
                customer_id="own",
                contract_id="contract",
                receipt_id="wanted",
                enrollment_card_id="old",
                status="accepted",
            )
        },
    )
    return offer, data


def check(selected, evidence, data=None):
    offer, default = facts()
    validator = getattr(resolution, "validate_eligibility", None)
    assert callable(validator), "Missing conditional component authority validation"
    return validator(offer, selected, evidence, **(data or default))


def test_old_enrollment_card_is_valid_through_same_contract_reissue():
    assert (
        check(["G"], {"G": {"contract_id": "contract", "record_id": "partner"}}) is None
    )


@pytest.mark.parametrize(
    "defect",
    [
        "receipt",
        "owner",
        "status",
        "contract",
        "card",
        "purchase_time",
        "reissue_date",
        "account",
    ],
)
def test_partner_requires_exact_purchase_contract_and_reissue(defect):
    _, data = facts()
    if defect == "receipt":
        data["confirmations"]["partner"]["receipt_id"] = "other-receipt"
    if defect == "owner":
        data["confirmations"]["partner"]["customer_id"] = "other"
    if defect == "status":
        data["confirmations"]["partner"]["status"] = "registered"
    if defect == "contract":
        data["confirmations"]["partner"]["contract_id"] = "other"
    if defect == "card":
        data["confirmations"]["partner"]["enrollment_card_id"] = "other"
    if defect == "purchase_time":
        data["contracts"]["contract"]["effective_from"] = "2026-07-27"
    if defect == "reissue_date":
        data["contracts"]["contract"]["replacement_effective_from"] = "2026-07-27"
    if defect == "account":
        data["contracts"]["contract"]["account_id"] = "other"
    with pytest.raises(ValueError):
        check(["G"], {"G": {"contract_id": "contract", "record_id": "partner"}}, data)


def test_incident_must_cover_purchase_and_same_contract():
    proof = {"E": {"contract_id": "contract", "record_id": "incident"}}
    assert check(["E"], proof) is None
    _, data = facts()
    data["incidents"]["incident"]["contract_id"] = "archived"
    with pytest.raises(ValueError):
        check(["E"], proof, data)
    _, data = facts()
    data["incidents"]["incident"]["starts_at"] = "2026-07-27T00:00:00+03:00"
    with pytest.raises(ValueError):
        check(["E"], proof, data)


@pytest.mark.parametrize(
    "proof",
    [
        {},
        {"G": {"contract_id": "contract", "record_id": "unknown"}},
        {
            "G": {"contract_id": "contract", "record_id": "partner"},
            "E": {"contract_id": "contract", "record_id": "incident"},
        },
    ],
)
def test_missing_unknown_and_unused_authority_is_rejected(proof):
    with pytest.raises(ValueError):
        check(["G"], proof)


def test_incident_boundaries_timezone_and_reissue_date_are_explicit():
    proof = {"E": {"contract_id": "contract", "record_id": "incident"}}
    _, data = facts()
    data["incidents"]["incident"]["starts_at"] = "2026-07-26T09:00:00+00:00"
    assert check(["E"], proof, data) is None
    data["incidents"]["incident"]["ends_at"] = "2026-07-26T09:00:00+00:00"
    with pytest.raises(ValueError):
        check(["E"], proof, data)
    _, data = facts()
    data["contracts"]["contract"]["replacement_effective_from"] = "2026-07-26"
    assert (
        check(["G"], {"G": {"contract_id": "contract", "record_id": "partner"}}, data)
        is None
    )
