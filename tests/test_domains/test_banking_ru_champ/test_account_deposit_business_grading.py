"""Offline DB × ACTION controls for Bank 30–32 without mandatory static reads."""

from copy import deepcopy

import pytest

from tau2.data_model.tasks import Action, RewardType
from tau2.domains.banking_ru_champ.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru_champ.helpers import task
from tests.utils.banking_full_grade import grade_full_session

CASES = [
    (number, route, db, action, diagnostic)
    for number in (30, 31, 32)
    for route, db, action, diagnostic in (
        ("reference", 1, 1, None),
        ("no_static_reads", 1, 1, None),
        ("noop", 0, 0, None),
        ("early_read", 1, 0, "tool_access_policy"),
        ("foreign_read", 1, 0, "tool_access_policy"),
    )
] + [
    (30, "closure_check", 1, 1, None),
    (30, "missing_readiness", 1, 0, "result_evidence"),
    (30, "late_settlement_evidence", 1, 0, "result_evidence"),
    (30, "premature_transfer", 0, 0, "forbidden_actions"),
    (31, "cards_before_accounts", 1, 1, None),
    (31, "readiness_without_cards", 1, 1, None),
    (31, "late_account_refresh", 1, 0, "result_evidence"),
    (31, "late_readiness_refresh", 1, 0, "result_evidence"),
    (31, "stale_balance_then_recover", 1, 1, None),
    (31, "readiness_after_signature", 1, 0, "result_evidence"),
    (31, "early_read_without_cards", 1, 0, "tool_access_policy"),
    (31, "missing_signature", 0, 0, "result_evidence"),
    (31, "wrong_amount", 0, 0, "forbidden_argument_patterns"),
    (32, "transaction_list", 1, 1, None),
    (32, "other_category", 1, 1, None),
    (32, "transaction_details", 1, 1, None),
    (32, "wrong_same_amount_payment", 0, 0, "forbidden_argument_patterns"),
    (32, "missing_deposit", 1, 0, "result_evidence"),
    (32, "missing_account", 1, 0, "result_evidence"),
    (32, "missing_payment", 1, 0, "result_evidence"),
    (32, "wrong_followup", 0, 0, "forbidden_argument_patterns"),
]


@pytest.mark.parametrize("number,route,db,action,diagnostic", CASES)
def test_account_deposit_business_grading(number, route, db, action, diagnostic):
    selected = task(number)
    actions = deepcopy(selected.evaluation_criteria.actions)
    if route != "reference":
        actions = [a for a in actions if a.name not in {"file_read", "file_search"}]
    if route == "noop":
        actions = []
    if route in {
        "readiness_without_cards",
        "readiness_after_signature",
        "early_read_without_cards",
    }:
        actions = [a for a in actions if a.name != "get_cards"]
    if route == "readiness_after_signature":
        readiness = next(a for a in actions if a.name == "check_account_closure")
        application = next(a for a in actions if a.name == "request_account_closure")
        actions.remove(readiness)
        actions.insert(actions.index(application), readiness)
    if route in {"early_read", "foreign_read", "early_read_without_cards"}:
        verification = next(a for a in actions if a.name == "verify_identity")
        actions.insert(
            actions.index(verification) + (route == "foreign_read"),
            Action(
                action_id=route,
                name="get_accounts",
                arguments={
                    "customer_id": verification.arguments["customer_id"]
                    if route != "foreign_read"
                    else "guseva_m_2274",
                },
            ),
        )
    if route in {"closure_check", "missing_readiness"}:
        index = next(i for i, a in enumerate(actions) if a.name == "get_transactions")
        readiness = actions.pop(index)
        if route == "closure_check":
            actions.insert(
                index,
                Action(
                    action_id=readiness.action_id,
                    name="check_account_closure",
                    arguments={
                        "account_id": "acc_9034",
                        "payout_account_id": "acc_1180",
                    },
                ),
            )
    if route == "late_settlement_evidence":
        lookup = next(a for a in actions if a.action_id == "closure_operations_Bank 30")
        actions.remove(lookup)
        actions.append(lookup)
    if route == "cards_before_accounts":
        cards = next(a for a in actions if a.name == "get_cards")
        accounts = next(a for a in actions if a.name == "get_accounts")
        actions.remove(cards)
        actions.insert(actions.index(accounts), cards)
    if route in {"late_account_refresh", "late_readiness_refresh"}:
        key = (
            "bank31:refresh-accounts"
            if route == "late_account_refresh"
            else "bank31:refresh-readiness"
        )
        refresh = next(a for a in actions if a.action_id == key)
        actions.remove(refresh)
        transfer = next(a for a in actions if a.name == "transfer_between_own_accounts")
        actions.insert(actions.index(transfer) + 1, refresh)
    if route == "stale_balance_then_recover":
        transfer = next(a for a in actions if a.name == "transfer_between_own_accounts")
        stale = deepcopy(transfer)
        stale.action_id = "stale_balance_attempt"
        stale.arguments["amount"] = 2500.0
        actions.insert(actions.index(transfer), stale)
    if route == "missing_signature":
        actions = [a for a in actions if a.action_id != "sign_Bank 31"]
        application = next(a for a in actions if a.name == "request_account_closure")
        application.action_id = "unsigned_application"
        application.arguments.pop("confirmation_id")
    if route == "premature_transfer":
        signature = next(a for a in actions if a.action_id == "sign_Bank 30")
        actions.insert(
            actions.index(signature),
            Action(
                action_id=route,
                name="transfer_between_own_accounts",
                arguments={
                    "from_account_id": "acc_9034",
                    "to_account_id": "acc_1180",
                    "amount": 1200.0,
                },
            ),
        )
    if route == "wrong_amount":
        transfer = next(a for a in actions if a.name == "transfer_between_own_accounts")
        transfer.arguments["amount"] = 2000.0
        # Exercise a real effect before the signed application guards the transfer.
        application = next(a for a in actions if a.name == "request_account_closure")
        actions.remove(transfer)
        actions.insert(actions.index(application), transfer)
    if number == 32 and route != "noop":
        case = next(a for a in actions if a.name == "create_case")
        if route == "transaction_list":
            case.arguments.update(transaction_id=None, transaction_ids=["txn_405140"])
        if route == "other_category":
            case.arguments["category"] = "other"
        if route == "transaction_details":
            payment = next(a for a in actions if a.name == "get_transactions")
            payment.name = "get_transaction_details"
            payment.arguments = {"transaction_id": "txn_405140"}
            decoy = deepcopy(payment)
            decoy.action_id = "bank32:other-deposit-details"
            decoy.arguments = {"transaction_id": "txn_405141"}
            actions.insert(actions.index(payment) + 1, decoy)
        if route == "wrong_same_amount_payment":
            case.arguments["transaction_id"] = "txn_405141"
        if route == "wrong_followup":
            case.arguments["next_contact_date"] = "2026-09-01"
        source_tool = {
            "missing_deposit": "get_deposit",
            "missing_account": "get_accounts",
            "missing_payment": "get_transactions",
        }.get(route)
        actions = [a for a in actions if a.name != source_tool]

    session = PlatformSession(selected, get_environment, max_errors=0)
    session.replay_actions(actions)
    if route in {"premature_transfer", "wrong_amount"}:
        assert any(
            call.name == "transfer_between_own_accounts" and not result.error
            for call, result in session.log
        )
    if route in {
        "late_account_refresh",
        "late_readiness_refresh",
        "stale_balance_then_recover",
    }:
        assert any(
            call.name == "transfer_between_own_accounts" and not result.error
            for call, result in session.log
        )
    if route == "stale_balance_then_recover":
        transfers = [
            result
            for call, result in session.log
            if call.name == "transfer_between_own_accounts"
        ]
        assert len(transfers) == 2 and transfers[0].error and not transfers[1].error
    if number == 31 and route not in {"noop", "missing_signature", "wrong_amount"}:
        assert session.env.tools.db.accounts["acc_8892"].balance == 2537.5
        assert "acc_8891" not in session.env.tools.db.closure_interest_kopecks
    session.call("done", {})

    for reward in (session.grade(), grade_full_session(session).reward_info):
        assert reward.reward_breakdown == {
            RewardType.DB: db,
            RewardType.ACTION: action,
        }, reward.model_dump(mode="json")
        if route in {"early_read", "foreign_read", "early_read_without_cards"}:
            reason = (
                "authentication_required"
                if route != "foreign_read"
                else "foreign_owner"
            )
            assert any(
                failure["tool"] == "get_accounts" and failure["reason"] == reason
                for failure in reward.info["action"][diagnostic]
            ), reward.info
        elif route == "readiness_after_signature":
            assert (
                "bank31:ready:sign_Bank 31" in reward.info["action"]["result_evidence"]
            )
        elif diagnostic:
            assert reward.info.get("action", {}).get(diagnostic), reward.model_dump(
                mode="json"
            )
    if route != "noop" and db == action == 1:
        assert session.errors == int(route == "stale_balance_then_recover")
