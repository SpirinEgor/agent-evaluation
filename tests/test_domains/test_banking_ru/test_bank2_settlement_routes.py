"""Bounded whole-grader contracts; literal monetary oracle in three scenarios."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru import environment, file_pilot, utils
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

C, A, B = "gromov_a_1187", "txn_552290", "txn_552291"
CASE, PERIOD = "case-standard-july", "2026-07"
VERSION = "2026-10-14-bank2-settlement-v3"


@pytest.fixture
def scenario(tmp_path, monkeypatch, request):
    import shutil

    source = Path(
        os.environ.get("BANK2_SETTLEMENT_DATA", "data/tau2/domains/banking_ru")
    ).resolve()
    p = tmp_path / "data"
    shutil.copytree(source, p)
    db = json.loads((p / "db.json").read_text())
    tasks = json.loads((p / "tasks.json").read_text())
    t = next(t for t in tasks if t["id"] == "Bank 2")
    assert t["scenario_version"] == VERSION, "settlement scenario not implemented"
    kind = request.param
    if kind == "unknown_only":
        db["cashback_settlements"] = {}
        for a in t["evaluation_criteria"]["actions"]:
            if a["name"] in [
                "execute_cashback_adjustment",
                "amend_cashback_investigation",
            ]:
                a["arguments"]["expected_revision"] -= 1
            if a["name"] == "amend_cashback_investigation":
                a["arguments"]["items"][1]["amount"] = 20
        t["evaluation_criteria"]["actions"] = [
            a
            for a in t["evaluation_criteria"]["actions"]
            if a["action_id"] not in ["bank2:v3:refresh", "bank2:v3:late-details"]
        ]
        t["evaluation_criteria"]["result_evidence"] = [
            e
            for e in t["evaluation_criteria"]["result_evidence"]
            if not e["evidence_id"].startswith("bank2:v3:late:")
        ]
        for a in t["evaluation_criteria"]["env_assertions"]:
            if a["func_name"] == "assert_cashback_investigation":
                a["arguments"]["amount"] = 38
                a["arguments"]["items"][1]["amount"] = 20
    if kind == "late_only":
        db["cashback_execution_profiles"][C]["first_adjustment_unknown"] = False
    (p / "db.json").write_text(json.dumps(db, ensure_ascii=False))
    (p / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False))
    import hashlib

    manifest = json.loads((p / "manifest.json").read_text())
    manifest["legacy_db_sha256"] = hashlib.sha256(
        (p / "db.json").read_bytes()
    ).hexdigest()
    (p / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    yield environment.get_tasks()[1], kind
    shutil.rmtree(p)


def data(result):
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "scenario,route,want",
    [
        ("joint", route, want)
        for route, want in [
            ("reference", 1),
            ("noop", 0),
            ("reserve_first", 1),
            ("split", 1),
            ("stale", 1),
            ("pending", 1),
            ("same_key", 1),
            ("split_amendments", 1),
            ("read_attachment", 1),
            ("repeat_refresh", 1),
            ("changed_key_content", 0),
            ("old38", 0),
            ("paid_in_claim", 0),
            ("over_authority", 0),
            ("unresolved", 0),
            ("late_result", 0),
            ("guess_revision", 0),
            ("equivalent_source", 1),
            ("ledger_confirmation", 1),
            ("pending_late_source", 0),
            ("forbidden_stale", 0),
            ("stale_current", 1),
            ("pending_case_plan", 1),
            ("stale_plan", 1),
            ("reserve_unresolved", 0),
        ]
    ]
    + [
        (kind, route, want)
        for kind in ["late_only", "unknown_only"]
        for route, want in [("reference", 1), ("noop", 0)]
    ],
    indirect=["scenario"],
)
def test_bank2_state_whole_grader(scenario, mode, route, want):
    task, kind = scenario
    if kind == "unknown_only" and route in ["old38", "pending", "repeat_refresh"]:
        pytest.skip("requires late batch")
    if kind == "late_only" and route in ["unresolved", "late_result"]:
        pytest.skip("requires unknown response")
    s = PlatformSession(task, environment.get_environment, max_calls=200, max_errors=50)
    actions = list(task.evaluation_criteria.actions)
    payout = next(a for a in actions if a.name == "execute_cashback_adjustment")
    claim = next(a for a in actions if a.name == "amend_cashback_investigation")
    if route in ["reserve_first", "reserve_unresolved"]:
        actions.remove(claim)
        actions.insert(actions.index(payout), claim)
    if route != "noop":
        for a in actions:
            args = deepcopy(a.arguments)
            if route == "pending_late_source" and a.name == "file_read":
                continue
            if route == "equivalent_source" and a.action_id == "bank2:v2:source":
                args["path"] = "/knowledge/kb_cashback_adjustment_authority.md"
            if a.name == "get_cashback_adjustment_status":
                continue  # each real execution below is individually confirmed
            if route == "guess_revision" and a.name in [
                "get_cashback_rules",
                "get_cashback_adjustment_permissions",
                "get_cashback_investigations",
            ]:
                continue
            current = s.env.tools.db.cashback_state_revisions[C + ":" + PERIOD]
            if a.name == "refresh_cashback_settlement" and route == "pending_case_plan":
                plan = deepcopy(claim.arguments)
                plan["items"][1]["amount"] = 20
                plan.update(
                    expected_revision=1,
                    reserved_adjustments=[
                        {
                            "permission_id": "adjustment-A",
                            "transaction_id": A,
                            "amount": 12,
                        }
                    ],
                )
                r = s.call(claim.name, plan)
                assert not r.is_error and data(r)["status"] == "settlement_pending"
            if a.name == "refresh_cashback_settlement" and route in [
                "pending",
                "pending_late_source",
            ]:
                r = s.call(payout.name, {**payout.arguments, "expected_revision": 1})
                assert data(r)["status"] == "settlement_pending"
                if route == "pending_late_source":
                    source = next(x for x in actions if x.name == "file_read")
                    s.call(source.name, source.arguments)
            if a == payout:
                args["expected_revision"] = current
                if route == "stale" and kind != "unknown_only":
                    r = s.call(a.name, {**args, "expected_revision": 1})
                    assert data(r)["status"] == "state_changed"
                if route == "over_authority":
                    s.call(a.name, {**args, "amount": 13})
                for index in range(2 if route == "split" else 1):
                    parameters = {
                        **args,
                        "expected_revision": s.env.tools.db.cashback_state_revisions[
                            C + ":" + PERIOD
                        ],
                    }
                    if route == "split":
                        parameters.update(amount=6, idempotency_key=f"part-{index}")
                    r = s.call(a.name, parameters)
                    assert not r.is_error, r
                    result = data(r)
                    if route == "changed_key_content":
                        s.call(a.name, {**parameters, "amount": 5})
                    if route == "same_key":
                        r = s.call(a.name, parameters)
                        assert data(r)["status"] == "succeeded"
                    elif result.get("status") == "execution_unknown" and route not in [
                        "unresolved",
                        "reserve_unresolved",
                    ]:
                        if route == "ledger_confirmation":
                            s.call("get_transactions", {"customer_id": C, "limit": 50})
                            s.call("get_cashback_rules", {"customer_id": C})
                            continue
                        params = {"operation_id": result["operation_id"]}
                        if route == "late_result":
                            pending = s.begin_call(
                                "get_cashback_adjustment_status", params, None
                            )
                            s.call(
                                "get_cashback_adjustment_status",
                                params,
                                attempt=pending,
                            )
                        else:
                            s.call("get_cashback_adjustment_status", params)
                continue
            if a == claim:
                args["expected_revision"] = current
                if route == "forbidden_stale":
                    bad = deepcopy(args)
                    bad["items"][0]["amount"] = 500
                    s.call(a.name, {**bad, "expected_revision": 2})
                if route == "stale_current":
                    r = s.call(a.name, {**args, "expected_revision": 2})
                    assert not r.is_error and data(r)["status"] == "state_changed"
                if route in ["reserve_first", "reserve_unresolved"]:
                    args["reserved_adjustments"] = [
                        {
                            "permission_id": "adjustment-A",
                            "transaction_id": A,
                            "amount": 12,
                        }
                    ]
                if route == "old38":
                    args["items"][1]["amount"] = 20
                if route == "paid_in_claim":
                    args["items"][0]["amount"] = 30
                if route == "split_amendments":
                    for item in args["items"]:
                        rev = s.env.tools.db.cashback_state_revisions[C + ":" + PERIOD]
                        s.call(
                            a.name, {**args, "items": [item], "expected_revision": rev}
                        )
                    continue
            r = s.call(a.name, args)
            if a.name == "refresh_cashback_settlement" and route == "stale_plan":
                plan = deepcopy(claim.arguments)
                plan["items"][1]["amount"] = 20
                plan.update(
                    expected_revision=1,
                    reserved_adjustments=[
                        {
                            "permission_id": "adjustment-A",
                            "transaction_id": A,
                            "amount": 12,
                        }
                    ],
                )
                conflict = s.call(claim.name, plan)
                assert (
                    not conflict.is_error
                    and data(conflict)["status"] == "state_changed"
                )
            if route == "repeat_refresh" and a.name == "refresh_cashback_settlement":
                assert data(s.call(a.name, args))["changed_transaction_ids"] == []
        if route == "read_attachment":
            s.call("get_documents", {"customer_id": C})
    if route == "late_result" and route != "noop":
        s.result_sent(pending)
    s.call("done", {})
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    if dest := os.environ.get("BANK2_SETTLEMENT_EVIDENCE"):
        p = Path(dest)
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{kind}-{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "reward": reward.model_dump(mode="json"),
                    "task": task.model_dump(mode="json"),
                    "messages": [m.model_dump(mode="json") for m in s.trajectory()],
                    "db": s.env.tools.db.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.reward == want, reward.model_dump(mode="json")
    if want:
        case = s.env.tools.db.cases[CASE]
        assert case.amount == (38 if kind == "unknown_only" else 31)
        assert {i.transaction_id: i.amount for i in case.reward_items} == {
            A: 18,
            B: 20 if kind == "unknown_only" else 13,
        }
        assert not case.adjustment_reservations
