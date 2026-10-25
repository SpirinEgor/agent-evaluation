"""Complete-grader validation of feasible maximum, not approved face value."""

import importlib
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
def feasible(tmp_path, monkeypatch):
    from upgrade_bank2_history import migrate

    p = tmp_path / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    restore_bank2_v5(p)
    migrate(p)
    assert importlib.util.find_spec("upgrade_bank2_feasible_offers") is not None
    module = importlib.import_module("upgrade_bank2_feasible_offers")
    module.migrate(p)
    for mod in [environment, utils, file_pilot]:
        for key, val in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(mod, key):
                monkeypatch.setattr(mod, key, val)
    return next(t for t in environment.get_tasks() if t.id == "Bank 2")


def data(result):
    assert not result.is_error, result
    try:
        return json.loads(result.content[0].text)
    except json.JSONDecodeError:
        return result.content[0].text


@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("repeat", 1),
        ("tie", 1),
        ("split", 1),
        ("reordered_docs", 1),
        ("noop", 0),
        ("one_cent_excess", 0),
        ("larger_infeasible", 0),
        ("suboptimal", 0),
        ("missing_consent", 0),
    ],
)
def test_feasible_offers_whole_grader(feasible, route, want, tmp_path):
    s = PlatformSession(
        feasible, environment.get_environment, max_calls=100, max_errors=5
    )
    actions = deepcopy(feasible.evaluation_criteria.actions)
    if route == "reordered_docs":
        docs = iter(reversed([a for a in actions if a.name == "file_read"]))
        actions = [next(docs) if a.name == "file_read" else a for a in actions]
    consent = None
    packet = None
    for a in [] if route == "noop" else actions:
        if a.name == "done":
            continue
        args = deepcopy(a.arguments)
        if (
            a.name == "ask_client"
            and args.get("request", {}).get("type") == "reward_resolution_confirmation"
        ):
            request = args["request"]
            if route == "tie":
                request["offer_id"] = "offer_620159"
            if route == "clock_order":
                next(
                    x for x in request["resulting_claims"] if x["transaction_id"] == A
                )["amount"] = 14
            if route in ["one_cent_excess", "larger_infeasible", "suboptimal"]:
                oid, total, first = {
                    "one_cent_excess": ("offer_620153", 25, 14.38),
                    "larger_infeasible": ("offer_620151", 28, 15),
                    "suboptimal": ("offer_610723", 24, 11),
                }[route]
                second = round(total - first, 2)
                request.update(
                    offer_id=oid,
                    total_amount=total,
                    payouts=[
                        dict(transaction_id=A, amount=first),
                        dict(transaction_id=B, amount=second),
                    ],
                    resulting_claims=[
                        dict(transaction_id=A, amount=round(24.37 - first, 2)),
                        dict(transaction_id=B, amount=round(53 - second, 2)),
                        dict(transaction_id=E, amount=7),
                    ],
                )
            packet = request
            consent = data(s.call(a.name, args)).get("confirmation_id")
        elif a.name == "execute_reward_resolution":
            for key in ["offer_id", "payouts", "total_amount"]:
                args[key] = deepcopy(packet[key])
            args["confirmation_id"] = (
                "invented" if route == "missing_consent" else consent
            )
            result = s.call(a.name, args)
            if route in [
                "one_cent_excess",
                "larger_infeasible",
                "missing_consent",
                "clock_order",
            ]:
                assert result.is_error
                break
            data(result)
            if route == "repeat":
                data(s.call(a.name, args))
        elif a.name == "amend_cashback_investigation":
            args["items"] = [
                x for x in packet["resulting_claims"] if x["transaction_id"] in [A, B]
            ]
            if route == "split":
                for i, item in enumerate(args["items"]):
                    data(
                        s.call(
                            a.name,
                            {**args, "items": [item], "expected_revision": 3 + i},
                        )
                    )
            else:
                data(s.call(a.name, args))
        else:
            data(s.call(a.name, args))
    s.call("done", {})
    half, full = s.grade(), grade_full_session(s).reward_info
    out = Path(os.environ.get("BANK2_OFFERS_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    (out / (route + ".json")).write_text(
        json.dumps(
            dict(
                half=half.model_dump(mode="json"),
                full=full.model_dump(mode="json"),
                simulation=s.to_simulation_run().model_dump(mode="json"),
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
        assert case.amount == 60.12
        assert {x.transaction_id: x.amount for x in case.reward_items} == {
            A: 13.37,
            B: 39.75,
            E: 7,
        }
        assert s.errors == 0 and len(s.log) < 50
    if route == "suboptimal":
        assert s.env.tools.assert_reward_resolution_attempts(C, CASE)


@pytest.mark.parametrize("other_status,rejected", [("open", True), ("closed", False)])
def test_execution_counts_all_open_claims_before_payment(
    feasible, other_status, rejected
):
    """Two open reservations cannot each independently spend the same free gap."""
    s = PlatformSession(
        feasible, environment.get_environment, max_calls=100, max_errors=5
    )
    original = s.env.tools.db.cases[CASE]
    other = original.model_copy(
        deep=True,
        update={
            "id": "case_multiple_claims_control",
            "status": other_status,
            "amount": 5,
            "transaction_ids": [A],
            "reward_items": [
                original.reward_items[0].model_copy(
                    update={"transaction_id": A, "amount": 5}
                )
            ],
        },
    )
    s.env.tools.db.cases[other.id] = other
    initial_balance = s.env.tools.db.accounts["acc_1187"].balance
    initial_transactions = set(s.env.tools.db.transactions)
    for a in feasible.evaluation_criteria.actions:
        if a.name in ["amend_cashback_investigation", "done"]:
            continue
        result = s.call(a.name, deepcopy(a.arguments))
        if a.name == "execute_reward_resolution":
            assert result.is_error == rejected
            if rejected:
                assert (
                    s.env.tools.db.accounts["acc_1187"].balance == initial_balance + 7
                )
                assert set(s.env.tools.db.transactions) - initial_transactions == {
                    "txn_804287"
                }
                assert not s.env.tools.assert_reward_resolution_attempts(C, CASE)
            else:
                assert data(result)["status"] == "succeeded"
                assert s.env.tools.assert_reward_resolution_attempts(C, CASE)
            return
        data(result)
    raise AssertionError("Missing payment action")
