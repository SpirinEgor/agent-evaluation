"""Accept different optimal subsets; reject permissible but suboptimal outcomes."""

import gzip
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru import environment, file_pilot, utils
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session
from tests.utils.banking_parent_fixture import copy_historical_data, restore_bank2_v5

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/banking_ru"))
C, A, B, E, CASE = (
    "gromov_a_1187",
    "txn_804219",
    "txn_804227",
    "txn_804205",
    "case_610903",
)


@pytest.fixture(scope="session")
def large_data(tmp_path_factory):
    from upgrade_bank2_feasible_offers import migrate as feasible
    from upgrade_bank2_history import migrate as history
    from upgrade_bank2_large_selection import migrate
    from upgrade_bank2_posting_order import migrate as posting
    from upgrade_bank2_selection import migrate as selection

    p = tmp_path_factory.mktemp("bank2-large") / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    restore_bank2_v5(p)
    for migration in [history, feasible, posting, selection, migrate]:
        migration(p)
    return p


@pytest.fixture
def large(large_data, monkeypatch):
    for mod in [environment, file_pilot, utils]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", large_data),
            ("BANKING_RU_DB_PATH", large_data / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", large_data / "tasks.json"),
        ]:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, value)
    return next(t for t in environment.get_tasks() if t.id == "Bank 2")


def received(r):
    assert not r.is_error, r
    try:
        return json.loads(r.content[0].text)
    except json.JSONDecodeError:
        return r.content[0].text


def test_large_optimum_and_distinct_independent_witnesses(large):
    from bank2_resolution_oracle import calculate
    from upgrade_bank2_large_selection import components, migrate

    from tau2.domains.banking_ru.data_model import BankingDB
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    p = utils.BANKING_RU_DATA_DIR
    raw = json.loads((p / "db.json").read_text())
    calc = calculate(raw)
    assert calc["maximum"] == 5500 and calc["case_total"] == 2937
    assert calc["payouts"] == {A: 1375, B: 4125}
    offer = BankingDB.model_validate(raw).reward_resolution_offers["offer_640201"]
    assert len(offer.components) == 120
    assert len({x.exclusive_group for x in offer.components}) == 40
    witnesses = components()[1:]
    assert len(witnesses[0]) == len(witnesses[1]) == 20
    assert witnesses[0] != witnesses[1] and witnesses[0] != calc["component_ids"]
    for ids in witnesses:
        assert selected_amounts(offer, ids) == (5500, {A: 1375, B: 4125})
    before = {str(f): f.read_bytes() for f in p.rglob("*") if f.is_file()}
    migrate(p)
    assert before == {str(f): f.read_bytes() for f in p.rglob("*") if f.is_file()}


@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("constructed", 1),
        ("alternate", 1),
        ("repeat", 1),
        ("split", 1),
        ("noop", 0),
        ("old_menu", 0),
        ("smaller_subset", 0),
        ("over_budget", 0),
        ("changed_selection", 0),
        ("missing_receipts", 0),
        ("terms_from_document", 1),
        ("terms_missing", 0),
        ("terms_late", 0),
    ],
)
def test_large_selection_whole_grader(large, route, want, tmp_path):
    from upgrade_bank2_large_selection import components

    s = PlatformSession(large, environment.get_environment, max_calls=100, max_errors=5)
    initial_balance = s.env.tools.db.accounts["acc_1187"].balance
    packet, confirmation = None, None
    for action in [] if route == "noop" else large.evaluation_criteria.actions:
        if action.name == "done":
            continue
        args = deepcopy(action.arguments)
        if (
            route in ["terms_from_document", "terms_missing", "terms_late"]
            and action.name == "get_promotions"
        ):
            continue
        if (
            route in ["terms_missing", "terms_late"]
            and action.name == "file_read"
            and args.get("path") == "/knowledge/reward_partner_offer.md"
        ):
            continue
        if (
            route == "missing_receipts"
            and action.name == "get_reward_resolution_offers"
        ):
            continue
        if (
            action.name == "ask_client"
            and args.get("request", {}).get("type") == "reward_resolution_confirmation"
        ):
            packet = args["request"]
            if route in ["constructed", "alternate", "over_budget"]:
                packet["component_ids"] = components()[1 if route != "alternate" else 2]
            if route == "over_budget":
                offer = s.env.tools.db.reward_resolution_offers["offer_640201"]
                selected_groups = {
                    p.exclusive_group
                    for p in offer.components
                    if p.id in packet["component_ids"]
                }
                packet["component_ids"].append(
                    next(
                        p.id
                        for p in offer.components
                        if p.exclusive_group not in selected_groups
                    )
                )
            if route == "smaller_subset":
                packet["component_ids"].pop()
                offer = s.env.tools.db.reward_resolution_offers["offer_640201"]
                total = sum(
                    round(p.amount * 100)
                    for p in offer.components
                    if p.id in packet["component_ids"]
                )
                packet.update(
                    total_amount=total / 100,
                    payouts=[
                        dict(transaction_id=A, amount=total / 400),
                        dict(transaction_id=B, amount=total * 3 / 400),
                    ],
                    resulting_claims=[
                        dict(transaction_id=A, amount=(2437 - total // 4) / 100),
                        dict(transaction_id=B, amount=(5300 - total * 3 // 4) / 100),
                        dict(transaction_id=E, amount=7),
                    ],
                )
            if route == "old_menu":
                packet.update(
                    offer_id="offer_630181",
                    component_ids=[
                        "part_941007",
                        "part_941015",
                        "part_941030",
                        "part_941035",
                        "part_941043",
                        "part_941051",
                    ],
                    total_amount=54.92,
                    payouts=[
                        dict(transaction_id=A, amount=13.19),
                        dict(transaction_id=B, amount=41.73),
                    ],
                    resulting_claims=[
                        dict(transaction_id=A, amount=11.18),
                        dict(transaction_id=B, amount=11.27),
                        dict(transaction_id=E, amount=7),
                    ],
                )
            confirmation = received(s.call(action.name, args))["confirmation_id"]
        elif action.name == "execute_reward_resolution":
            for key in ["offer_id", "component_ids", "payouts", "total_amount"]:
                args[key] = deepcopy(packet[key])
            args["confirmation_id"] = confirmation
            if route == "changed_selection":
                args["component_ids"] = args["component_ids"][:-1]
            r = s.call(action.name, args)
            if route in ["over_budget", "changed_selection"]:
                assert r.is_error and not s.env.tools.db.reward_resolution_executions
                break
            received(r)
            if route == "terms_late":
                received(
                    s.call("file_read", {"path": "/knowledge/reward_partner_offer.md"})
                )
            if route == "repeat":
                args["component_ids"].reverse()
                received(s.call(action.name, args))
        elif action.name == "amend_cashback_investigation":
            args["items"] = [
                x for x in packet["resulting_claims"] if x["transaction_id"] in [A, B]
            ]
            if route == "split":
                for i, item in enumerate(reversed(args["items"])):
                    received(
                        s.call(
                            action.name,
                            {**args, "items": [item], "expected_revision": 3 + i},
                        )
                    )
            else:
                received(s.call(action.name, args))
        else:
            received(s.call(action.name, args))
    s.call("done", {})
    half, full = s.grade(), grade_full_session(s).reward_info
    out = Path(os.environ.get("BANK2_LARGE_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    payload = dict(
        half=half.model_dump(mode="json"),
        full=full.model_dump(mode="json"),
        simulation=s.to_simulation_run().model_dump(mode="json"),
        calls=len(s.log),
        errors=s.errors,
    )
    with gzip.open(out / (route + ".json.gz"), "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    assert half.reward == full.reward == want, (half, full)
    if want:
        case = s.env.tools.db.cases[CASE]
        assert case.amount == 29.37
        assert {x.transaction_id: x.amount for x in case.reward_items} == {
            A: 10.62,
            B: 11.75,
            E: 7,
        }
        assert (
            round(s.env.tools.db.accounts["acc_1187"].balance - initial_balance, 2)
            == 62
        )
    if route in ["old_menu", "smaller_subset"] or want:
        assert s.errors == 0
    if route in ["old_menu", "smaller_subset"]:
        assert s.env.tools.assert_reward_resolution_attempts(C, CASE)
