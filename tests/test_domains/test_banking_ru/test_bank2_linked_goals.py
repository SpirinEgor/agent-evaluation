"""Bank 2: shared reserve, exact consent, eligibility and complete case handoff."""

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


def _claims(first, second):
    return [
        {"transaction_id": "txn_804219", "amount": first},
        {"transaction_id": "txn_804227", "amount": second},
        {"transaction_id": "txn_804205", "amount": 7},
    ]


def _payment_route(task, route):
    """Build legal current-menu alternatives without changing the scoring task."""
    actions = deepcopy(task.evaluation_criteria.actions)
    ci = next(i for i, a in enumerate(actions) if a.action_id == "bank2:v5:consent")
    first_consent, first_pay = actions[ci : ci + 2]
    second_consent = next(
        a for a in actions if a.action_id == "bank2:v10:consent-second"
    )
    second_pay = next(a for a in actions if a.action_id == "bank2:v10:pay-second")
    packages = {
        "X": ("offer_610741", ["X"], [("txn_804219", 18)], {}),
        "Z": ("offer_610743", ["Z"], [("txn_804219", 24)], {}),
        "Y": (
            "offer_610747",
            ["Y"],
            [("txn_804227", 22)],
            {"Y": {"contract_id": "rc_610482", "record_id": "pc_740613"}},
        ),
        "W": ("offer_610749", ["W"], [("txn_804227", 14)], {}),
        "ACG": (
            "offer_610735",
            ["A", "C", "G"],
            [("txn_804219", 15), ("txn_804227", 21)],
            {"G": {"contract_id": "rc_610482", "record_id": "pc_740613"}},
        ),
    }
    selected = (
        ["Y", "X"]
        if route == "reverse"
        else ["Z", "W"]
        if route in {"suboptimal", "overdraw_recovered"}
        else ["ACG"]
        if route == "old_acg"
        else ["X", "Y"]
    )
    remaining = {"txn_804219": 25, "txn_804227": 53}
    for index, key in enumerate(selected):
        consent, pay = (
            (first_consent, first_pay) if index == 0 else (second_consent, second_pay)
        )
        offer, components, raw_payouts, proof = packages[key]
        payouts = [
            {"transaction_id": tid, "amount": amount} for tid, amount in raw_payouts
        ]
        for tid, amount in raw_payouts:
            remaining[tid] -= amount
        packet = dict(
            offer_id=offer,
            offer_version=2,
            component_ids=components,
            payouts=payouts,
            total_amount=sum(p["amount"] for p in payouts),
            eligibility_evidence=proof,
        )
        consent.arguments["request"].update(
            **packet,
            resulting_claims=_claims(remaining["txn_804219"], remaining["txn_804227"]),
            state_revision=2 + index,
        )
        pay.arguments.update(
            **packet,
            expected_revision=2 + index,
            confirmation_id=f"resolution-confirmation-{index + 1}",
        )
    if len(selected) == 1:
        actions = [
            a
            for a in actions
            if a.action_id
            not in {
                "bank2:v10:consent-second",
                "bank2:v10:pay-second",
                "bank2:v10:offers-after-first",
            }
        ]
    amend = next(a for a in actions if a.name == "amend_cashback_investigation")
    amend.arguments.update(
        items=_claims(remaining["txn_804219"], remaining["txn_804227"])[:2],
        expected_revision=2 + len(selected),
    )
    return actions


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,want_db,want_action",
    [
        ("reference", 1, 1),
        ("reverse", 1, 1),
        ("split", 1, 1),
        ("suboptimal", 0, 1),
        ("old_acg", 0, 1),
        ("overdraw_recovered", 0, 1),
        ("early", 0, 0),
        ("no_submit", 0, 0),
        ("unreceived_version", 1, 0),
        ("unreceived_acceptance", 1, 0),
        ("late_eligibility", 1, 0),
        ("conflict_recovered", 1, 1),
        ("repeat", 1, 1),
        ("final_first", 1, 1),
        ("wrong_partner_recovered", 1, 1),
        ("proof_changed_recovered", 1, 1),
        ("payment_repeat", 1, 1),
        ("delayed_payment_repeat", 1, 1),
        ("delayed_payment_original_replayed", 1, 1),
        ("unreceived_payment", 1, 0),
        ("stale_consent_recovered", 1, 1),
    ],
)
def test_bank2_payment_and_review_whole_grade(mode, route, want_db, want_action):
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    actions = _payment_route(task, route)
    if route == "final_first":
        actions = [a for a in actions if a.action_id != "bank2:v8:preliminary-offers"]
    if route == "late_eligibility":
        source = actions.pop(
            next(
                i
                for i, a in enumerate(actions)
                if a.name == "get_partner_confirmations"
            )
        )
        actions.insert(
            next(
                i
                for i, a in enumerate(actions)
                if a.action_id == "bank2:v10:pay-second"
            ),
            source,
        )
    if route == "split":
        for action in actions:
            if action.name == "file_read":
                action.name, action.arguments = (
                    "corpus_command",
                    {"command": "cat " + action.arguments["path"]},
                )
    submit = actions.pop()
    assert submit.name == "submit_cashback_review"
    session = PlatformSession(
        task,
        get_environment,
        mcp_layout="directory"
        if route in {"reference", "reverse", "split"}
        else "single",
        max_errors=0,
    )

    def call(name, args):
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

    def payload(result):
        assert not result.is_error, result.content
        return json.loads(result.content[0].text)

    def replay(items):
        for item in items:
            result = call(item.name, item.arguments)
            assert not result.is_error, result.content

    case_id = "case_610903"
    delayed = None
    if route == "early":
        pi = next(
            i for i, a in enumerate(actions) if a.name == "execute_reward_resolution"
        )
        replay(actions[:pi])
        cases = payload(
            call("get_cashback_investigations", {"customer_id": "gromov_a_1187"})
        )
        case = next(c for c in cases if c["id"] == case_id)
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": case["version"]})
        )
        assert call(actions[pi].name, actions[pi].arguments).is_error
    elif route in {
        "overdraw_recovered",
        "wrong_partner_recovered",
        "proof_changed_recovered",
        "payment_repeat",
        "stale_consent_recovered",
    }:
        ci = next(
            i
            for i, a in enumerate(actions)
            if a.action_id == "bank2:v10:consent-second"
        )
        replay(actions[:ci])
        consent, pay = actions[ci : ci + 2]
        if route != "payment_repeat":
            bad = deepcopy(consent.arguments)
            if route == "overdraw_recovered":
                bad["request"].update(
                    offer_id="offer_610747",
                    component_ids=["Y"],
                    payouts=[{"transaction_id": "txn_804227", "amount": 22}],
                    total_amount=22,
                    eligibility_evidence={
                        "Y": {"contract_id": "rc_610482", "record_id": "pc_740613"}
                    },
                    resulting_claims=_claims(1, 31),
                )
            elif route == "wrong_partner_recovered":
                bad["request"]["eligibility_evidence"]["Y"]["record_id"] = "pc_740619"
            elif route == "proof_changed_recovered":
                bad["request"]["eligibility_evidence"] = {}
            else:
                bad["request"]["state_revision"] = 2
            receipt = payload(call(consent.name, bad))
            bad_pay = {
                **pay.arguments,
                **{
                    k: bad["request"][k]
                    for k in (
                        "offer_id",
                        "component_ids",
                        "payouts",
                        "total_amount",
                        "eligibility_evidence",
                    )
                },
                "confirmation_id": receipt["confirmation_id"],
            }
            before = deepcopy(session.env.tools.db.reward_resolution_executions)
            denied = call(pay.name, bad_pay)
            if route == "stale_consent_recovered":
                assert payload(denied)["status"] == "consent_stale"
            else:
                assert denied.is_error, denied.content
                assert (
                    "SHARED_FUNDING_EXHAUSTED"
                    if route == "overdraw_recovered"
                    else "COMPONENT_NOT_APPLICABLE"
                ) in denied.content[0].text
            assert session.env.tools.db.reward_resolution_executions == before
        receipt = payload(call(consent.name, consent.arguments))
        args = {**pay.arguments, "confirmation_id": receipt["confirmation_id"]}
        if route == "proof_changed_recovered":
            missing = deepcopy(args)
            missing.pop("eligibility_evidence")
            assert call(pay.name, missing).is_error
        paid = payload(call(pay.name, args))
        if route == "payment_repeat":
            assert payload(call(pay.name, args)) == paid
            assert len(session.env.tools.db.reward_resolution_executions) == 2
        replay(actions[ci + 2 :])
    elif route in {
        "delayed_payment_repeat",
        "delayed_payment_original_replayed",
        "unreceived_payment",
    }:
        pi = next(
            i for i, a in enumerate(actions) if a.action_id == "bank2:v10:pay-second"
        )
        replay(actions[:pi])
        pay = actions[pi]
        if route == "delayed_payment_repeat":
            payload(call(pay.name, pay.arguments))
        delayed = session.begin_call(pay.name, pay.arguments, None)
        payload(session.call(pay.name, pay.arguments, attempt=delayed))
        if route == "delayed_payment_original_replayed":
            normalized = deepcopy(pay.arguments)
            normalized["total_amount"] = float(normalized["total_amount"])
            payload(call(pay.name, normalized))
        replay(actions[pi + 1 :])
    elif route == "unreceived_acceptance":
        ai = next(
            i
            for i, a in enumerate(actions)
            if a.action_id == "bank2:v8:accept-statement"
        )
        replay(actions[:ai])
        accepted = actions[ai]
        delayed = session.begin_call(accepted.name, accepted.arguments, None)
        payload(session.call(accepted.name, accepted.arguments, attempt=delayed))
        replay(actions[ai + 1 :])
    elif route in {"split", "unreceived_version"}:
        amend = actions.pop()
        assert amend.name == "amend_cashback_investigation"
        replay(actions)
        if route == "split":
            first = payload(
                call(
                    amend.name,
                    {**amend.arguments, "items": amend.arguments["items"][:1]},
                )
            )
            payload(
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
            delayed = session.begin_call(amend.name, amend.arguments, None)
            payload(session.call(amend.name, amend.arguments, attempt=delayed))
    else:
        replay(actions)
    if route not in {"early", "no_submit"}:
        case = session.env.tools.db.cases[case_id]
        args = {"case_id": case_id, "expected_version": case.version}
        if route == "conflict_recovered":
            assert (
                payload(call(submit.name, {**args, "expected_version": 0}))["status"]
                == "version_conflict"
            )
        payload(call(submit.name, args))
        if route == "repeat":
            payload(call(submit.name, args))
    if delayed is not None:
        session.result_sent(delayed)
    assert not call("done", {}).is_error
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert reward.reward_breakdown == {
        RewardType.DB: want_db,
        RewardType.ACTION: want_action,
    }, reward.info
    if want_db:
        case = session.env.tools.db.cases[case_id]
        assert case.review_status == "registered"
        assert case.responsible_unit == "rewards_review"
        assert case.review_amount == case.amount == 45
        executions = list(session.env.tools.db.reward_resolution_executions.values())
        assert len(executions) == 2
        charges = [operation.result["funding_charge"] for operation in executions]
        assert {charge["reserve_id"] for charge in charges} == {"budget_610735"}
        assert sum(charge["bank"] for charge in charges) == 22
        assert sum(charge["partner"] for charge in charges) == 18
        assert {r.transaction_id: r.amount for r in case.review_items} == {
            "txn_804219": 7,
            "txn_804227": 31,
            "txn_804205": 7,
        }
        assert {r.transaction_id: r.amount for r in case.reward_items} == {
            "txn_804219": 7,
            "txn_804227": 31,
            "txn_804205": 7,
        }
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
                    "expected": {"DB": want_db, "ACTION": want_action},
                    "final_reward": reward.final_reward,
                    "components": reward.reward_breakdown,
                    "info": reward.info,
                    "case": session.env.tools.db.cases[case_id].model_dump(mode="json"),
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
                        for e in session.mcp_events
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


def test_bank2_current_menu_has_unique_shared_reserve_optimum():
    """Independent enumeration catches accidental reserve resets or larger legal paths."""
    from itertools import combinations

    from tau2.domains.banking_ru.reward_resolution import (
        selected_amounts,
        validate_eligibility,
    )

    task = next(t for t in get_tasks() if t.id == "Bank 2")
    session = PlatformSession(task, get_environment)
    db = session.env.tools.db
    menu = []
    proof = {
        "G": {"contract_id": "rc_610482", "record_id": "pc_740613"},
        "Y": {"contract_id": "rc_610482", "record_id": "pc_740613"},
    }
    for original in db.reward_resolution_offers.values():
        if original.selection_budget is None or not original.selection_budget.shared:
            continue
        offer = original.model_copy(deep=True)
        offer.selection_budget.bank = 23
        if offer.id == "offer_610735":
            offer.amount = 45
        for length in range(1, len(offer.components) + 1):
            for subset in combinations([c.id for c in offer.components], length):
                evidence = {k: v for k, v in proof.items() if k in subset}
                try:
                    validate_eligibility(
                        offer,
                        list(subset),
                        evidence,
                        transactions=db.transactions,
                        contracts=db.reward_contracts,
                        incidents=db.service_incidents,
                        confirmations=db.partner_confirmations,
                    )
                    amount, allocation = selected_amounts(offer, list(subset))
                except ValueError:
                    continue
                parts = [c for c in offer.components if c.id in subset]
                menu.append(
                    (
                        offer.id,
                        subset,
                        set(offer.transaction_ids),
                        amount,
                        allocation,
                        round(sum(p.bank_cost for p in parts) * 100),
                        round(sum(p.partner_cost for p in parts) * 100),
                    )
                )
    legal = []
    for length in (1, 2):
        for choices in combinations(menu, length):
            if length == 2 and choices[0][2] & choices[1][2]:
                continue
            bank = sum(c[5] for c in choices)
            partner = sum(c[6] for c in choices)
            if bank <= 2300 and partner <= 2000:
                legal.append(
                    (
                        sum(c[3] for c in choices),
                        tuple(c[1] for c in choices),
                        bank,
                        partner,
                    )
                )
    assert max(value for value, _, _, _ in legal) == 4000
    assert [row for row in legal if row[0] == 4000] == [
        (4000, (("X",), ("Y",)), 2200, 1800)
    ]
    assert (3800, (("Z",), ("W",)), 1900, 1900) in legal
    assert (3600, (("A", "C", "G"),), 2100, 2000) in legal
    assert all(parts != (("Z",), ("Y",)) for _, parts, _, _ in legal)
    assert "offer_610735" not in get_environment().tools.db.reward_resolution_offers
    # Legacy approved preserve-rights offers and the manual path cannot beat40.
    from tau2.domains.banking_ru.reward_workflows import cents

    scope = {"txn_804219", "txn_804227"}
    already_paid = sum(
        cents(tx.amount)
        for tx in db.transactions.values()
        if tx.customer_id == "gromov_a_1187"
        and tx.original_transaction_id in scope
        and tx.status == "posted"
        and (tx.kind == "cashback" or tx.compensates_cashback)
    )
    for offer in db.reward_resolution_offers.values():
        if (
            offer.customer_id == "gromov_a_1187"
            and not offer.components
            and offer.status == "approved"
            and offer.rights == "preserve"
        ):
            amount = cents(offer.amount) - (
                already_paid if offer.amount_basis == "cumulative" else 0
            )
            assert amount < 4000
    permissions = [
        p
        for p in db.cashback_adjustment_permissions.values()
        if p["customer_id"] == "gromov_a_1187"
        and p["transaction_id"] in scope
        and p["status"] == "approved"
    ]
    assert {p["transaction_id"] for p in permissions} == {"txn_804219"}
    assert max(cents(p["remaining_amount"]) for p in permissions) + 2200 < 4000


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
    assert len(category_ids) >= 2
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
    assert len(evidence) >= 2
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


@pytest.mark.parametrize("source", ["policy", "knowledge", "missing", "late_knowledge"])
def test_bank2_review_rules_allow_equivalent_received_source(source):
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)
    delayed = None
    for action in task.evaluation_criteria.actions:
        if action.name == "file_read":
            path = action.arguments["path"]
            if path == "/policies/fees_rewards.md" and source != "policy":
                continue
            if path == "/knowledge/reward_case_amendment.md" and source in {
                "missing",
                "late_knowledge",
            }:
                delayed = action
                continue
        result = session.call(action.name, action.arguments)
        assert not result.is_error, result.content
    if source == "late_knowledge":
        assert delayed is not None
        session.replay_actions([delayed])
    session.call("done", {})
    for grade in (session.grade(), grade_full_session(session).reward_info):
        assert grade.reward_breakdown[RewardType.DB] == 1
        assert grade.reward == (1 if source in {"policy", "knowledge"} else 0), (
            grade.info
        )
        missing = (grade.info.get("action") or {}).get("result_evidence", [])
        assert ("bank2:v6:received-review-policy" in missing) == (
            source in {"missing", "late_knowledge"}
        )


@pytest.mark.parametrize("invalid", ["partial", "hash", "search_only"])
def test_bank2_review_rules_reject_incomplete_or_unrelated_evidence(invalid):
    from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage
    from tau2.evaluator.evaluator_action import _result_evidence_failures
    from tau2.evaluator.trace_evidence import evidence_from_messages

    task = next(t for t in get_tasks() if t.id == "Bank 2")
    rule = next(
        e
        for e in task.evaluation_criteria.result_evidence
        if e.evidence_id == "bank2:v6:received-review-policy"
    )
    target = next(
        a
        for a in task.evaluation_criteria.actions
        if a.action_id == rule.before_action_id
    )
    s = PlatformSession(task, get_environment)
    for path in ["/policies/fees_rewards.md", "/knowledge/reward_case_amendment.md"]:
        payload = json.loads(s.call("file_read", {"path": path}).content[0].text)

        def failures(data, name="file_read"):
            trace = evidence_from_messages(
                [
                    AssistantMessage(
                        role="assistant",
                        tool_calls=[
                            ToolCall(id="source", name=name, arguments={"path": path})
                        ],
                    ),
                    ToolMessage(role="tool", id="source", content=json.dumps(data)),
                    AssistantMessage(
                        role="assistant",
                        tool_calls=[
                            ToolCall(
                                id="target",
                                name=target.name,
                                arguments=target.arguments,
                            )
                        ],
                    ),
                    ToolMessage(
                        role="tool",
                        id="target",
                        content=json.dumps({"review_status": "registered"}),
                    ),
                ]
            )
            return _result_evidence_failures(
                trace.observed, task.evaluation_criteria.actions, [rule]
            )

        assert not failures(payload), path
        if invalid == "partial":
            payload["complete"] = False
        elif invalid == "hash":
            payload["sha256"] = "outdated-revision"
        assert failures(
            payload, "file_search" if invalid == "search_only" else "file_read"
        ) == [rule.evidence_id]
