"""Compare exact independent algorithms on small finite funding menus."""

import sys
from pathlib import Path
from random import Random

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))


def test_bitset_oracle_matches_exhaustive_subsets():
    from bank2_resolution_oracle import menu_choices, quarter_menu_choices

    rng = Random(3881)
    for _ in range(5):
        rows = []
        for g in range(6):
            for k in range(3):
                amount = rng.randrange(10, 80) * 4
                bank = rng.randrange(0, amount + 1)
                rows.append(
                    dict(
                        id=f"{g}-{k}",
                        exclusive_group=str(g),
                        amount=amount / 100,
                        bank_cost=bank / 100,
                        partner_cost=(amount - bank) / 100,
                        allocation_first_amount=amount / 400,
                    )
                )
        offer = dict(
            components=rows,
            transaction_ids=["a", "b"],
            allocation_first_transaction_id="a",
            amount=8,
            selection_budget=dict(bank=4, partner=5),
        )
        exact = menu_choices(offer, {"a": 300, "b": 700})
        fast = quarter_menu_choices(offer, {"a": 300, "b": 700})
        assert fast[0]["additional"] == exact[0]["additional"]
        assert fast[0]["allocation"] == exact[0]["allocation"]
        assert fast[0]["component_ids"] in [x["component_ids"] for x in exact]
