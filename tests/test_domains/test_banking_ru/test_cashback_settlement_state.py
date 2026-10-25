"""Real banking calls: late posting, optimistic state and unknown executions."""

import json
from copy import deepcopy
from pathlib import Path

from tau2.data_model.message import ToolCall
from tau2.domains.banking_ru.data_model import BankingDB

C, A, B = "gromov_a_1187", "txn_552290", "txn_552291"
PERIOD, CASE = "2026-07", "case-standard-july"
KEY = C + ":" + PERIOD


def ready(late=True, unknown=True):
    # This mechanism fixture describes settlement-v3, not the current Bank2 task.
    from tests.utils.banking_parent_fixture import FIXTURES, verify_fixture

    pinned = FIXTURES / "fees_rewards_parent_2026_10_14"
    verify_fixture(pinned)
    verify_fixture(FIXTURES / "bank2_settlement_v3_2026_10_14")
    raw = json.loads((pinned / "db.json").read_text())
    parent = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "fixtures/banking_ru/bank2_settlement_v3_2026_10_14/subject.json"
        ).read_text()
    )
    for collection, rows in parent["subject_db"].items():
        raw[collection] = {
            k: v
            for k, v in raw.get(collection, {}).items()
            if not isinstance(v, dict) or (v.get("customer_id") != C and k != C)
        }
        raw[collection].update(rows)
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


def call(env, name, **args):
    r = env.get_response(ToolCall(id=name, name=name, arguments=args))
    assert not r.error, r
    return json.loads(r.content)


def pay(env, amount=12, key="request-1", revision=2):
    return call(
        env,
        "execute_cashback_adjustment",
        permission_id="adjustment-A",
        transaction_id=A,
        amount=amount,
        idempotency_key=key,
        expected_revision=revision,
    )


def refresh(env):
    return call(env, "refresh_cashback_settlement", customer_id=C, period=PERIOD)


def test_pending_is_not_paid_and_refresh_posts_exactly_once():
    e = ready()
    before = e.tools.db.accounts["acc_1187"].balance
    rules = call(e, "get_cashback_rules", customer_id=C)
    assert rules["cycles"][0]["settlements"][0]["status"] == "pending"
    assert "settlement-B7" not in e.tools.db.transactions
    assert pay(e, revision=1)["status"] == "settlement_pending"
    assert refresh(e)["current_revision"] == 2
    assert refresh(e)["changed_transaction_ids"] == []
    assert e.tools.db.accounts["acc_1187"].balance == before + 7


def test_unknown_execution_status_and_identical_retry_do_not_pay_twice():
    e = ready()
    refresh(e)
    r = pay(e)
    assert r["status"] == "execution_unknown"
    status = call(e, "get_cashback_adjustment_status", operation_id=r["operation_id"])
    assert status["status"] == "succeeded" and status["granted"] == 12
    assert status["transaction_id"] == A and status["state_revision"] == 3
    assert (
        pay(e)["status"] == "succeeded"
    )  # lookup precedes consumed authority/revision
    assert (
        e.tools.db.cashback_adjustment_permissions["adjustment-A"]["used_amount"] == 12
    )


def test_stale_revision_is_recoverable_but_authority_violation_is_not_hidden():
    e = ready()
    refresh(e)
    assert pay(e, revision=1) == {
        "status": "state_changed",
        "customer_id": C,
        "period": PERIOD,
        "expected_revision": 1,
        "current_revision": 2,
    }
    r = e.get_response(
        ToolCall(
            id="bad",
            name="execute_cashback_adjustment",
            arguments={
                "permission_id": "adjustment-A",
                "transaction_id": A,
                "amount": 13,
                "idempotency_key": "bad",
                "expected_revision": 1,
            },
        )
    )
    assert r.error and "NOT_AUTHORIZED" in r.content
    assert pay(e)["status"] == "execution_unknown"


def test_reservation_first_then_split_execution_closes_reservations():
    e = ready()
    refresh(e)
    r = call(
        e,
        "amend_cashback_investigation",
        case_id=CASE,
        items=[
            {"transaction_id": A, "amount": 18},
            {"transaction_id": B, "amount": 13},
        ],
        reserved_adjustments=[
            {"permission_id": "adjustment-A", "transaction_id": A, "amount": 12}
        ],
        expected_revision=2,
    )
    assert r["state_revision"] == 3
    first = pay(e, amount=6, revision=3)
    assert first["status"] == "execution_unknown"
    call(e, "get_cashback_adjustment_status", operation_id=first["operation_id"])
    assert pay(e, amount=6, key="request-2", revision=4)["status"] == "succeeded"
    assert not e.tools.db.cases[CASE].adjustment_reservations
    assert e.tools.db.cases[CASE].amount == 31


def test_same_key_different_parameters_is_rejected():
    e = ready(late=False)
    pay(e, amount=6, revision=1)
    r = e.get_response(
        ToolCall(
            id="bad",
            name="execute_cashback_adjustment",
            arguments={
                "permission_id": "adjustment-A",
                "transaction_id": A,
                "amount": 5,
                "idempotency_key": "request-1",
                "expected_revision": 2,
            },
        )
    )
    assert r.error and "Ключ другого запроса" in r.content


def test_foreign_operation_and_new_key_after_consumption_are_rejected():
    e = ready(late=False)
    r = pay(e, revision=1)
    e.tools.db.cashback_adjustment_operations[r["operation_id"]]["customer_id"] = (
        "foreign"
    )
    res = e.get_response(
        ToolCall(
            id="foreign",
            name="get_cashback_adjustment_status",
            arguments={"operation_id": r["operation_id"]},
        )
    )
    assert res.error
    res = e.get_response(
        ToolCall(
            id="new",
            name="execute_cashback_adjustment",
            arguments={
                "permission_id": "adjustment-A",
                "transaction_id": A,
                "amount": 12,
                "idempotency_key": "new-key",
                "expected_revision": 2,
            },
        )
    )
    assert res.error
