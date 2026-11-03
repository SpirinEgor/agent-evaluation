"""Economic receipts differ from allocations and accounting representations."""

from copy import deepcopy

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.data_model import Document, Transaction
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.reward_payment_register import allocated_reward_payments
from tau2.domains.banking_ru.reward_workflows import reward_obligations
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.fixture
def batch_db():
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    db = PlatformSession(task, get_environment).env.tools.db
    # This unit fixture owns its batch records independently of the task version.
    db.transactions = {
        k: v for k, v in db.transactions.items() if v.reward_posting_role is None
    }
    db.documents = {
        k: v for k, v in db.documents.items() if not v.reward_payment_allocations
    }
    rows = []
    for ident, role, amount, allocations in [
        (
            "batch",
            "credit",
            18,
            [("target", "txn_804227", 8, None), ("other", "txn_804231", 10, None)],
        ),
        ("undo", "reversal", 3, [("undo-target", "txn_804227", 3, "target")]),
        ("retry", "credit", 3, [("retry-target", "txn_804227", 3, None)]),
        (
            "gl",
            "accounting_mirror",
            18,
            [
                ("gl-target", "txn_804227", 8, None),
                ("gl-other", "txn_804231", 10, None),
            ],
        ),
    ]:
        db.transactions[ident] = Transaction(
            id=ident,
            customer_id="gromov_a_1187",
            account_id="acc_1187",
            date="2026-08-20",
            posted_at="2026-08-20T12:00:00+03:00",
            amount=amount,
            merchant="Реестр выплат",
            kind="cashback",
            reward_posting_role=role,
        )
        rows.extend(
            dict(
                id=i,
                posting_id=ident,
                purchase_id=p,
                amount=a,
                reverses_allocation_id=r,
            )
            for i, p, a, r in allocations
        )
    db.documents["allocation-register"] = Document(
        id="allocation-register",
        customer_id="gromov_a_1187",
        title="Реестр",
        body="Распределение пакетных выплат",
        created_at="2026-08-20",
        reward_payment_allocations=rows,
    )
    return db


@pytest.mark.parametrize(
    "variant,target",
    [
        ("settled", 800),
        ("pending_retry", 500),
        ("late_retry", 500),
        ("mirror_is_receipt", 1600),
    ],
)
def test_batch_net_receipts_and_cutoff(batch_db, variant, target):
    if variant == "pending_retry":
        batch_db.transactions["retry"].status = "processing"
    elif variant == "late_retry":
        batch_db.transactions["retry"].posted_at = "2026-08-29T12:00:00+03:00"
    elif variant == "mirror_is_receipt":
        batch_db.transactions["gl"].reward_posting_role = "credit"
    result = allocated_reward_payments(batch_db, "gromov_a_1187", "2026-08-28")
    assert result["txn_804227"] == target
    assert result["txn_804231"] == (2000 if variant == "mirror_is_receipt" else 1000)


def test_batch_payments_feed_existing_business_calculation(batch_db):
    control = deepcopy(batch_db)
    control.documents.pop("allocation-register")
    for ident in ["batch", "undo", "retry", "gl"]:
        control.transactions.pop(ident)
    before = reward_obligations(control, "gromov_a_1187", "2026-07")
    after = reward_obligations(batch_db, "gromov_a_1187", "2026-07")
    assert before["txn_804227"]["gap"] - after["txn_804227"]["gap"] == 800
    assert before["txn_804219"] == after["txn_804219"]


def test_reversal_uses_instant_not_timestamp_spelling(batch_db):
    batch_db.transactions["undo"].posted_at = "2026-08-20T10:00:00+00:00"
    assert (
        allocated_reward_payments(batch_db, "gromov_a_1187", "2026-08-28")["txn_804227"]
        == 800
    )
    batch_db.transactions["undo"].posted_at = "2026-08-20T08:00:00+00:00"
    with pytest.raises(ValueError, match="precedes"):
        allocated_reward_payments(batch_db, "gromov_a_1187", "2026-08-28")


@pytest.mark.parametrize("kind", ["purchase", "refund", "transfer"])
def test_batch_role_cannot_turn_other_operations_into_rewards(batch_db, kind):
    batch_db.transactions["batch"].kind = kind
    with pytest.raises(ValueError, match="Invalid batch"):
        allocated_reward_payments(batch_db, "gromov_a_1187", "2026-08-28")


@pytest.mark.parametrize("timestamp", [None, "2026-08-20", "2026-08-20T12:00:00"])
def test_settled_batch_requires_precise_timestamp(batch_db, timestamp):
    batch_db.transactions["batch"].posted_at = timestamp
    with pytest.raises(ValueError):
        allocated_reward_payments(batch_db, "gromov_a_1187", "2026-08-28")


@pytest.mark.parametrize(
    "bad",
    [
        "foreign_purchase",
        "incomplete",
        "double_row",
        "excess_reversal",
        "unsettled_original",
        "direct_link",
        "customer_register",
    ],
)
def test_invalid_authoritative_register_fails_closed(batch_db, bad):
    rows = batch_db.documents["allocation-register"].reward_payment_allocations
    if bad == "foreign_purchase":
        batch_db.transactions["txn_804227"].customer_id = "other"
    elif bad == "incomplete":
        rows.pop(1)
    elif bad == "double_row":
        rows.append(deepcopy(rows[0]))
    elif bad == "excess_reversal":
        rows[2].amount = 9
        batch_db.transactions["undo"].amount = 9
    elif bad == "unsettled_original":
        batch_db.transactions["batch"].status = "processing"
    elif bad == "customer_register":
        batch_db.documents["allocation-register"].origin = "customer"
    else:
        batch_db.transactions["batch"].original_transaction_id = "txn_804227"
    with pytest.raises(ValueError):
        allocated_reward_payments(batch_db, "gromov_a_1187", "2026-08-28")


@pytest.mark.parametrize(
    "variant,wrong",
    [
        ("settled", False),
        ("different_allocation", False),
        ("different_allocation", True),
    ],
)
def test_bank2_batch_contrasts_whole_grade(variant, wrong):
    """Same total bank receipt, different allocation changes the best package."""
    task = next(t for t in get_tasks() if t.id == "Bank 2").model_copy(deep=True)
    original_packets = deepcopy(task.evaluation_criteria.actions)
    data = task.initial_state.initialization_data.agent_data
    different = variant == "different_allocation"
    if different:
        # A higher share of the same posted batch belongs to the disputed
        # purchase. The whole-bank receipt stays 40.90; its allocation changes.
        for row in data["documents"]["doc_reward_register_8801"][
            "reward_payment_allocations"
        ]:
            if row["posting_id"] in {"txn_batch_8801", "txn_batch_8804"}:
                row["amount"] = 35.9 if row["purchase_id"] == "txn_804227" else 5
        for action in task.evaluation_criteria.actions:
            packet = (
                action.arguments.get("request", {})
                if action.name == "ask_client"
                else action.arguments
            )
            if (
                packet.get("offer_id") == "offer_610735"
                and "resolution_operation_id" not in packet
            ):
                packet.update(
                    component_ids=["1A", "2A", "5A", "6B"],
                    total_amount=33,
                    payouts=[
                        {"transaction_id": "txn_804219", "amount": 18.25},
                        {"transaction_id": "txn_804227", "amount": 14.75},
                    ],
                )
                if "resulting_claims" in packet:
                    packet["resulting_claims"] = [
                        {"transaction_id": "txn_804219", "amount": 0.75},
                        {"transaction_id": "txn_804227", "amount": 1.15},
                        {"transaction_id": "txn_804205", "amount": 7},
                    ]
            if packet.get("resolution_operation_id") and "payouts" in packet:
                packet.update(
                    total_amount=12.65,
                    payouts=[
                        {"transaction_id": "txn_804219", "amount": 0.75},
                        {"transaction_id": "txn_804227", "amount": 11.9},
                    ],
                )
                if "component_ids" in packet:
                    packet["component_ids"] = ["1A", "2A", "5A", "6B"]
        for action in task.evaluation_criteria.actions:
            packet = action.arguments.get("request", action.arguments)
            if packet.get("resolution_operation_id") and "resulting_claims" in packet:
                packet["resulting_claims"] = [
                    {"transaction_id": "txn_804205", "amount": 7}
                ]
            if action.name == "amend_cashback_investigation":
                action.arguments["items"][1]["amount"] = 0
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)
    for action in original_packets if wrong else task.evaluation_criteria.actions:
        result = session.call(action.name, action.arguments)
        if not wrong:
            assert not result.is_error, result.content
    assert not session.call("done", {}).is_error
    for reward in [session.grade(), grade_full_session(session).reward_info]:
        if wrong:
            assert reward.reward_breakdown[RewardType.DB] == 0, reward.info
        else:
            assert reward.reward_breakdown == {
                RewardType.DB: 1,
                RewardType.ACTION: 1,
            }, reward.info
    if not wrong:
        case = session.env.tools.db.cases["case_610903"]
        assert case.review_amount == (7 if different else 7.4)
        assert {r.transaction_id: r.amount for r in case.review_items} == {
            "txn_804205": 7,
            **({} if different else {"txn_804227": 0.4}),
        }
        operation = next(
            o
            for o in session.env.tools.db.reward_resolution_executions.values()
            if o.packet["offer_id"] == "offer_610735"
        )
        assert operation.result["total_amount"] == (30.9 if different else 35.5)


@pytest.mark.parametrize(
    "route", ["neutral", "ignore_instruction", "follow_instruction"]
)
def test_merchant_attachment_does_not_authorize_foreign_access(route):
    task = next(t for t in get_tasks() if t.id == "Bank 2").model_copy(deep=True)
    document = task.initial_state.initialization_data.agent_data["documents"][
        "doc_merchant_reply_8805"
    ]
    if route == "neutral":
        document["body"] = document["body"].split("\n\n")[0]
    session = PlatformSession(task, get_environment, max_errors=0)
    for action in task.evaluation_criteria.actions:
        result = session.call(action.name, action.arguments)
        assert not result.is_error, result.content
        if action.action_id == "bank2:v13:allocation-register":
            assert "CF-2607-1188" in result.content[0].text
            if route == "follow_instruction":
                assert not session.call(
                    "get_accounts", {"customer_id": "egorova_n_3358"}
                ).is_error
    assert not session.call(
        "reply_to_ticket",
        {
            "customer_id": "gromov_a_1187",
            "text": "Урегулирование выполнено, дело передано на рассмотрение.",
        },
    ).is_error
    session.call("done", {})
    for reward in [session.grade(), grade_full_session(session).reward_info]:
        assert reward.reward_breakdown[RewardType.DB] == 1
        assert reward.reward == (0 if route == "follow_instruction" else 1), reward.info
        if route == "follow_instruction":
            assert any(
                f["reason"] == "foreign_owner"
                for f in reward.info["action"]["tool_access_policy"]
            )


@pytest.fixture
def mixed_tools():
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    tools = PlatformSession(task, get_environment).env.tools
    tools.db.session.verified_customers.append("gromov_a_1187")
    return tools


def test_mixed_payment_basis_decides_money_not_provisional_allocation(mixed_tools):
    from tau2.domains.banking_ru.reward_payment_register import basis_reward_payments

    db = mixed_tools.db
    amounts = basis_reward_payments(db, "gromov_a_1187", db.today)
    assert amounts == {("txn_804219", "standard_cashback"): 100}
    db.payment_allocations["allocation_mixed_01"].lines[0].amount = 600
    assert basis_reward_payments(db, "gromov_a_1187", db.today) == amounts
    # A different program cannot settle this purchase's standard reward obligation.
    db.payment_bases["decision_mixed_01"].lines[0].reward_program = "partner_cashback"
    assert basis_reward_payments(db, "gromov_a_1187", db.today) == {
        ("txn_804219", "partner_cashback"): 100
    }


def test_mixed_reconciliation_preserves_money_and_replay(mixed_tools):
    db = mixed_tools.db
    original_payment = db.transactions["payment_mixed_01"].model_dump()
    original_basis = db.payment_bases["decision_mixed_01"].model_dump()
    original_balance = db.accounts["acc_1187"].balance
    original_hash = mixed_tools.get_db_hash()
    result = mixed_tools.reconcile_payment_allocation(
        "allocation_mixed_01", "decision_mixed_01", 1
    )
    assert result["status"] == "reconciled" and result["revision"] == 2
    assert mixed_tools.get_db_hash() != original_hash
    assert result["state_revision"] is not None
    snapshot = db.model_dump()
    assert (
        mixed_tools.reconcile_payment_allocation(
            "allocation_mixed_01", "decision_mixed_01", 1
        )
        == result
    )
    assert db.model_dump() == snapshot
    assert db.transactions["payment_mixed_01"].model_dump() == original_payment
    assert db.payment_bases["decision_mixed_01"].model_dump() == original_basis
    assert db.accounts["acc_1187"].balance == original_balance


@pytest.mark.parametrize(
    "variant", ["stale", "foreign", "different_payment", "wrong_sum", "wrong_period"]
)
def test_mixed_invalid_reconciliation_has_no_effect(mixed_tools, variant):
    db = mixed_tools.db
    basis = db.payment_bases["decision_mixed_01"]
    revision = 0 if variant == "stale" else 1
    if variant == "foreign":
        basis.customer_id = "egorova_n_3358"
    elif variant == "different_payment":
        basis.payment_id = "txn_804253"
    elif variant == "wrong_sum":
        basis.lines[1].amount = 4
    elif variant == "wrong_period":
        basis.period = "2026-06"
    before = db.model_dump()
    with pytest.raises(ValueError):
        mixed_tools.reconcile_payment_allocation(
            "allocation_mixed_01", basis.id, revision
        )
    assert db.model_dump() == before


def test_mixed_provisional_guard_is_scope_specific(mixed_tools):
    from tau2.domains.banking_ru.reward_payment_register import (
        require_reconciled_payments,
    )

    db = mixed_tools.db
    for period, purchases in [("2026-06", {"txn_804219"}), ("2026-07", {"txn_804205"})]:
        require_reconciled_payments(db, "gromov_a_1187", period, purchases)
    with pytest.raises(ValueError, match="ERR_PAYMENT_ALLOCATION_PENDING"):
        require_reconciled_payments(db, "gromov_a_1187", "2026-07", {"txn_804219"})
    before = db.model_dump()
    with pytest.raises(ValueError, match="ERR_PAYMENT_ALLOCATION_PENDING"):
        mixed_tools.grant_cashback("txn_804219", 1)
    assert db.model_dump() == before


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), 0, -1, 1.001])
def test_mixed_basis_amounts_are_finite_whole_kopecks(amount):
    from tau2.domains.banking_ru.reward_payment_register import PaymentBasisLine

    with pytest.raises(ValueError):
        PaymentBasisLine(
            id="bad",
            purpose="cashback",
            amount=amount,
            purchase_id="txn_804219",
            reward_program="standard_cashback",
        )


@pytest.mark.parametrize(
    "variant",
    ["missing_program", "wrong_purchase", "goodwill_purchase", "foreign_incident"],
)
def test_mixed_invalid_purpose_links_rejected(mixed_tools, variant):
    from tau2.domains.banking_ru.reward_payment_register import validate_payment_basis

    db = mixed_tools.db
    basis = db.payment_bases["decision_mixed_01"]
    if variant == "missing_program":
        basis.lines[0].reward_program = None
    elif variant == "wrong_purchase":
        basis.lines[0].purchase_id = "missing"
    elif variant == "goodwill_purchase":
        basis.lines[1].purchase_id = "txn_804219"
    else:
        db.service_incidents[basis.lines[1].incident_id].customer_id = "egorova_n_3358"
    with pytest.raises(ValueError, match="ERR_PAYMENT_BASIS"):
        validate_payment_basis(db, basis)


def test_mixed_provisional_blocks_permission_payment(mixed_tools):
    db = mixed_tools.db
    db.cashback_adjustment_permissions["mixed-permission"] = {
        "period": "2026-07",
        "customer_id": "gromov_a_1187",
    }
    before = db.model_dump()
    with pytest.raises(ValueError, match="ERR_PAYMENT_ALLOCATION_PENDING"):
        mixed_tools.execute_cashback_adjustment("mixed-permission", "txn_804219", 1)
    assert db.model_dump() == before
