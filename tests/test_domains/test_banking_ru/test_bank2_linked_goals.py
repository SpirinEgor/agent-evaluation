"""Bank 2: payment and complete handoff, including a legal split amendment."""

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.mcp_systems import TOOL_OWNERS
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("split", 1),
        ("early", 0),
        ("no_submit", 0),
        ("unreceived_version", 0),
        ("conflict_recovered", 1),
        ("repeat", 1),
        ("suboptimal", 0),
        ("previous_offer", 0),
        ("recovered_budget", 1),
        ("final_first", 1),
        ("old_optimum", 0),
        ("ineligible_recovered", 1),
        ("wrong_partner_recovered", 1),
        ("proof_changed_recovered", 1),
        ("payment_repeat", 1),
        ("late_eligibility", 0),
        ("stale_consent_recovered", 1),
        ("unreceived_acceptance", 0),
    ],
)
def test_bank2_payment_and_review_whole_grade(mode, route, want):
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    actions = deepcopy(task.evaluation_criteria.actions)
    if route in {"suboptimal", "previous_offer", "old_optimum"}:
        total, first, components, offer = (
            (34, 14, ["B", "D", "F"], "offer_610735")
            if route == "suboptimal"
            else (
                (42, 18, ["B", "D", "E", "G"], "offer_610735")
                if route == "old_optimum"
                else (24, 11, [], "offer_610723")
            )
        )
        payouts = [
            {"transaction_id": "txn_804219", "amount": first},
            {"transaction_id": "txn_804227", "amount": total - first},
        ]
        items = [
            {"transaction_id": "txn_804219", "amount": 25 - first},
            {"transaction_id": "txn_804227", "amount": 53 - (total - first)},
        ]
        for action in actions:
            if action.action_id == "bank2:v5:consent":
                action.arguments["request"].update(
                    offer_id=offer,
                    offer_version=1 if route == "previous_offer" else 2,
                    component_ids=components,
                    payouts=payouts,
                    total_amount=total,
                    eligibility_evidence={}
                    if route != "old_optimum"
                    else {
                        "E": {"contract_id": "rc_610482", "record_id": "inc_620411"},
                        "G": {"contract_id": "rc_610482", "record_id": "pc_740613"},
                    },
                    resulting_claims=items
                    + [{"transaction_id": "txn_804205", "amount": 7}],
                )
            elif action.name == "execute_reward_resolution":
                action.arguments.update(
                    offer_id=offer,
                    offer_version=1 if route == "previous_offer" else 2,
                    component_ids=components,
                    payouts=payouts,
                    total_amount=total,
                    eligibility_evidence={}
                    if route != "old_optimum"
                    else {
                        "E": {"contract_id": "rc_610482", "record_id": "inc_620411"},
                        "G": {"contract_id": "rc_610482", "record_id": "pc_740613"},
                    },
                )
            elif action.name == "amend_cashback_investigation":
                action.arguments["items"] = items
    if route == "late_eligibility":
        source = actions.pop(
            next(
                i
                for i, a in enumerate(actions)
                if a.name == "get_partner_confirmations"
            )
        )
        pi = next(
            i for i, a in enumerate(actions) if a.name == "execute_reward_resolution"
        )
        actions.insert(pi, source)
    if route == "final_first":
        actions = [a for a in actions if a.action_id != "bank2:v8:preliminary-offers"]
    if route == "split":
        for action in actions:
            if action.name == "file_read":
                action.name, action.arguments = (
                    "corpus_command",
                    {"command": "cat " + action.arguments["path"]},
                )
            elif action.name == "execute_reward_resolution":
                action.arguments["component_ids"].reverse()
    assert actions[-1].name == "submit_cashback_review"
    submit = actions.pop()
    s = PlatformSession(
        task,
        get_environment,
        mcp_layout="directory" if route in {"reference", "split"} else "single",
    )

    def call(name, args):
        if s.directory is not None:
            owner = TOOL_OWNERS[name]
            assert not s.call(
                "bank_directory",
                {"operation": "open_tool", "system_id": owner, "tool_name": name},
            ).is_error
            return s.call(
                "bank_call", {"system_id": owner, "tool_name": name, "arguments": args}
            )
        return s.call(name, args)

    def payload(result):
        assert not result.is_error
        return json.loads(result.content[0].text)

    case_id = submit.arguments["case_id"]
    if route == "reference":
        s.replay_actions(task.evaluation_criteria.actions)
    elif route in {"proof_changed_recovered", "payment_repeat"}:
        ci = next(i for i, a in enumerate(actions) if a.action_id == "bank2:v5:consent")
        s.replay_actions(actions[:ci])
        consent, pay = actions[ci : ci + 2]
        if route == "proof_changed_recovered":
            empty = deepcopy(consent.arguments)
            empty["request"]["eligibility_evidence"] = {}
            receipt = payload(call(consent.name, empty))
            denied = call(
                pay.name,
                {**pay.arguments, "confirmation_id": receipt["confirmation_id"]},
            )
            assert (
                denied.is_error and "COMPONENT_NOT_APPLICABLE" in denied.content[0].text
            )
        receipt = payload(call(consent.name, consent.arguments))
        args = {**pay.arguments, "confirmation_id": receipt["confirmation_id"]}
        if route == "proof_changed_recovered":
            missing = deepcopy(args)
            missing.pop("eligibility_evidence")
            denied = call(pay.name, missing)
            assert (
                denied.is_error and "COMPONENT_NOT_APPLICABLE" in denied.content[0].text
            )
        paid = payload(call(pay.name, args))
        if route == "payment_repeat":
            assert payload(call(pay.name, args)) == paid
        s.replay_actions(actions[ci + 2 :])
        case = json.loads(s.log[-1][1].content)
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": case["version"]})
        )
    elif route == "old_optimum":
        pi = next(
            i for i, a in enumerate(actions) if a.name == "execute_reward_resolution"
        )
        s.replay_actions(actions[:pi])
        rejected = call(actions[pi].name, actions[pi].arguments)
        assert (
            rejected.is_error and "COMPONENT_NOT_APPLICABLE" in rejected.content[0].text
        )
    elif route == "unreceived_acceptance":
        ai = next(
            i
            for i, a in enumerate(actions)
            if a.action_id == "bank2:v8:accept-statement"
        )
        s.replay_actions(actions[:ai])
        accepted = actions[ai]
        delayed = s.begin_call(accepted.name, accepted.arguments, None)
        payload(s.call(accepted.name, accepted.arguments, attempt=delayed))
        s.replay_actions(actions[ai + 1 :])
        case = json.loads(s.log[-1][1].content)
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": case["version"]})
        )
        s.result_sent(delayed)
    elif route == "stale_consent_recovered":
        # Form an old package while funding is preliminary, then recover after
        # acceptance changes the offer. Both refused executions have no effect.
        before = next(
            i for i, a in enumerate(actions) if a.action_id == "bank2:v8:give-statement"
        )
        s.replay_actions(actions[:before])
        s.replay_actions(
            [
                a
                for a in actions
                if a.action_id.startswith("bank2:v5:doc:")
                or a.action_id == "bank2:v5:preferences"
            ]
        )
        consent = next(a for a in actions if a.action_id == "bank2:v5:consent")
        pay = next(a for a in actions if a.name == "execute_reward_resolution")
        old = deepcopy(consent.arguments)
        old["request"].update(
            offer_version=1,
            component_ids=["B", "D", "E"],
            total_amount=35,
            state_revision=1,
            payouts=[
                {"transaction_id": "txn_804219", "amount": 15},
                {"transaction_id": "txn_804227", "amount": 20},
            ],
            resulting_claims=[
                {"transaction_id": "txn_804219", "amount": 10},
                {"transaction_id": "txn_804227", "amount": 40},
                {"transaction_id": "txn_804205", "amount": 7},
            ],
        )
        receipt = payload(call(consent.name, old))
        old_pay = {
            **pay.arguments,
            **{
                k: old["request"][k]
                for k in ("offer_version", "component_ids", "total_amount", "payouts")
            },
            "confirmation_id": receipt["confirmation_id"],
            "expected_revision": 999,
        }
        assert payload(call(pay.name, old_pay))["status"] == "funding_pending"
        ci = actions.index(consent)
        s.replay_actions(actions[before:ci])
        assert payload(call(pay.name, old_pay))["status"] == "offer_changed"
        receipt = payload(call(consent.name, consent.arguments))
        payload(
            call(
                pay.name,
                {**pay.arguments, "confirmation_id": receipt["confirmation_id"]},
            )
        )
        pi = actions.index(pay)
        s.replay_actions(actions[pi + 1 :])
        case = json.loads(s.log[-1][1].content)
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": case["version"]})
        )
    elif route in {
        "recovered_budget",
        "ineligible_recovered",
        "wrong_partner_recovered",
    }:
        consent_index = next(
            i for i, a in enumerate(actions) if a.action_id == "bank2:v5:consent"
        )
        s.replay_actions(actions[:consent_index])
        consent, pay = actions[consent_index : consent_index + 2]
        bad = deepcopy(consent.arguments)
        bad["request"].update(
            component_ids=["B", "C", "G"],
            total_amount=34,
            payouts=[
                {"transaction_id": "txn_804219", "amount": 14},
                {"transaction_id": "txn_804227", "amount": 20},
            ],
            resulting_claims=[
                {"transaction_id": "txn_804219", "amount": 11},
                {"transaction_id": "txn_804227", "amount": 33},
                {"transaction_id": "txn_804205", "amount": 7},
            ],
        )
        if route == "ineligible_recovered":
            bad["request"].update(
                component_ids=["B", "D", "E", "G"],
                total_amount=42,
                payouts=[
                    {"transaction_id": "txn_804219", "amount": 18},
                    {"transaction_id": "txn_804227", "amount": 24},
                ],
                resulting_claims=[
                    {"transaction_id": "txn_804219", "amount": 7},
                    {"transaction_id": "txn_804227", "amount": 29},
                    {"transaction_id": "txn_804205", "amount": 7},
                ],
                eligibility_evidence={
                    "E": {"contract_id": "rc_610482", "record_id": "inc_620411"},
                    "G": {"contract_id": "rc_610482", "record_id": "pc_740613"},
                },
            )
        if route == "wrong_partner_recovered":
            bad = deepcopy(consent.arguments)
            bad["request"]["eligibility_evidence"]["G"]["record_id"] = "pc_740619"
        receipt = payload(call(consent.name, bad))
        bad_pay = {
            **pay.arguments,
            **{
                k: bad["request"][k]
                for k in (
                    "component_ids",
                    "total_amount",
                    "payouts",
                    "eligibility_evidence",
                )
            },
            "confirmation_id": receipt["confirmation_id"],
        }
        rejected = call(pay.name, bad_pay)
        assert (
            rejected.is_error
            and (
                "бюджет" if route == "recovered_budget" else "COMPONENT_NOT_APPLICABLE"
            )
            in rejected.content[0].text
        )
        receipt = payload(call(consent.name, consent.arguments))
        payload(
            call(
                pay.name,
                {**pay.arguments, "confirmation_id": receipt["confirmation_id"]},
            )
        )
        s.replay_actions(actions[consent_index + 2 :])
        case = json.loads(s.log[-1][1].content)
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": case["version"]})
        )
    elif route == "early":
        pay = next(
            i for i, a in enumerate(actions) if a.name == "execute_reward_resolution"
        )
        s.replay_actions(actions[:pay])
        cases = payload(
            call("get_cashback_investigations", {"customer_id": "gromov_a_1187"})
        )
        case = next(c for c in cases if c["id"] == case_id)
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": case["version"]})
        )
        for action in actions[pay:]:
            assert call(action.name, action.arguments).is_error
    elif route in {"split", "unreceived_version"}:
        amend = actions.pop()
        assert amend.name == "amend_cashback_investigation"
        s.replay_actions(actions)
        if route == "split":
            first = payload(
                call(
                    amend.name,
                    {**amend.arguments, "items": amend.arguments["items"][:1]},
                )
            )
            last = payload(
                call(
                    amend.name,
                    {
                        **amend.arguments,
                        "items": amend.arguments["items"][1:],
                        "expected_revision": first["state_revision"],
                    },
                )
            )
        else:
            # Author-only counterexample: execution happened, but the agent has
            # not received the response from which it guesses the new version.
            delayed = s.begin_call(amend.name, amend.arguments, None)
            last = payload(s.call(amend.name, amend.arguments, attempt=delayed))
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": last["version"]})
        )
        if route == "unreceived_version":
            s.result_sent(delayed)
    else:
        s.replay_actions(actions)
        case = json.loads(s.log[-1][1].content)
        args = {"case_id": case_id, "expected_version": case["version"]}
        if route != "no_submit":
            if route == "conflict_recovered":
                assert (
                    payload(call(submit.name, {**args, "expected_version": 0}))[
                        "status"
                    ]
                    == "version_conflict"
                )
            payload(call(submit.name, args))
            if route == "repeat":
                payload(call(submit.name, args))
    assert not call("done", {}).is_error
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    assert reward.final_reward == want, reward.info
    assert set(reward.reward_breakdown) == {RewardType.DB, RewardType.ACTION}
    if route in {"unreceived_version", "unreceived_acceptance", "late_eligibility"}:
        assert reward.reward_breakdown == {RewardType.DB: 1, RewardType.ACTION: 0}
        if route == "late_eligibility":
            assert "resolution_components_invalid_or_not_received" in str(reward.info)
        elif route == "unreceived_acceptance":
            assert "funding_acceptance_not_received" in str(reward.info)
        else:
            assert any(
                "review_version_not_received" in str(f)
                for f in reward.info["action"]["result_argument_bindings"]
            )
    if route in {"early", "no_submit"}:
        assert reward.reward_breakdown[RewardType.DB] == 0
    if route in {"suboptimal", "previous_offer"}:
        assert reward.reward_breakdown == {RewardType.DB: 0, RewardType.ACTION: 1}, (
            reward.info
        )
    if want:
        case = s.env.tools.db.cases[case_id]
        assert case.review_status == "registered"
        assert case.responsible_unit == "rewards_review"
        assert case.review_amount == case.amount == 49
        expected = {"txn_804219": 10, "txn_804227": 32, "txn_804205": 7}
        assert {r.transaction_id: r.amount for r in case.review_items} == expected
        assert {r.transaction_id: r.amount for r in case.reward_items} == expected

    if directory := os.environ.get("BANK2_LINKED_EVIDENCE"):
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "task_sha256": hashlib.sha256(
                        task.model_dump_json().encode()
                    ).hexdigest(),
                    "route": route,
                    "mode": mode,
                    "expected": want,
                    "final_reward": reward.final_reward,
                    "components": reward.reward_breakdown,
                    "info": reward.info,
                    "case": s.env.tools.db.cases[case_id].model_dump(mode="json"),
                    "calls": [
                        {
                            k: e.get(k)
                            for k in (
                                "canonical_name",
                                "original_name",
                                "request_order",
                                "response_order",
                                "error",
                            )
                        }
                        for e in s.mcp_events
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


@pytest.mark.parametrize(
    "bank,cap,count,total,subset,allocation",
    [
        (20, 40, 19, 3500, ("B", "D", "E"), {"txn_804219": 1500, "txn_804227": 2000}),
        (
            23,
            45,
            21,
            4200,
            ("B", "D", "E", "G"),
            {"txn_804219": 1800, "txn_804227": 2400},
        ),
    ],
)
def test_bank2_structural_menu_optimum_before_eligibility(
    bank, cap, count, total, subset, allocation
):
    from itertools import combinations

    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    task = next(t for t in get_tasks() if t.id == "Bank 2")
    session = PlatformSession(task, get_environment)
    offer = session.env.tools.db.reward_resolution_offers["offer_610735"]
    offer.amount = cap
    offer.selection_budget.bank = bank
    legal = []
    for length in range(1, len(offer.components) + 1):
        for selection in combinations([p.id for p in offer.components], length):
            try:
                amount, amounts = selected_amounts(offer, list(selection))
            except ValueError:
                continue
            legal.append((amount, selection, amounts))
    assert len(legal) == count
    assert max(legal) == (total, subset, allocation)
    assert sum(amount == total for amount, _, _ in legal) == 1
    assert selected_amounts(offer, ["B", "E", "H"])[0] == 2900
    assert selected_amounts(offer, ["B", "D", "F"])[0] == 3400
    assert "offer_610735" not in get_environment().tools.db.reward_resolution_offers


@pytest.mark.parametrize(
    "variant,total,first,components,review",
    [
        ("main", 36, 15, ["A", "C", "G"], 49),
        ("both", 42, 18, ["B", "D", "E", "G"], 43),
        ("neither", 34, 14, ["B", "D", "F"], 51),
    ],
)
def test_bank2_eligibility_changes_actual_optimum_and_whole_grade(
    variant, total, first, components, review
):
    from itertools import combinations

    from tau2.domains.banking_ru.reward_resolution import (
        selected_amounts,
        validate_eligibility,
    )

    task = next(t for t in get_tasks() if t.id == "Bank 2").model_copy(deep=True)
    data = task.initial_state.initialization_data.agent_data
    if variant == "both":
        data["service_incidents"]["inc_620411"]["contract_id"] = "rc_610482"
    if variant == "neither":
        data["partner_confirmations"]["pc_740613"]["status"] = "registered"
    proof = {
        k: {
            "contract_id": "rc_610482",
            "record_id": "inc_620411" if k == "E" else "pc_740613",
        }
        for k in components
        if k in {"E", "G"}
    }
    payouts = [
        {"transaction_id": "txn_804219", "amount": first},
        {"transaction_id": "txn_804227", "amount": total - first},
    ]
    items = [
        {"transaction_id": "txn_804219", "amount": 25 - first},
        {"transaction_id": "txn_804227", "amount": 53 - total + first},
    ]
    for a in task.evaluation_criteria.actions:
        if a.action_id == "bank2:v5:consent":
            a.arguments["request"].update(
                component_ids=components,
                payouts=payouts,
                total_amount=total,
                eligibility_evidence=proof,
                resulting_claims=items
                + [{"transaction_id": "txn_804205", "amount": 7}],
            )
        if a.name == "execute_reward_resolution":
            a.arguments.update(
                component_ids=components,
                payouts=payouts,
                total_amount=total,
                eligibility_evidence=proof,
            )
        if a.name == "amend_cashback_investigation":
            a.arguments["items"] = items
    s = PlatformSession(task, get_environment, mcp_layout="directory")
    db = s.env.tools.db
    offer = db.reward_resolution_offers["offer_610735"]
    # Financial v2 conditions are published after acceptance; use their sealed
    # proposed limits for the author-only enumeration, without calling a model.
    final = offer.model_copy(deep=True)
    final.amount = 45
    final.selection_budget.bank = 23
    available = {}
    for part in final.components:
        if part.eligibility is None:
            continue
        collection = (
            db.service_incidents
            if part.eligibility.kind == "service_incident"
            else db.partner_confirmations
        )
        for contract_id in db.reward_contracts:
            for record_id in collection:
                candidate = {
                    part.id: {"contract_id": contract_id, "record_id": record_id}
                }
                try:
                    validate_eligibility(
                        final,
                        [part.id],
                        candidate,
                        transactions=db.transactions,
                        contracts=db.reward_contracts,
                        incidents=db.service_incidents,
                        confirmations=db.partner_confirmations,
                    )
                except ValueError:
                    continue
                available[part.id] = candidate[part.id]
    assert set(available) == (
        {"E", "G"} if variant == "both" else {"G"} if variant == "main" else set()
    )
    legal = []
    for length in range(1, len(final.components) + 1):
        for subset in combinations([c.id for c in final.components], length):
            required = {
                p.id
                for p in final.components
                if p.id in subset and p.eligibility is not None
            }
            if not required <= available.keys():
                continue
            grounds = {k: available[k] for k in required}
            try:
                validate_eligibility(
                    final,
                    list(subset),
                    grounds,
                    transactions=db.transactions,
                    contracts=db.reward_contracts,
                    incidents=db.service_incidents,
                    confirmations=db.partner_confirmations,
                )
                value, allocation = selected_amounts(final, list(subset))
            except ValueError:
                continue
            legal.append((value, subset, allocation))
    assert max(legal) == (
        total * 100,
        tuple(components),
        {"txn_804219": first * 100, "txn_804227": (total - first) * 100},
    )
    assert sum(value == total * 100 for value, _, _ in legal) == 1
    s.replay_actions(task.evaluation_criteria.actions)
    assert not s.call(
        "bank_directory",
        {"operation": "open_tool", "system_id": "support", "tool_name": "done"},
    ).is_error
    assert not s.call(
        "bank_call", {"system_id": "support", "tool_name": "done", "arguments": {}}
    ).is_error
    assert s.grade().reward_breakdown == {RewardType.DB: 1, RewardType.ACTION: 1}
    assert grade_full_session(s).reward_info.reward_breakdown == {
        RewardType.DB: 1,
        RewardType.ACTION: 1,
    }
    assert db.cases["case_610903"].review_amount == review


@pytest.mark.parametrize("source", ["article", "rules", "missing", "late_rules"])
def test_bank2_category_source_equivalence(source):
    """The MCC fact may come from the article or received historical rules."""
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)
    deferred = []
    for action in task.evaluation_criteria.actions:
        if (
            source != "article"
            and action.name == "file_read"
            and action.arguments["path"] == "/knowledge/reward_merchant_categories.md"
        ):
            continue
        if action.name == "get_cashback_rules" and source in {"missing", "late_rules"}:
            deferred.append(action)
            continue
        result = session.call(action.name, action.arguments)
        assert not result.is_error, result.content
    if source == "late_rules":
        assert deferred
        session.replay_actions(deferred)
    session.call("done", {})
    category_ids = {
        item.evidence_id
        for item in task.evaluation_criteria.result_evidence
        if "doc:reward_merchant_categories:" in item.evidence_id
    }
    assert len(category_ids) == 2
    for grade in (session.grade(), grade_full_session(session).reward_info):
        assert grade.reward_breakdown[RewardType.DB] == 1
        assert grade.reward == (1 if source in {"article", "rules"} else 0)
        failures = set((grade.info.get("action") or {}).get("result_evidence", []))
        assert (
            category_ids.isdisjoint(failures)
            if source in {"article", "rules"}
            else category_ids <= failures
        )


@pytest.mark.parametrize("invalid", ["mcc", "period", "product", "split_records"])
def test_bank2_category_rules_require_one_matching_historical_record(invalid):
    from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage
    from tau2.evaluator.evaluator_action import _result_evidence_failures
    from tau2.evaluator.trace_evidence import evidence_from_messages

    task = next(t for t in get_tasks() if t.id == "Bank 2")
    session = PlatformSession(task, get_environment)
    payload = session.env.tools.db.cashback_rules["gromov_a_1187"].model_dump(
        mode="json"
    )
    evidence = [
        item
        for item in task.evaluation_criteria.result_evidence
        if "doc:reward_merchant_categories:" in item.evidence_id
    ]
    assert len(evidence) == 2
    targets = [
        next(
            a
            for a in task.evaluation_criteria.actions
            if a.action_id == item.before_action_id
        )
        for item in evidence
    ]

    def failures(data):
        read = ToolCall(
            id="rules",
            name="get_cashback_rules",
            arguments={"customer_id": "gromov_a_1187"},
        )
        messages = [
            AssistantMessage(role="assistant", tool_calls=[read]),
            ToolMessage(role="tool", id="rules", content=json.dumps(data)),
        ]
        for index, action in enumerate(targets):
            call = ToolCall(
                id=f"target-{index}", name=action.name, arguments=action.arguments
            )
            messages.append(AssistantMessage(role="assistant", tool_calls=[call]))
        return _result_evidence_failures(
            evidence_from_messages(messages).observed,
            task.evaluation_criteria.actions,
            evidence,
        )

    assert not failures(payload)
    record = next(
        row for row in payload["categories_history"] if row["id"] == "cafe-rate-july"
    )
    if invalid == "mcc":
        record["categories"][0]["mcc_codes"] = ["5411"]
    elif invalid == "period":
        record["effective_from"] = "2026-08-01"
    elif invalid == "product":
        record["product_code"] = "other_rewards"
    else:
        other = deepcopy(record)
        other["id"] = "different-record"
        record["categories"][0]["mcc_codes"] = ["5411"]
        payload["categories_history"].append(other)
    assert set(failures(payload)) == {item.evidence_id for item in evidence}
