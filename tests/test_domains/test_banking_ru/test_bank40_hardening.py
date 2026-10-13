"""Bank40 billing-cycle classification and partial duplicate-fee correction."""

import json
import os
from pathlib import Path

import pytest

from tau2.data_model.message import AssistantMessage, Tick, ToolMessage
from tau2.data_model.simulation import SimulationRun
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.orchestrator.modes import CommunicationMode
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def task40():
    return next(t for t in get_tasks() if t.id == "Bank 40")


def test_bank40_billing_period_is_discoverable_before_correction():
    session = PlatformSession(task40(), get_environment)
    for action in task40().evaluation_criteria.actions:
        if action.name == "refund_fee":
            break
        session.call(action.name, dict(action.arguments))
    response = session.call("get_transaction_details", {"transaction_id": "txn_088215"})
    payload = json.loads(response.content[0].text)
    assert payload.get("billing_period") == {"start": "2026-08-28", "end": "2026-09-27"}
    assert payload.get("tariff_revision") == "standard-cycle-2026-08-28"
    assert payload["amount"] == 249
    original = session.call("get_transaction_details", {"transaction_id": "txn_088214"})
    first = json.loads(original.content[0].text)
    assert first["amount"] == 199
    assert first["billing_period"] == {"start": "2026-07-28", "end": "2026-08-27"}
    assert first["tariff_revision"] == "standard-cycle-2026-07-28"
    profile = session.call("get_customer_profile", {"customer_id": "solovyova_a_7734"})
    history = json.loads(profile.content[0].text)["servicing_contract_history"]
    assert [(row["effective_from"], row["monthly_fee"]) for row in history] == [
        ("2026-07-28", 199),
        ("2026-08-28", 249),
    ]
    assert history[0]["qualifying_period"] == {
        "start": "2026-06-28",
        "end": "2026-07-27",
    }
    assert history[1]["qualifying_period"] == {
        "start": "2026-07-28",
        "end": "2026-08-27",
    }
    assert [
        (row["avg_monthly_balance"], row["monthly_turnover"]) for row in history
    ] == [(4200, 8000), (4200, 8000)]


CASES = [
    ("reference", 1),
    ("details", 1),
    ("details_selected", 1),
    ("no_catalog", 1),
    ("refund_details", 1),
    ("no_calculate", 1),
    ("noop", 0),
    ("wrong_fee_169", 0),
    ("old_119", 0),
    ("full_249", 0),
    ("processing_139", 0),
    ("early_repaired", 0),
    ("wrong_attempt_repaired", 0),
    ("missing_history", 0),
    ("missing_period_receipt", 0),
    ("missing_done", 0),
]


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize("route,expected", CASES, ids=[c[0] for c in CASES])
def test_bank40_period_routes(route, expected, mode):
    task = task40()
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=100)
    correction = {
        "transaction_id": "txn_088219",
        "amount": 169.0,
        "request": {"type": "fee_correction"},
    }
    if route == "early_repaired":
        session.call("refund_fee", correction)
    if route != "noop":
        for action in task.evaluation_criteria.actions:
            if route == "no_catalog" and action.name == "get_tariff":
                continue
            if route == "missing_history" and action.name == "get_customer_profile":
                continue
            if route == "missing_period_receipt" and action.name == "get_transactions":
                continue
            if (
                route in {"details", "details_selected"}
                and action.name == "get_transactions"
            ):
                for transaction_id in [
                    "txn_088214",
                    "txn_088215",
                    "txn_088216",
                    "txn_088217",
                    "txn_088218",
                    "txn_088219",
                ]:
                    if route == "details_selected" and transaction_id == "txn_088216":
                        continue
                    session.call(
                        "get_transaction_details", {"transaction_id": transaction_id}
                    )
                continue
            if action.name != "refund_fee":
                session.call(action.name, dict(action.arguments))
                continue
            args = dict(correction)
            if route == "wrong_fee_169":
                args["transaction_id"] = "txn_088215"
            if route in {"old_119", "full_249", "processing_139"}:
                args["amount"] = {
                    "old_119": 119,
                    "full_249": 249,
                    "processing_139": 139,
                }[route]
            if route == "wrong_attempt_repaired":
                session.call(
                    "refund_fee",
                    {**args, "transaction_id": "txn_088214", "amount": 249},
                )
            session.call("refund_fee", args)
            if route == "refund_details":
                refund = next(r for c, r in session.log if c.name == "refund_fee")
                receipt = json.loads(refund.content)
                session.call(
                    "get_transaction_details",
                    {"transaction_id": receipt["refund_transaction_id"]},
                )
    if route != "missing_done":
        session.call("done", {})
    if mode == "HALF":
        reward = session.grade()
    elif route != "missing_done":
        reward = grade_full_session(session).reward_info
    else:
        ticks = [
            Tick(
                tick_id=i,
                timestamp=session.created_at.isoformat(),
                agent_chunk=m if isinstance(m, AssistantMessage) else None,
                agent_tool_calls=(m.tool_calls or [])
                if isinstance(m, AssistantMessage)
                else [],
                agent_tool_results=[m] if isinstance(m, ToolMessage) else [],
            )
            for i, m in enumerate(session.trajectory())
        ]
        simulation = SimulationRun(
            id=session.token,
            task_id=task.id,
            start_time=session.created_at.isoformat(),
            end_time=session.created_at.isoformat(),
            duration=0,
            termination_reason=session.termination_reason(),
            ticks=ticks,
            mode=CommunicationMode.FULL_DUPLEX.value,
            info={"platform": "mcp", "mcp_events": session.mcp_events},
        )
        reward = evaluate_simulation(
            simulation=simulation,
            task=task,
            evaluation_type=EvaluationType.ALL,
            solo_mode=True,
            domain=session.env.domain_name,
            mode=CommunicationMode.FULL_DUPLEX,
            strict_replay=True,
            env_kwargs=session.env.evaluation_env_kwargs,
        )
    if destination := os.environ.get("BANK40_HARDENING_EVIDENCE"):
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": reward.model_dump(mode="json"),
                    "errors": session.errors,
                    "trajectory": [
                        message.model_dump(mode="json")
                        for message in session.trajectory()
                    ],
                    "mcp_events": session.mcp_events,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert reward.final_reward == expected, reward.info
    if expected:
        db = session.env.tools.db
        assert db.accounts["acc_7734"].balance == 3951
        assert not db.transactions["txn_088214"].fee_refunded
        assert not db.transactions["txn_088215"].fee_refunded
        assert db.transactions["txn_088219"].fee_refunded
        posted = [
            t
            for t in db.transactions.values()
            if t.original_transaction_id == "txn_088219"
            and t.kind == "fee_refund"
            and t.status == "posted"
        ]
        assert sorted(t.amount for t in posted) == [80, 169]
        assert db.transactions["txn_088218"].status == "processing"


def test_bank40_migration_is_addressed_and_idempotent(tmp_path):
    from copy import deepcopy

    from scripts.banking_ru.harden_bank40 import migrate, with_contract

    base = Path(__file__).resolve().parents[3] / "data/tau2/domains/banking_ru"
    tasks = json.loads((base / "tasks.json").read_text())
    db = json.loads((base / "db.json").read_text())
    before_tasks, before_db = deepcopy(tasks), deepcopy(db)
    after_tasks, after_db = with_contract(tasks, db)
    assert tasks == before_tasks and db == before_db
    assert [t for t in after_tasks if t["id"] != "Bank 40"] == [
        t for t in before_tasks if t["id"] != "Bank 40"
    ]
    assert with_contract(after_tasks, after_db) == (after_tasks, after_db)
    for key in db:
        if key not in {"transactions", "customers"}:
            assert db[key] == after_db[key]
    for key, customer in db["customers"].items():
        if key != "solovyova_a_7734":
            assert customer == after_db["customers"][key]
    for key, transaction in db["transactions"].items():
        if key not in {
            "txn_088214",
            "txn_088215",
            "txn_088217",
            "txn_088218",
            "txn_088219",
        }:
            assert transaction == after_db["transactions"][key]
    for filename in ["tasks.json", "db.json"]:
        (tmp_path / filename).write_text((base / filename).read_text())
    for filename in ["knowledge/kb_121.md", "policies/fees_rewards.md"]:
        path = tmp_path / "corpus" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((base / "corpus" / filename).read_text())
    migrate(tmp_path)
    migrate(tmp_path, check=True)
    original_bytes = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    migrate(tmp_path)
    assert all(p.read_bytes() == contents for p, contents in original_bytes.items())
