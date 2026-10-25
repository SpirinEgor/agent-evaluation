"""Real-bank calls and whole DB x ENV x ACTION for the menu-selection scenario."""

import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru import environment, file_pilot, utils
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session
from tests.utils.banking_parent_fixture import copy_historical_data

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/banking_ru"))
C, A, B, E, CASE = (
    "gromov_a_1187",
    "txn_804219",
    "txn_804227",
    "txn_804205",
    "case_610903",
)


@pytest.fixture
def selection(tmp_path, monkeypatch):
    p = tmp_path / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    restore_bank2_v5(p)
    from upgrade_bank2_feasible_offers import migrate as feasible
    from upgrade_bank2_history import migrate as history
    from upgrade_bank2_posting_order import migrate as posting
    from upgrade_bank2_selection import migrate

    history(p)
    feasible(p)
    posting(p)
    db, tasks, _ = migrate(p)
    for mod in [environment, file_pilot, utils]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, value)
    return next(t for t in environment.get_tasks() if t.id == "Bank 2")


def result(r):
    assert not r.is_error, r
    try:
        return json.loads(r.content[0].text)
    except json.JSONDecodeError:
        return r.content[0].text


def test_author_oracle_unique_optimum_and_migration_idempotent(selection):
    from bank2_resolution_oracle import calculate
    from upgrade_bank2_selection import migrate

    db = json.loads((utils.BANKING_RU_DATA_DIR / "db.json").read_text())
    d = calculate(db)
    assert d["maximum"] == 5492 and d["case_total"] == 2945
    assert d["payouts"] == {A: 1319, B: 4173}
    assert d["claims"] == {A: 1118, B: 1127, E: 700}
    assert len(d["offers"]["offer_630181"]["optimal_selections"]) == 1
    before = {
        str(p): p.read_bytes()
        for p in utils.BANKING_RU_DATA_DIR.rglob("*")
        if p.is_file()
    }
    migrate(utils.BANKING_RU_DATA_DIR)
    assert before == {
        str(p): p.read_bytes()
        for p in utils.BANKING_RU_DATA_DIR.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("permuted", 1),
        ("repeat", 1),
        ("split", 1),
        ("noop", 0),
        ("suboptimal", 0),
        ("duplicate", 0),
        ("over_budget", 0),
        ("changed_selection", 0),
        ("missing_receipts", 0),
    ],
)
def test_selection_whole_grader(selection, route, want, tmp_path):
    s = PlatformSession(
        selection, environment.get_environment, max_calls=100, max_errors=5
    )
    initial = s.env.tools.db.model_dump(mode="json")
    packet = None
    consent = None
    for a in [] if route == "noop" else selection.evaluation_criteria.actions:
        if a.name == "done":
            continue
        args = deepcopy(a.arguments)
        if route == "missing_receipts" and a.name == "get_reward_resolution_offers":
            continue
        if (
            a.name == "ask_client"
            and args.get("request", {}).get("type") == "reward_resolution_confirmation"
        ):
            packet = args["request"]
            if route == "permuted":
                packet["component_ids"].reverse()
                packet["payouts"].reverse()
                packet["resulting_claims"].reverse()
            if route == "suboptimal":
                packet.update(
                    offer_id="offer_620157",
                    component_ids=[],
                    total_amount=24.25,
                    payouts=[
                        dict(transaction_id=A, amount=11),
                        dict(transaction_id=B, amount=13.25),
                    ],
                    resulting_claims=[
                        dict(transaction_id=A, amount=13.37),
                        dict(transaction_id=B, amount=39.75),
                        dict(transaction_id=E, amount=7),
                    ],
                )
            if route == "duplicate":
                packet["component_ids"].append(packet["component_ids"][0])
            if route == "over_budget":
                packet["component_ids"].append("part_941000")
            consent = result(s.call(a.name, args))["confirmation_id"]
        elif a.name == "execute_reward_resolution":
            for k in ["offer_id", "component_ids", "payouts", "total_amount"]:
                args[k] = deepcopy(packet[k])
            args["confirmation_id"] = consent
            if route == "changed_selection":
                args["component_ids"] = args["component_ids"][:-1]
            r = s.call(a.name, args)
            if route in ["duplicate", "over_budget", "changed_selection"]:
                assert r.is_error
                assert not s.env.tools.db.reward_resolution_executions
                break
            result(r)
            if route == "repeat":
                args["component_ids"].reverse()
                result(s.call(a.name, args))
        elif a.name == "amend_cashback_investigation":
            args["items"] = [
                x for x in packet["resulting_claims"] if x["transaction_id"] in [A, B]
            ]
            if route == "split":
                for n, item in enumerate(reversed(args["items"])):
                    result(
                        s.call(
                            a.name,
                            {**args, "items": [item], "expected_revision": 3 + n},
                        )
                    )
            else:
                result(s.call(a.name, args))
        else:
            result(s.call(a.name, args))
    s.call("done", {})
    half, full = s.grade(), grade_full_session(s).reward_info
    out = Path(os.environ.get("BANK2_SELECTION_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    (out / (route + ".json")).write_text(
        json.dumps(
            dict(
                half=half.model_dump(mode="json"),
                full=full.model_dump(mode="json"),
                simulation=s.to_simulation_run().model_dump(mode="json"),
                initial=initial,
                calls=len(s.log),
                errors=s.errors,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    assert half.reward == full.reward == want, (half, full)
    if want:
        case = s.env.tools.db.cases[CASE]
        assert case.amount == 29.45
        assert {x.transaction_id: x.amount for x in case.reward_items} == {
            A: 11.18,
            B: 11.27,
            E: 7,
        }
        assert (
            round(
                s.env.tools.db.accounts["acc_1187"].balance
                - initial["accounts"]["acc_1187"]["balance"],
                2,
            )
            == 61.92
        )
        assert s.errors == 0
        assert len(s.log) < 50
