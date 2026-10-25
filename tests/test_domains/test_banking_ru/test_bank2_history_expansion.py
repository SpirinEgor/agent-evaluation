"""Whole-grader controls for the larger Bank2 contract history."""

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
C, A, B, CASE = "gromov_a_1187", "txn_804219", "txn_804227", "case_610903"


def migration():
    spec = importlib.util.find_spec("upgrade_bank2_history")
    assert spec is not None, "Missing the versioned history-expansion migration"
    return importlib.import_module("upgrade_bank2_history")


def test_history_migration_preserves_unrelated_tasks_and_is_idempotent(tmp_path):
    p = tmp_path / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    restore_bank2_v5(p)
    before = json.loads((p / "tasks.json").read_text())
    migration().migrate(p)
    after = json.loads((p / "tasks.json").read_text())
    for left, right in zip(before, after):
        if left["id"] != "Bank 2":
            left["evaluation_criteria"].pop("tool_access_policy", None)
            right["evaluation_criteria"].pop("tool_access_policy", None)
            assert left == right
    from bank2_resolution_oracle import calculate

    db = json.loads((p / "db.json").read_text())
    calc = calculate(db)
    assert calc["maximum"] == 2400
    assert calc["claims"] == {A: 1337, B: 4000, "txn_804205": 700}
    assert calc["case_total"] == 6037
    from bank2_multisource_oracle import calculate as independent_obligations

    from tau2.domains.banking_ru.data_model import BankingDB
    from tau2.domains.banking_ru.reward_workflows import reward_obligations

    oracle = independent_obligations(db, include_pending=False)
    runtime = reward_obligations(BankingDB.model_validate(db), C, "2026-07")
    assert oracle["rows"][A]["entitlement"] == runtime[A]["entitlement"] == 4937
    assert (
        sum(
            oracle["rows"][migration().purchase_id(i)]["entitlement"] for i in range(24)
        )
        == 19273
    )
    for tid, row in oracle["rows"].items():
        assert {
            k: row[k] for k in ["program", "entitlement", "paid", "gap"]
        } == runtime[tid]
    changed = deepcopy(db)
    changed["transactions"][migration().refund_id(23)]["amount"] += 1
    assert independent_obligations(changed)["rows"][A]["entitlement"] == 4942
    assert (
        reward_obligations(BankingDB.model_validate(changed), C, "2026-07")[A][
            "entitlement"
        ]
        == 4942
    )
    hashes = {
        str(f.relative_to(p)): f.read_bytes() for f in p.rglob("*") if f.is_file()
    }
    migration().migrate(p)
    assert hashes == {
        str(f.relative_to(p)): f.read_bytes() for f in p.rglob("*") if f.is_file()
    }


@pytest.fixture
def expanded(tmp_path, monkeypatch):
    p = tmp_path / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    restore_bank2_v5(p)
    migration().migrate(p)
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    return next(t for t in environment.get_tasks() if t.id == "Bank 2")


def payload(result):
    assert not result.is_error, result
    try:
        return json.loads(result.content[0].text)
    except json.JSONDecodeError:
        return result.content[0].text


@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("reference_repeat", 1),
        ("broad_pages", 1),
        ("split_amend", 1),
        ("noop", 0),
        ("old_claim", 0),
        ("missing_refunds", 0),
        ("failed_forbidden", 0),
    ],
)
def test_expanded_history_whole_grader(expanded, route, want, tmp_path):
    s = PlatformSession(
        expanded, environment.get_environment, max_calls=60, max_errors=5
    )
    consent = None
    for a in [] if route == "noop" else expanded.evaluation_criteria.actions:
        if a.name == "done":
            continue
        args = deepcopy(a.arguments)
        if route == "broad_pages" and a.name == "get_transactions":
            if a.action_id == "bank2:v4:purchases":
                first = payload(
                    s.call(
                        a.name,
                        dict(
                            customer_id=C,
                            posted_from="2026-07-01",
                            posted_to="2026-08-28",
                            limit=50,
                        ),
                    )
                )
                assert int(first["total"]) > 50
                second = payload(
                    s.call(
                        a.name,
                        dict(
                            customer_id=C,
                            posted_from="2026-07-01",
                            posted_to="2026-08-28",
                            limit=50,
                            offset=50,
                        ),
                    )
                )
                assert len(first["items"]) + len(second["items"]) == int(first["total"])
            continue
        if route == "missing_refunds" and a.name == "get_transactions":
            if a.action_id == "bank2:v4:related":
                payload(s.call(a.name, {**args, "posted_to": "2026-08-17"}))
                payload(s.call(a.name, {**args, "posted_from": "2026-08-19"}))
            else:
                payload(s.call(a.name, args))
        elif (
            a.name == "ask_client"
            and args.get("request", {}).get("type") == "reward_resolution_confirmation"
        ):
            if route == "old_claim":
                next(
                    x
                    for x in args["request"]["resulting_claims"]
                    if x["transaction_id"] == A
                )["amount"] = 14
            consent = payload(s.call(a.name, args)).get("confirmation_id")
        elif a.name == "execute_reward_resolution":
            args["confirmation_id"] = consent
            result = s.call(a.name, args)
            if route == "old_claim":
                assert result.is_error
                break
            payload(result)
            if route == "failed_forbidden":
                assert s.call(
                    "execute_cashback_adjustment",
                    dict(
                        permission_id="cap_620184",
                        transaction_id=A,
                        amount=1,
                        expected_revision=3,
                        idempotency_key="extra",
                    ),
                ).is_error
        elif a.name == "amend_cashback_investigation" and route == "split_amend":
            for i, item in enumerate(args["items"]):
                payload(
                    s.call(
                        a.name, {**args, "items": [item], "expected_revision": 3 + i}
                    )
                )
        else:
            payload(s.call(a.name, args))
    s.call("done", {})
    half = s.grade()
    full = grade_full_session(s).reward_info
    out = Path(os.environ.get("BANK2_HISTORY_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    (out / (route + ".json")).write_text(
        json.dumps(
            dict(
                half=half.model_dump(mode="json"),
                full=full.model_dump(mode="json"),
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
        assert case.amount == 60.37
        assert {x.transaction_id: x.amount for x in case.reward_items} == {
            A: 13.37,
            B: 40,
            "txn_804205": 7,
        }
        assert s.errors == 0 and len(s.log) < 50
