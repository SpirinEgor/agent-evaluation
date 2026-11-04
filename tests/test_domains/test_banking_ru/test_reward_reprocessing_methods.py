"""Recovery choices: real net effects, atomic consent and received evidence."""

from copy import deepcopy

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.cashback_state import revision
from tau2.domains.banking_ru.environment import get_environment
from tau2.domains.banking_ru.reward_workflows import reward_obligations
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.test_bank2_linked_goals import (
    RID,
    C,
    data,
    task2,
)
from tests.utils.banking_full_grade import grade_full_session

A, B = "txn_804219", "txn_804227"


@pytest.mark.parametrize("amount", [4, 3.25])
def test_future_bank_credit_is_visible_before_recovery_and_matches_posting(amount):
    session = PlatformSession(task2(), get_environment, max_errors=0)
    db = session.env.tools.db
    profile = db.reward_resolution_correction_profiles["offer_610735"]
    profile.amount = amount
    actions = iter(session.task.evaluation_criteria.actions)
    for action in actions:
        data(session.call(action.name, deepcopy(action.arguments)))
        if action.name == "verify_identity":
            break

    def terms():
        snapshot = db.model_dump()
        rows = data(
            session.call(
                "get_reward_resolution_offers",
                {"customer_id": C, "case_id": "case_610903"},
            )
        )
        assert db.model_dump() == snapshot
        assert all(
            "additional_bank_credit" not in row
            for row in rows
            if row["id"] != "offer_610735"
        )
        return next(row for row in rows if row["id"] == "offer_610735")[
            "additional_bank_credit"
        ]

    advertised = terms()
    assert advertised == {
        "transaction_id": B,
        "amount": amount,
        "currency": "RUB",
        "status": "approved",
        "posting_condition": "first_accepted_execution",
        "guaranteed_if_executed": True,
        "included_in_agreement_amount": False,
        "posting_id": None,
    }
    assert db.reward_reprocessing_cases[RID].selected_method_id is None
    assert not db.reward_resolution_executions
    assert profile.ledger_id not in db.transactions
    assert terms() == advertised
    for action in actions:
        if action.name == "execute_reward_resolution":
            assert terms() == advertised
        data(session.call(action.name, deepcopy(action.arguments)))
        if action.name == "execute_reward_resolution":
            break
    posted = db.transactions[profile.ledger_id]
    assert posted.amount == amount
    assert posted.original_transaction_id == advertised["transaction_id"]
    assert posted.status == "posted"
    assert terms() == {**advertised, "status": "posted", "posting_id": posted.id}
    assert terms() == {**advertised, "status": "posted", "posting_id": posted.id}


@pytest.mark.parametrize("disabled", ["missing_credit", "no_partial", "delayed"])
def test_bank_credit_is_not_promised_when_its_posting_event_is_inactive(disabled):
    session = before_consent()
    db = session.env.tools.db
    if disabled == "missing_credit":
        del db.reward_resolution_correction_profiles["offer_610735"]
    elif disabled == "no_partial":
        del db.reward_resolution_partial_profiles["offer_610735"]
    else:
        db.reward_resolution_execution_profiles["offer_610735"] = "failed_final"
    snapshot = db.model_dump()
    rows = data(
        session.call(
            "get_reward_resolution_offers",
            {"customer_id": C, "case_id": "case_610903"},
        )
    )
    assert all("additional_bank_credit" not in row for row in rows)
    assert db.model_dump() == snapshot


def before_consent():
    session = PlatformSession(task2(), get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        if action.action_id == "bank2:v29:recovery-consent":
            break
        data(session.call(action.name, deepcopy(action.arguments)))
    return session


def packet(session, method="individual_correction", reversals=None):
    return {
        "customer_id": C,
        "request": {
            "type": "reward_reprocessing_confirmation",
            "reprocessing_id": RID,
            "method_id": method,
            "payouts": [
                {
                    "transaction_id": A,
                    "amount": 3.3 if method == "individual_correction" else 5,
                },
                {"transaction_id": B, "amount": 1.2},
            ],
            "reversals": reversals
            if reversals is not None
            else (
                []
                if method == "individual_correction"
                else [{"allocation_id": "alloc_8801_a", "amount": 5.2}]
            ),
            "state_revision": revision(session.env.tools.db, C, "2026-07"),
        },
    }


def execute(session, method, cid):
    return session.call(
        "reprocess_rewards",
        {
            "reprocessing_id": RID,
            "transaction_ids": [A, B],
            "method_id": method,
            "confirmation_id": cid,
        },
    )


@pytest.mark.parametrize(
    "method,delta,gap_a,gap_b",
    [
        ("individual_correction", 4.5, -3.3, -1.2),
        ("period_reallocation", 1, -5, 4),
    ],
)
def test_net_effect_and_idempotent_method_lock(method, delta, gap_a, gap_b):
    session = before_consent()
    db = session.env.tools.db
    before = db.accounts["acc_1187"].balance
    gaps = reward_obligations(db, C, "2026-07")
    answer = data(session.call("ask_client", packet(session, method)))
    data(execute(session, method, answer["confirmation_id"]))
    assert round(db.accounts["acc_1187"].balance - before, 2) == delta
    after = reward_obligations(db, C, "2026-07")
    assert after[A]["gap"] - gaps[A]["gap"] == round(gap_a * 100)
    assert after[B]["gap"] - gaps[B]["gap"] == round(gap_b * 100)
    snapshot = db.model_dump()
    data(execute(session, method, answer["confirmation_id"]))
    assert db.model_dump() == snapshot
    other = (
        "period_reallocation"
        if method == "individual_correction"
        else "individual_correction"
    )
    assert execute(session, other, answer["confirmation_id"]).is_error
    assert db.model_dump() == snapshot


def test_independent_pool_control_removes_displacement():
    session = before_consent()
    db = session.env.tools.db
    pool = db.reward_reprocessing_cases[RID].funding_pools[0]
    pool.allocation_ids = []
    pool.reversal_order = []
    before = db.accounts["acc_1187"].balance
    answer = data(
        session.call("ask_client", packet(session, "period_reallocation", []))
    )
    data(execute(session, "period_reallocation", answer["confirmation_id"]))
    assert round(db.accounts["acc_1187"].balance - before, 2) == 6.2


def test_missing_reversal_consent_is_atomic_and_recoverable():
    session = before_consent()
    db = session.env.tools.db
    bad = data(session.call("ask_client", packet(session, "period_reallocation", [])))
    before = db.model_dump()
    assert execute(session, "period_reallocation", bad["confirmation_id"]).is_error
    assert db.model_dump() == before
    good = data(session.call("ask_client", packet(session, "period_reallocation")))
    data(execute(session, "period_reallocation", good["confirmation_id"]))


@pytest.mark.parametrize("delayed", [False, True])
def test_whole_grade_requires_received_method_consent(delayed):
    session = PlatformSession(task2(), get_environment, max_errors=0)
    pending = None
    for action in session.task.evaluation_criteria.actions:
        args = deepcopy(action.arguments)
        if delayed and action.action_id == "bank2:v29:recovery-consent":
            pending = session.begin_call(action.name, args, None)
            data(session.call(action.name, args, attempt=pending))
        else:
            data(session.call(action.name, args))
            if action.name == "reprocess_rewards" and pending:
                session.result_sent(pending)
    data(session.call("done", {}))
    reward = grade_full_session(session).reward_info
    assert reward.db_check.db_match
    assert reward.reward_breakdown[RewardType.ACTION] == (0 if delayed else 1)


def test_legal_suboptimal_route_gets_db_zero_action_one():
    session = PlatformSession(task2(), get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        args = deepcopy(action.arguments)
        if action.action_id == "bank2:v29:recovery-consent":
            args = packet(session, "period_reallocation")
        elif action.name == "reprocess_rewards":
            args["method_id"] = "period_reallocation"
        elif action.action_id == "bank2:v12:consent-first":
            args["request"]["resulting_claims"][0]["amount"] = 3.5
            args["request"]["resulting_claims"][1]["amount"] = 6.35
        elif action.action_id == "bank2:v17:consent-amendment":
            args["request"]["payouts"][0]["amount"] = 3.5
            args["request"]["payouts"][1]["amount"] = 16.25
            args["request"]["resulting_claims"][0]["amount"] = 5.85
        elif action.action_id == "bank2:v17:amend-resolution":
            args["payouts"][0]["amount"] = 3.5
            args["payouts"][1]["amount"] = 16.25
        elif action.action_id == "bank2:v4:amend":
            args["items"][1]["amount"] = 5.85
        data(session.call(action.name, args))
    data(session.call("done", {}))
    reward = grade_full_session(session).reward_info
    assert not reward.db_check.db_match
    assert reward.reward_breakdown[RewardType.ACTION] == 1
