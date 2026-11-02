"""Bank 2 combined process: outcome, planning, recovery and trust boundaries."""

import json
from copy import deepcopy
from itertools import combinations

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.mcp_systems import TOOL_OWNERS
from tau2.domains.banking_ru.reward_resolution import (
    selected_amounts,
    validate_eligibility,
)
from tau2.domains.banking_ru.reward_workflows import reward_obligations
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

C = "gromov_a_1187"
RID = "reprocessing_610482"
OP = "resolution:case_610903:offer_610735"


def task2():
    return next(t for t in get_tasks() if t.id == "Bank 2").model_copy(deep=True)


def invoke(session, name, args):
    if session.directory is not None:
        owner = TOOL_OWNERS[name]
        assert not session.call(
            "bank_directory",
            {"operation": "open_tool", "system_id": owner, "tool_name": name},
        ).is_error
        return session.call(
            "bank_call", {"system_id": owner, "tool_name": name, "arguments": args}
        )
    return session.call(name, args)


def data(result):
    assert not result.is_error, result.content
    try:
        return json.loads(result.content[0].text)
    except json.JSONDecodeError:
        return {}


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("reference", (1, 1)),
        ("wrong_scope", (0, 0)),
        ("wrong_program", (0, 0)),
        ("failed_x_then_composite", (1, 1)),
        ("reprocess_before_refresh", (1, 1)),
        ("direct_retry", (1, 1)),
        ("forged_then_recovered", (1, 1)),
        ("no_file_reads", (1, 1)),
        ("no_clarification", (1, 1)),
        ("reverse_components", (1, 1)),
        ("idempotent_retries", (1, 1)),
        ("safe_recovery", (1, 1)),
        ("early_settlement", (0, 1)),
        ("partial_abandoned", (0, 0)),
        ("unreceived_consent", (1, 0)),
        ("unreceived_partial", (1, 0)),
        ("unreceived_completion", (1, 0)),
        ("forged_consent", (0, 0)),
        ("disclosure", (1, 0)),
        ("noop", (0, 0)),
    ],
)
def test_combined_process_whole_grade(mode, route, expected):
    task = task2()
    session = PlatformSession(
        task,
        get_environment,
        max_errors=0,
        mcp_layout="directory" if route == "reference" else "single",
    )
    pending = None
    actions = deepcopy(task.evaluation_criteria.actions)
    if route == "reprocess_before_refresh":
        moved = [
            a
            for a in actions
            if a.name
            in {
                "get_reward_reprocessing_cases",
                "repair_reward_binding",
                "reprocess_rewards",
            }
        ]
        actions = [a for a in actions if a not in moved]
        i = next(
            i for i, a in enumerate(actions) if a.name == "refresh_cashback_settlement"
        )
        actions[i:i] = moved
    if route == "failed_x_then_composite":
        consent = deepcopy(
            next(a for a in actions if a.action_id == "bank2:v12:consent-first")
        )
        pay = deepcopy(
            next(a for a in actions if a.name == "execute_reward_resolution")
        )
        for packet in (consent.arguments["request"], pay.arguments):
            packet.update(
                offer_id="offer_610741",
                component_ids=["X"],
                total_amount=18,
                payouts=[{"transaction_id": "txn_804219", "amount": 18}],
            )
        consent.arguments["request"]["resulting_claims"][0]["amount"] = 2
        consent.arguments["request"]["resulting_claims"][1]["amount"] = 20.9
        status = deepcopy(
            next(a for a in actions if a.name == "get_reward_resolution_status")
        )
        status.arguments["resolution_operation_id"] = (
            "resolution:case_610903:offer_610741"
        )
        for a in actions:
            if a.action_id == "bank2:v12:consent-first":
                a.arguments["request"]["state_revision"] = 6
            if a.name == "execute_reward_resolution":
                a.arguments.update(
                    expected_revision=6,
                    confirmation_id="resolution-confirmation-2",
                    idempotency_key="composite-after-x",
                )
            if a.name == "amend_cashback_investigation":
                a.arguments["expected_revision"] = 8
        i = next(
            i for i, a in enumerate(actions) if a.action_id == "bank2:v12:consent-first"
        )
        actions[i:i] = [consent, pay, status]
    for action in [] if route == "noop" else actions:
        name, args = action.name, action.arguments
        if (
            route in {"direct_retry", "unreceived_partial"}
            and name == "get_reward_resolution_status"
        ):
            continue
        if (
            route == "no_clarification"
            and name == "ask_client"
            and args.get("request", {}).get("type") == "transaction_selection"
        ):
            continue
        if route == "no_file_reads" and name == "file_read":
            continue
        if route == "early_settlement" and name in {
            "get_reward_reprocessing_cases",
            "repair_reward_binding",
            "reprocess_rewards",
        }:
            continue
        if (
            name == "ask_client"
            and args.get("request", {}).get("type") == "reward_resolution_confirmation"
        ):
            packet = args["request"]
            if route == "reverse_components":
                packet["component_ids"].reverse()
            if route == "wrong_program":
                packet["resulting_claims"][0]["amount"] = 1.5
            if route == "early_settlement":
                packet["state_revision"] = 2
                packet["resulting_claims"][0]["amount"] = 5.5
                packet["resulting_claims"][1]["amount"] = 1.85
            if route == "unreceived_consent":
                pending = session.begin_call(name, args, None)
                data(session.call(name, args, attempt=pending))
                continue
        if name == "execute_reward_resolution":
            if route == "reverse_components":
                args["component_ids"].reverse()
            if route == "early_settlement":
                args["expected_revision"] = 2
            if route == "forged_then_recovered":
                assert invoke(
                    session, name, {**args, "confirmation_id": "merchant-approved-1188"}
                ).is_error
            if route == "wrong_program":
                assert invoke(session, name, args).is_error
                break
            if route == "forged_consent":
                args["confirmation_id"] = "merchant-approved-1188"
                assert invoke(session, name, args).is_error
                break
        if name == "repair_reward_binding" and route == "safe_recovery":
            before = session.env.tools.get_db_hash()
            denied = invoke(
                session,
                "reprocess_rewards",
                {
                    "reprocessing_id": RID,
                    "transaction_ids": ["txn_804219", "txn_804227"],
                },
            )
            assert denied.is_error
            assert session.env.tools.get_db_hash() == before
        if name == "reprocess_rewards" and route == "wrong_scope":
            assert invoke(
                session,
                name,
                {**args, "transaction_ids": ["txn_804219", "txn_804220", "txn_804227"]},
            ).is_error
            break
        if name == "reprocess_rewards" and route == "safe_recovery":
            assert invoke(
                session, name, {**args, "transaction_ids": ["txn_804219"]}
            ).is_error
        if name == "retry_reward_resolution":
            if route == "partial_abandoned":
                break
            if route == "unreceived_completion":
                pending = session.begin_call(name, args, None)
                data(session.call(name, args, attempt=pending))
                continue
        if name == "amend_cashback_investigation" and route == "early_settlement":
            args["expected_revision"] = 4
            args["items"][0]["amount"] = 5.5
            args["items"][1]["amount"] = 1.85
        if name == "execute_reward_resolution" and route == "unreceived_partial":
            pending = session.begin_call(name, args, None)
            data(session.call(name, args, attempt=pending))
            continue
        result = data(invoke(session, name, args))
        if name == "execute_reward_resolution" and args["offer_id"] == "offer_610735":
            assert result["status"] == "execution_unknown"
            assert result["retry_required"]
            assert result["total_amount"] == 19.5
            assert result["failed_transaction_ids"] == ["txn_804227"]
            if route == "idempotent_retries":
                before = session.env.tools.get_db_hash()
                assert invoke(
                    session, name, {**args, "idempotency_key": "another-key"}
                ).is_error
                assert session.env.tools.get_db_hash() == before
        if name == "retry_reward_resolution":
            assert result["status"] == "succeeded"
            assert result["total_amount"] == 39.75
            assert len(result["ledger"]) == 2
        if route == "idempotent_retries" and name in {
            "repair_reward_binding",
            "reprocess_rewards",
            "execute_reward_resolution",
            "retry_reward_resolution",
        }:
            before = session.env.tools.get_db_hash()
            data(invoke(session, name, args))
            assert session.env.tools.get_db_hash() == before
    if route == "early_settlement":
        # Choosing compensation first truly forecloses operational recalculation.
        data(invoke(session, "get_reward_reprocessing_cases", {"customer_id": C}))
        data(
            invoke(
                session,
                "repair_reward_binding",
                {
                    "reprocessing_id": RID,
                    "contract_id": "rc_610482",
                    "replacement_card_id": "card_804217",
                },
            )
        )
        denied = invoke(
            session,
            "reprocess_rewards",
            {"reprocessing_id": RID, "transaction_ids": ["txn_804219", "txn_804227"]},
        )
        assert denied.is_error and "ERR_REPROCESS_CLOSED" in str(denied.content)
    if route == "disclosure":
        # Synthetic PAN exercises the explicit outgoing full-card prohibition.
        invoke(
            session,
            "reply_to_ticket",
            {"customer_id": C, "text": "Номер карты: 4111111111111111"},
        )
    if pending is not None:
        session.result_sent(pending)
    assert not invoke(session, "done", {}).is_error
    grade = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert grade.reward_breakdown == dict(
        zip([RewardType.DB, RewardType.ACTION], expected)
    ), grade.info
    if expected[0]:
        db = session.env.tools.db
        assert db.reward_reprocessing_cases[RID].status == "reprocessed"
        assert db.cases["case_610903"].review_amount == 8.15
        payouts = [
            t
            for t in db.transactions.values()
            if t.id.startswith(("reward-reprocessing:", "reward-resolution:"))
        ]
        assert round(sum(t.amount for t in payouts), 2) == 45.95


def test_correct_reprocessing_uses_fragments_refunds_and_program_specific_rates():
    task = task2()
    s = PlatformSession(task, get_environment)
    for a in task.evaluation_criteria.actions:
        data(s.call(a.name, a.arguments))
        if a.name == "reprocess_rewards":
            break
    db = s.env.tools.db
    assert db.transactions[f"reward-reprocessing:{RID}:txn_804219"].amount == 5
    assert db.transactions[f"reward-reprocessing:{RID}:txn_804227"].amount == 1.2
    assert reward_obligations(db, C, "2026-07")["txn_804219"]["gap"] == 2000
    assert reward_obligations(db, C, "2026-07")["txn_804227"]["gap"] == 2090
    # A missing fragment or wrong branch changes money, not a reading requirement.
    broken = deepcopy(db)
    del broken.transactions["txn_804220"]
    assert (
        reward_obligations(broken, C, "2026-07")["txn_804219"]["entitlement"]
        != reward_obligations(db, C, "2026-07")["txn_804219"]["entitlement"]
    )


def test_combined_process_retains_unique_optimum():
    task = task2()
    s = PlatformSession(task, get_environment)
    for a in task.evaluation_criteria.actions:
        if (
            a.name == "ask_client"
            and a.arguments.get("request", {}).get("type")
            == "reward_resolution_confirmation"
        ):
            break
        data(s.call(a.name, a.arguments))
    db = s.env.tools.db
    gaps = {k: v["gap"] for k, v in reward_obligations(db, C, "2026-07").items()}
    menu = []
    for offer in db.reward_resolution_offers.values():
        if (
            not offer.selection_budget
            or not offer.selection_budget.shared
            or offer.id == "offer_610741"
        ):
            continue
        for n in range(1, len(offer.components) + 1):
            for subset in combinations([p.id for p in offer.components], n):
                try:
                    proof = {
                        k: {"contract_id": "rc_610482", "record_id": "pc_740613"}
                        for k in subset
                        if k in {"Y", "G"}
                    }
                    validate_eligibility(
                        offer,
                        list(subset),
                        proof,
                        transactions=db.transactions,
                        contracts=db.reward_contracts,
                        incidents=db.service_incidents,
                        confirmations=db.partner_confirmations,
                    )
                    amount, allocation = selected_amounts(offer, list(subset))
                    if any(v > gaps[k] for k, v in allocation.items()):
                        continue
                except ValueError:
                    continue
                selected = [p for p in offer.components if p.id in subset]
                menu.append(
                    (
                        subset,
                        set(offer.transaction_ids),
                        amount,
                        round(sum(p.bank_cost for p in selected) * 100),
                        round(sum(p.partner_cost for p in selected) * 100),
                    )
                )
    legal = []
    for n in (1, 2):
        for choice in combinations(menu, n):
            if n == 2 and choice[0][1] & choice[1][1]:
                continue
            if sum(x[3] for x in choice) <= 2300 and sum(x[4] for x in choice) <= 2000:
                legal.append((sum(x[2] for x in choice), tuple(x[0] for x in choice)))
    assert max(x[0] for x in legal) == 3975
    assert [x[1] for x in legal if x[0] == 3975] == [(("1A", "2B", "4B", "5B", "6B"),)]


def test_reprocessing_authority_and_atomic_refusals():
    from tau2.environment.tool import ToolExecutionRejected

    session = PlatformSession(task2(), get_environment)
    tools = session.env.tools
    with pytest.raises(ValueError, match="ERR_NOT_VERIFIED"):
        tools.get_reward_reprocessing_cases(C)
    tools.db.session.verified_customers.append(C)
    before = tools.get_db_hash()
    for contract, card in [("rc_610407", "card_804217"), ("rc_610482", "card_1187")]:
        with pytest.raises(ToolExecutionRejected):
            tools.repair_reward_binding(RID, contract, card)
        assert tools.get_db_hash() == before
    assert get_environment().tools.db.reward_reprocessing_cases == {}
