"""An alternative article is valid only for its exact edition and matching terms."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru import utils

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))


@pytest.mark.parametrize(
    "field,value",
    [
        (None, None),
        ("id", "other"),
        ("customer_id", "other"),
        ("product_code", "other"),
        ("program", "standard_cashback"),
        ("merchant", "other"),
        ("mcc_codes", ["5814"]),
        ("valid_from", "2026-07-02"),
        ("valid_until", "2026-08-01"),
        ("rate", 0.2),
        ("per_purchase_cap", 99),
        ("activation_required", False),
        ("replaces_standard", False),
        ("active", False),
    ],
)
def test_matching_terms_guard(field, value):
    from upgrade_bank2_multisource import with_promotion_source_alternatives

    p = utils.BANKING_RU_DATA_DIR
    db = json.loads((p / "db.json").read_text())
    task = deepcopy(
        next(
            t for t in json.loads((p / "tasks.json").read_text()) if t["id"] == "Bank 2"
        )
    )
    if field:
        db["promotions"]["promo_730415"][field] = value
    with_promotion_source_alternatives(task, db, p)
    rules = [
        r
        for r in task["evaluation_criteria"]["result_evidence"]
        if r["evidence_id"].startswith("bank2:v4:offer:")
    ]
    assert rules
    assert all(bool(r.get("alternatives")) == (field is None) for r in rules)
    before = deepcopy(task)
    with_promotion_source_alternatives(task, db, p)
    assert task == before


def test_changed_document_does_not_inherit_old_authorization(tmp_path):
    from upgrade_bank2_multisource import with_promotion_source_alternatives

    p = utils.BANKING_RU_DATA_DIR
    task = deepcopy(
        next(
            t for t in json.loads((p / "tasks.json").read_text()) if t["id"] == "Bank 2"
        )
    )
    db = json.loads((p / "db.json").read_text())
    target = tmp_path / "corpus/knowledge/reward_partner_offer.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        (p / "corpus/knowledge/reward_partner_offer.md")
        .read_text()
        .replace("10%", "20%")
    )
    with_promotion_source_alternatives(task, db, tmp_path)
    assert all(
        not r.get("alternatives")
        for r in task["evaluation_criteria"]["result_evidence"]
        if r["evidence_id"].startswith("bank2:v4:offer:")
    )
