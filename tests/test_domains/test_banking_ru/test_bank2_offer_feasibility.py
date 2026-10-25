"""An approved offer may be infeasible because existing claims reserve its scope."""

import sys
from copy import deepcopy
from pathlib import Path

from tests.utils.banking_parent_fixture import copy_historical_data

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/banking_ru"))


def test_approved_offer_cannot_spend_already_claimed_amount(tmp_path):
    from bank2_resolution_oracle import calculate
    from upgrade_bank2_history import migrate

    p = tmp_path / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    restore_bank2_v5(p)
    db, _, _ = migrate(p)
    # A gap24.37 contains an existing claim10: only14.37 is free.
    offer = deepcopy(db["reward_resolution_offers"]["offer_610723"])
    offer.update(id="offer_feasibility", amount=25, allocation_first_amount=14.38)
    db["reward_resolution_offers"][offer["id"]] = offer
    result = calculate(db)
    assert result["maximum"] == 2400
    assert result["winners"] == ["offer_610723"]
    assert result["offers"][offer["id"]]["permitted"] is False
    # Boundary:14.37 can be paid while preserving the existing10.
    offer["allocation_first_amount"] = 14.37
    result = calculate(db)
    assert result["maximum"] == 2500
    assert result["payouts"] == {"txn_804219": 1437, "txn_804227": 1063}
    assert result["claims"] == {
        "txn_804219": 1000,
        "txn_804227": 4237,
        "txn_804205": 700,
    }
