"""State counterexamples for the scoped DB/ACTION migration.

Inject a final-state defect into the replayed predicted environment before its
ordinary hash calculation. Reference replay and all evaluator code stay intact.
The extra read distinguishes prediction from the canonical reference replay.
These synthetic defects prove hash coverage; they do not simulate model routes.
"""

from copy import deepcopy

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.data_model import CashbackAdjustmentReservation
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.tools import BankingTools
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def _state_counterexample(monkeypatch, task_id, mutate):
    task = next(t for t in get_tasks() if t.id == task_id)
    session = PlatformSession(task, get_environment)
    session.replay_actions(deepcopy(task.evaluation_criteria.actions))
    session.call(
        "get_fee_refund_state",
        {"transaction_id": "txn_077040" if task_id == "Bank 41" else "txn_804219"},
    )
    session.call("done", {})
    ordinary_hash = BankingTools.get_db_hash
    touched = []

    def state_hash(tools):
        if tools.db.tool_calls_log[-1:] == ["get_fee_refund_state"]:
            mutate(tools.db)
            touched.append(True)
        return ordinary_hash(tools)

    monkeypatch.setattr(BankingTools, "get_db_hash", state_hash)
    reward = grade_full_session(session).reward_info
    assert touched, "The actual predicted replay must receive the state defect"
    return reward


@pytest.mark.parametrize(
    "defect", ["case_items", "delivery_missing", "delivery_version"]
)
def test_bank41_state_assertions_are_covered_by_real_db_grade(monkeypatch, defect):
    def mutate(db):
        if defect == "case_items":
            case = db.cases["case-n40-current"]
            case.reward_items[0].amount += 1
            case.amount += 1
        elif defect == "delivery_missing":
            db.document_deliveries.clear()
        else:
            delivery = next(iter(db.document_deliveries.values()))
            delivery.version = "outdated-counterexample"

    reward = _state_counterexample(monkeypatch, "Bank 41", mutate)
    assert reward.reward_breakdown[RewardType.DB] == 0
    assert reward.final_reward == 0


def test_bank2_open_reservation_is_covered_by_explicit_db_check(monkeypatch):
    def mutate(db):
        db.cases["case_610903"].adjustment_reservations = [
            CashbackAdjustmentReservation(
                permission_id="cap_620184", transaction_id="txn_804219", amount=1.0
            )
        ]

    reward = _state_counterexample(monkeypatch, "Bank 2", mutate)
    assert reward.reward_breakdown[RewardType.DB] == 0
    assert reward.final_reward == 0
