"""Historical routes must not inherit new customer facts after promotion."""

import json
import sys
from pathlib import Path

from tau2.domains.banking_ru import environment, file_pilot, utils
from tests.test_domains.test_banking_ru.test_bank2_resolution import run_reference
from tests.utils.banking_full_grade import grade_full_session
from tests.utils.banking_parent_fixture import copy_historical_data


def test_v5_restore_from_v9_keeps_other_clients_and_passes_whole_grader(
    tmp_path, monkeypatch
):
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))
    from upgrade_bank2_feasible_offers import migrate as feasible
    from upgrade_bank2_history import migrate as history
    from upgrade_bank2_posting_order import migrate as posting
    from upgrade_bank2_selection import migrate as selection

    p = tmp_path / "bank"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    restore_bank2_v5(p)
    history(p)
    feasible(p)
    posting(p)
    db, _, _ = selection(p)
    other = {
        k: v
        for k, v in db["transactions"].items()
        if v["customer_id"] != "gromov_a_1187"
    }
    restore_bank2_v5(p)
    d = json.loads((p / "db.json").read_text())
    assert other == {
        k: v
        for k, v in d["transactions"].items()
        if v["customer_id"] != "gromov_a_1187"
    }
    assert not any(k.startswith(("txn_816", "txn_817")) for k in d["transactions"])
    assert set(d["reward_resolution_offers"]) == {
        "offer_610711",
        "offer_610719",
        "offer_610723",
        "offer_610729",
    }
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    task = next(t for t in environment.get_tasks() if t.id == "Bank 2")
    s = run_reference(task)
    s.call("done", {})
    assert s.grade().reward == grade_full_session(s).reward_info.reward == 1
    assert s.env.tools.db.cases["case_610903"].amount == 61
