"""Chronology is an instant plus stable-ID order, not ISO string order."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru import environment, file_pilot, utils
from tests.test_domains.test_banking_ru.test_bank2_feasible_offers import (
    test_feasible_offers_whole_grader as check_offer_route,
)
from tests.utils.banking_parent_fixture import copy_historical_data

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/banking_ru"))


def test_independent_oracle_uses_instant_and_id_tiebreak(tmp_path):
    from bank2_multisource_oracle import CUSTOMER, A, calculate
    from upgrade_bank2_history import migrate

    from tau2.domains.banking_ru.data_model import BankingDB
    from tau2.domains.banking_ru.reward_workflows import reward_obligations

    p = tmp_path / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    restore_bank2_v5(p)
    db, _, _ = migrate(p)
    for ident, amount, instant in [
        ("txn_817101", 348.60, "2026-07-25T14:15:00+05:30"),
        ("txn_817105", 451.40, "2026-07-25T14:00:00+05:45"),
        ("txn_817109", 395.80, "2026-07-24T23:00:00-10:00"),
    ]:
        row = deepcopy(db["transactions"][A])
        row.update(
            id=ident,
            amount=amount,
            merchant="Городское кафе",
            card_id="card_1187",
            date="2026-07-19",
            purchased_at="2026-07-19T15:00:00+03:00",
            posted_at=instant,
            receipt_id=ident,
            order_id=ident,
        )
        db["transactions"][ident] = row
    db["cashback_rules"][CUSTOMER]["cycles"][0]["limit"] = 322.10
    independent = calculate(db)
    actual = reward_obligations(BankingDB.model_validate(db), CUSTOMER, "2026-07")
    assert actual[A]["entitlement"] == 4937
    assert independent["rows"][A]["entitlement"] == 4937
    assert independent["rows"]["txn_817101"]["entitlement"] == 1743
    assert independent["rows"]["txn_817105"]["entitlement"] == 2257
    assert independent["rows"]["txn_817109"]["entitlement"] == 0


def test_activation_and_purchase_are_compared_as_instants():
    from bank2_multisource_oracle import CUSTOMER, B, calculate

    from tau2.domains.banking_ru.data_model import BankingDB
    from tau2.domains.banking_ru.reward_workflows import reward_obligations

    db = json.loads((utils.BANKING_RU_DATA_DIR / "db.json").read_text())
    db["promotion_activations"]["pa_730416"]["activated_at"] = (
        "2026-07-26T14:30:00+05:45"
    )
    db["transactions"][B]["purchased_at"] = "2026-07-26T09:00:00+00:00"
    actual = reward_obligations(BankingDB.model_validate(db), CUSTOMER, "2026-07")
    assert actual[B]["entitlement"] == 10000
    assert calculate(db)["rows"][B]["entitlement"] == 10000


@pytest.fixture
def chronological(tmp_path, monkeypatch):
    from upgrade_bank2_feasible_offers import migrate as offers
    from upgrade_bank2_history import migrate as history
    from upgrade_bank2_posting_order import migrate as chronology

    p = tmp_path / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    restore_bank2_v5(p)
    history(p)
    offers(p)
    chronology(p)
    for mod in [environment, utils, file_pilot]:
        for key, val in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(mod, key):
                monkeypatch.setattr(mod, key, val)
    return next(t for t in environment.get_tasks() if t.id == "Bank 2")


@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("repeat", 1),
        ("tie", 1),
        ("split", 1),
        ("noop", 0),
        ("one_cent_excess", 0),
        ("clock_order", 0),
    ],
)
def test_chronology_whole_grader(chronological, route, want, tmp_path):
    check_offer_route(chronological, route, want, tmp_path)
