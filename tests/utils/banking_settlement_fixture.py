"""Pinned settlement state shared by reward API controls."""

import json
from copy import deepcopy

from tau2.domains.banking_ru.data_model import BankingDB

C, B = "gromov_a_1187", "txn_552291"
PERIOD = "2026-07"
KEY = C + ":" + PERIOD


def ready(late=True, unknown=True):
    """Build isolated settlement API state from standalone fixture data."""
    from pathlib import Path

    pinned = Path(__file__).resolve().parents[1] / "fixtures/banking_ru/settlement_api"
    raw = json.loads((pinned / "db.json").read_text())
    from tau2.domains.banking_ru.file_corpus import FileCorpus
    from tau2.domains.banking_ru.file_pilot import get_file_environment

    env = get_file_environment(
        pinned,
        "banking_ru",
        structured_identity=True,
        snapshot=(FileCorpus(pinned), BankingDB.model_validate(raw)),
    )
    db = env.tools.db
    db.session.verified_customers.append(C)
    assert "cashback_settlements" in type(db).model_fields
    db.cashback_settlements = {}
    db.cashback_adjustment_operations = {}
    db.cashback_state_revisions[KEY] = 1
    db.cashback_execution_profiles[C] = {"first_adjustment_unknown": unknown}
    if late:
        credit = deepcopy(db.transactions["txn_552298"].model_dump())
        credit.update(
            id="settlement-B7",
            original_transaction_id=B,
            amount=7,
            status="posted",
            date=db.today,
            posted_at=None,
        )
        db.cashback_settlements["july-processing"] = {
            "id": "july-processing",
            "customer_id": C,
            "period": PERIOD,
            "status": "pending",
            "credits": [credit],
            "ledger_ids": [],
        }
    return env
