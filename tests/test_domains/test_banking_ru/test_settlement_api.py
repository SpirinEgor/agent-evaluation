"""Shared reward API controls on an isolated settlement fixture."""

from copy import deepcopy

from tau2.data_model.message import ToolCall
from tau2.domains.banking_ru.reward_workflows import standard_gaps
from tests.utils.banking_settlement_fixture import ready

C = "gromov_a_1187"
A = "txn_552290"


def env_ready():
    return ready(late=False, unknown=False)


def call(env, name, **args):
    if name in {"execute_cashback_adjustment", "amend_cashback_investigation"}:
        args.setdefault(
            "expected_revision", env.tools.db.cashback_state_revisions[C + ":2026-07"]
        )
    return env.get_response(ToolCall(id=name, name=name, arguments=args))


def test_remaining_permission_can_be_used_in_two_distinct_operations():
    env = env_ready()
    p = env.tools.db.cashback_adjustment_permissions["adjustment-A"]
    p.update(max_amount=12, remaining_amount=12, used_amount=0, used=False)
    for key in ["part-one", "part-two"]:
        result = call(
            env,
            "execute_cashback_adjustment",
            permission_id="adjustment-A",
            transaction_id=A,
            amount=6,
            idempotency_key=key,
        )
        assert not result.error, result
    assert p["used_amount"] == 12 and p["remaining_amount"] == 0 and p["used"]
    credits = [
        t
        for t in env.tools.db.transactions.values()
        if t.id.startswith("cashback-adjustment:adjustment-A")
    ]
    assert len(credits) == 2 and sum(t.amount for t in credits) == 12


def test_rule_scoped_cycle_uses_posting_date_and_complete_real_registry():
    env = env_ready()
    db = env.tools.db
    for t in db.transactions.values():
        if t.customer_id == C:
            # The new scheme intentionally distinguishes explicit program membership.
            t.__dict__["reward_program"] = None
    tx = db.transactions[A]
    tx.__dict__["reward_program"] = "standard_cashback"
    tx.date = "2026-06-30"
    tx.posted_at = "2026-07-01T10:00:00+03:00"
    db.cashback_rules[C].cycles = [
        {
            "id": "july",
            "period": "2026-07",
            "program": "standard_cashback",
            "limit": 115,
            "rules_revision": "posting-v2",
            "card_ids": [tx.card_id],
            "posting_from": "2026-07-01",
            "posting_to": "2026-07-31",
            "settlement_as_of": db.today,
        }
    ]
    h = deepcopy(db.cashback_rules[C].categories_history[0])
    h.update(effective_from="2026-06-01", effective_to="2026-06-30")
    h["categories"][0]["rate"] = 0.05
    db.cashback_rules[C].categories_history = [h]
    assert standard_gaps(db, C, "2026-07")[A] == 3000
    result = env.tools.get_cashback_rules(C)
    assert result.cycles[0]["transaction_ids"] == [A]
    assert result.cycles[0]["registry_complete"] is True


def test_transaction_selection_answers_both_amounts_but_not_wrong_question():
    from tau2.domains.banking_ru.client_requests import TransactionSelectionRequest

    env = env_ready()
    env.tools.db.client_answers[C] = [
        a
        for a in env.tools.db.client_answers[C]
        if a.transaction_selection and a.transaction_selection["amount"] != 1400
    ]
    from tau2.domains.banking_ru.data_model import ClientAnswer

    env.tools.db.client_answers[C].append(
        ClientAnswer(
            keywords=[],
            answer="Чек второй покупки",
            transaction_selection={
                "id": "customer-purchase-selection",
                "merchant": "Кофемания",
                "amount": 1400,
                "date": "2026-07-26",
                "receipt": "CF-B",
            },
        )
    )
    import json

    for amount, receipt in [(1200, "CF-2507-1187"), (1400, "CF-B")]:
        response = call(
            env,
            "ask_client",
            customer_id=C,
            request={
                "type": "transaction_selection",
                "merchant": "Кофемания",
                "amount": amount,
            },
        )
        assert json.loads(response.content)["data"]["receipt"] == receipt
    # Optional date is a real constraint, not a way to reveal an unrelated purchase.
    req = TransactionSelectionRequest(
        type="transaction_selection",
        merchant="Кофемания",
        amount=1400,
        date="2026-07-25",
    )
    result = call(
        env, "ask_client", customer_id=C, request=req.model_dump(exclude_none=True)
    )
    assert "unknown" in result.content


def test_standard_case_can_increase_existing_item_and_reserve_future_payout():
    from tau2.domains.banking_ru.data_model import Case

    env = env_ready()
    env.tools.db.cases = {
        k: v for k, v in env.tools.db.cases.items() if v.customer_id != C
    }
    env.tools.db.cashback_rules[C].cycles = [
        {
            "period": "2026-07",
            "program": "standard_cashback",
            "limit": 90,
            "transaction_ids": [A, "txn_552291"],
        }
    ]
    env.tools.db.cases["partial-standard"] = Case(
        id="partial-standard",
        customer_id=C,
        category="cashback_investigation",
        reward_program="standard_cashback",
        reward_period="2026-07",
        amount=10,
        transaction_ids=[A],
        reward_items=[{"transaction_id": A, "amount": 10}],
        status="open",
        created_at=env.tools.db.today,
    )
    result = call(
        env,
        "amend_cashback_investigation",
        case_id="partial-standard",
        items=[
            {"transaction_id": A, "amount": 18},
            {"transaction_id": "txn_552291", "amount": 25},
        ],
        reserved_adjustments=[
            {"permission_id": "adjustment-A", "transaction_id": A, "amount": 12}
        ],
    )
    assert not result.error, result
    assert env.tools.db.cases["partial-standard"].amount == 43
