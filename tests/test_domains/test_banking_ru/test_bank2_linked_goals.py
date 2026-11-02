"""Bank 2: authoritative unknown resolution, replacement and complete review."""

import json
from copy import deepcopy

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
    """Construct independent legal packets, preserving received evidence prefix."""
    reference = deepcopy(task.evaluation_criteria.actions)
    by_id = {a.action_id: a for a in reference}
    start = next(
        i for i, a in enumerate(reference) if a.action_id == "bank2:v12:consent-first"
    )
    actions = reference[:start]
    consent = by_id["bank2:v12:consent-first"]
    pay = by_id["bank2:v12:pay-first"]
    offers = by_id["bank2:v5:offers"]
    status = pay.model_copy(deep=True)
    status.name = "get_reward_resolution_status"
    status.action_id = "bank2:v11:status"
    status.required_for_reward = False
    status.compare_args = []
    status.arguments = {
        "resolution_operation_id": "resolution:case_610903:offer_610741"
    }
    operation_id = status.arguments["resolution_operation_id"]
    sequence = {
        "reverse": ["X", "resolve", "C"],
        "direct": ["C"],
        "early_y": ["Y", "X", "resolve"],
        "relaxed": ["X", "resolve", "R", "Y"],
        "suboptimal": ["Y"],
        "success": ["X", "resolve", "Y"],
        "success_reverse": ["Y", "X", "resolve"],
        "success_pending_y": ["X", "Y", "resolve"],
    }.get(route, ["X", "resolve", "C"])
    composite = by_id["bank2:v12:pay-first"].arguments
    packages = {
        "C": (
            "offer_610735",
            composite["component_ids"],
            [(r["transaction_id"], r["amount"]) for r in composite["payouts"]],
            {},
        ),
        "X": ("offer_610741", ["X"], [("txn_804219", 18)], {}),
        "R": ("offer_610751", ["R"], [("txn_804219", 17)], {}),
        "Z": ("offer_610743", ["Z"], [("txn_804219", 24)], {}),
        "Y": (
            "offer_610747",
            ["Y"],
            [("txn_804227", 22)],
            {"Y": {"contract_id": "rc_610482", "record_id": "pc_740613"}},
        ),
        "W": ("offer_610749", ["W"], [("txn_804227", 14)], {}),
    }
    revision, number = 2, 0
    remaining = {"txn_804219": 25, "txn_804227": 22.1}
    x_pay = None
    for key in sequence:
        if key == "resolve":
            actions.append(deepcopy(x_pay if route == "replay" else status))
            revision += 1
            if route.startswith("success"):
                remaining["txn_804219"] -= 18
            continue
        actions.append(deepcopy(offers))
        number += 1
        c, p = deepcopy(consent), deepcopy(pay)
        c.action_id, p.action_id = (
            ("bank2:v12:consent-first", "bank2:v12:pay-first")
            if key in {"R", "Z", "C"}
            else ("bank2:v10:consent-second", "bank2:v10:pay-second")
            if key in {"Y", "W"}
            else ("bank2:v5:consent", "bank2:v5:pay")
        )
        offer, components, raw_payouts, proof = packages[key]
        if route == "reverse":
            components = list(reversed(components))
        payouts = [
            {"transaction_id": tid, "amount": amount} for tid, amount in raw_payouts
        ]
        projected = dict(remaining)
        for tid, amount in raw_payouts:
            projected[tid] = round(projected[tid] - amount, 2)
        packet = dict(
            offer_id=offer,
            offer_version=2,
            component_ids=components,
            payouts=payouts,
            total_amount=sum(x["amount"] for x in payouts),
            eligibility_evidence=proof,
        )
        if key == "R":
            packet["fallback_operation_id"] = operation_id
        c.arguments["request"].update(
            **packet,
            state_revision=revision,
            resulting_claims=_claims(projected["txn_804219"], projected["txn_804227"]),
        )
        p.arguments.update(
            **packet,
            expected_revision=revision,
            confirmation_id=f"resolution-confirmation-{number}",
            idempotency_key=f"test-bank2-{key}",
        )
        if key == "R":
            minimum = deepcopy(consent)
            minimum.action_id = "bank2:v12:minimum-change"
            minimum.argument_values = {
                "request.type": "reward_resolution_minimum_change"
            }
            minimum.arguments = {
                "customer_id": "gromov_a_1187",
                "transaction_id": "txn_804219",
                "request": {
                    "type": "reward_resolution_minimum_change",
                    "case_id": "case_610903",
                    "minimum_amount": 17,
                },
            }
            actions.append(minimum)
        actions.extend([c, p])
        revision += 1
        if key == "X":
            x_pay = p
        else:
            remaining = projected
    amend = by_id["bank2:v4:amend"]
    amend.arguments.update(
        items=_claims(remaining["txn_804219"], remaining["txn_804227"])[:2],
        expected_revision=revision,
    )
    actions.extend([amend, by_id["bank2:v6:submit"]])
    return actions


def test_bank2_minimum_and_prior_payments_select_composite_reference():
    """A below-minimum replacement must not remain the main accepted reference."""
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    db = PlatformSession(task, get_environment).env.tools.db
    preferences = db.reward_resolution_preferences["gromov_a_1187"].model_dump()
    assert preferences.get("minimum_payouts") == {}
    payouts = [
        action.arguments["offer_id"]
        for action in task.evaluation_criteria.actions
        if action.name == "execute_reward_resolution"
    ]
    assert payouts == ["offer_610735"]


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,want_db,want_action",
    [
        ("reference", 1, 1),
        ("direct", 1, 1),
        ("reverse", 1, 1),
        ("early_y", 0, 1),
        ("strict_decline", 1, 1),
        ("replay", 1, 1),
        ("late_origin_snapshot", 1, 1),
        ("late_origin_replay", 1, 1),
        ("success", 1, 1),
        ("success_reverse", 1, 1),
        ("success_pending_y", 1, 1),
        ("suboptimal", 0, 1),
        ("early_r", 1, 1),
        ("old_consent", 1, 1),
        ("unreceived_status", 1, 0),
        ("late_status", 1, 0),
        ("no_submit", 0, 0),
        ("early_review", 0, 0),
        ("noop", 0, 0),
        ("unresolved", 0, 0),
    ],
)
def test_bank2_payment_and_review_whole_grade(mode, route, want_db, want_action):
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    if route == "strict_decline":
        task = task.model_copy(deep=True)
        task.initial_state.initialization_data.agent_data[
            "reward_resolution_preferences"
        ]["gromov_a_1187"]["minimum_payouts"] = {"txn_804219": 18}
    replacement_consent = deepcopy(
        next(
            a
            for a in _payment_route(task, "relaxed")
            if a.action_id == "bank2:v12:consent-first"
        )
    )
    if route.startswith("success"):
        task = task.model_copy(deep=True)
        task.initial_state.initialization_data.agent_data[
            "reward_resolution_execution_profiles"
        ] = {"offer_610741": "succeeded"}
        for evidence in task.evaluation_criteria.result_evidence:
            if evidence.before_action_id == "bank2:v12:pay-first":
                evidence.before_action_id = "bank2:v5:pay"
        task.evaluation_criteria.actions = _payment_route(task, route)
    actions = (
        _payment_route(task, "replay" if route == "late_origin_replay" else route)
        if not route.startswith("success")
        else deepcopy(task.evaluation_criteria.actions)
    )
    session = PlatformSession(
        task,
        get_environment,
        mcp_layout="directory"
        if route in {"reference", "reverse", "early_y", "replay", "relaxed", "success"}
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
        try:
            return json.loads(result.content[0].text)
        except json.JSONDecodeError:
            return {}

    delayed = None
    late_origin = None
    received_operation = None
    for action in [] if route == "noop" else actions:
        if action.name == "submit_cashback_review" and route == "no_submit":
            continue
        if route == "early_review" and action.action_id == "bank2:v5:pay":
            payload(
                call(
                    "submit_cashback_review",
                    {"case_id": "case_610903", "expected_version": 1},
                )
            )
            assert call(action.name, action.arguments).is_error
            break
        if (
            route.startswith("late_origin")
            and action.action_id == "bank2:v5:pay"
            and late_origin is None
        ):
            late_origin = session.begin_call(action.name, action.arguments, None)
            payload(session.call(action.name, action.arguments, attempt=late_origin))
            if route == "late_origin_snapshot":
                snapshot = payload(
                    call(
                        "get_reward_resolution_offers",
                        {
                            "customer_id": next(
                                a.arguments["customer_id"]
                                for a in actions
                                if a.name == "get_reward_resolution_offers"
                            ),
                            "case_id": "case_610903",
                        },
                    )
                )
                rows = (
                    snapshot
                    if isinstance(snapshot, list)
                    else snapshot.get("offers", [])
                )
                received_operation = next(
                    op["id"]
                    for row in rows
                    for op in (row.get("funding_reserve") or {}).get("operations", [])
                    if op["status"] == "execution_unknown"
                )
            continue
        if action.name == "get_reward_resolution_status":
            if route == "unresolved":
                break
            # The fixture follows the operation ID actually received by the agent.
            assert received_operation is not None
            action.arguments["resolution_operation_id"] = received_operation
            if route == "early_r":
                replacement = replacement_consent
                before = deepcopy(session.env.tools.db.reward_resolution_executions)
                assert call(replacement.name, replacement.arguments).is_error
                assert session.env.tools.db.reward_resolution_executions == before
            if route in {"unreceived_status", "late_status"}:
                delayed = session.begin_call(action.name, action.arguments, None)
                payload(session.call(action.name, action.arguments, attempt=delayed))
                continue
        if action.action_id == "bank2:v12:pay-first" and route == "old_consent":
            before = deepcopy(session.env.tools.db.reward_resolution_executions)
            denied = call(
                action.name,
                {**action.arguments, "confirmation_id": "resolution-confirmation-1"},
            )
            assert denied.is_error or payload(denied)["status"] == "consent_stale"
            assert session.env.tools.db.reward_resolution_executions == before
        if action.action_id in {
            "bank2:v12:consent-first",
            "bank2:v12:pay-first",
        }:
            target = (
                action.arguments["request"]
                if action.name == "ask_client"
                else action.arguments
            )
            if target["offer_id"] == "offer_610751":
                target["fallback_operation_id"] = received_operation
        if action.action_id == "bank2:v12:consent-first" and route == "strict_decline":
            result = payload(
                call(
                    "ask_client",
                    {
                        "customer_id": "gromov_a_1187",
                        "transaction_id": "txn_804219",
                        "request": {
                            "type": "reward_resolution_minimum_change",
                            "case_id": "case_610903",
                            "minimum_amount": 17,
                        },
                    },
                )
            )
            assert result["status"] == "declined"
            assert result["data"]["effective_minimum"] == 18
            rejected = payload(
                call(replacement_consent.name, replacement_consent.arguments)
            )
            assert rejected["status"] == "declined"
            assert "confirmation_id" not in rejected
            before = deepcopy(session.env.tools.db.reward_resolution_executions)
            replacement_pay = next(
                a
                for a in _payment_route(task, "relaxed")
                if a.action_id == "bank2:v12:pay-first"
            )
            assert call(replacement_pay.name, replacement_pay.arguments).is_error
            assert session.env.tools.db.reward_resolution_executions == before
        result = payload(call(action.name, action.arguments))
        if (
            route.startswith("success")
            and action.name == "get_reward_resolution_status"
        ):
            before = deepcopy(session.env.tools.db.reward_resolution_executions)
            replacement_consent.arguments["request"]["fallback_operation_id"] = (
                received_operation
            )
            assert call(
                replacement_consent.name, replacement_consent.arguments
            ).is_error
            assert session.env.tools.db.reward_resolution_executions == before
        if (
            action.name == "execute_reward_resolution"
            and result.get("operation_id") is not None
        ):
            received_operation = result["operation_id"]
        if route == "late_status" and action.action_id == "bank2:v12:pay-first":
            session.result_sent(delayed)
            delayed = None
    if late_origin is not None:
        session.result_sent(late_origin)
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
        case = session.env.tools.db.cases["case_610903"]
        first, second, bank, partner = (
            (7, 0.1, 22, 18) if route.startswith("success") else (5.5, 1.85, 21, 20)
        )
        assert case.review_status == "registered"
        assert case.responsible_unit == "rewards_review"
        assert case.review_amount == case.amount == round(first + second + 7, 2)
        assert {r.transaction_id: r.amount for r in case.review_items} == {
            r["transaction_id"]: r["amount"] for r in _claims(first, second)
        }
        executions = list(session.env.tools.db.reward_resolution_executions.values())
        paid = [
            x.result["funding_charge"]
            for x in executions
            if x.result["status"] == "succeeded"
        ]
        assert sum(x["bank"] for x in paid) == bank
        assert sum(x["partner"] for x in paid) == partner
        assert not any(x.result["status"] == "execution_unknown" for x in executions)


def test_bank2_unknown_outcome_reference_preserves_declined_replacement():
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    session = PlatformSession(task, get_environment)
    db = session.env.tools.db
    assert db.reward_resolution_execution_profiles == {"offer_610741": "failed_final"}
    replacement = db.reward_resolution_offers["offer_610751"]
    assert replacement.fallback_for_offer_id == "offer_610741"
    assert replacement.amount == 17
    assert replacement.components[0].bank_cost == 8
    actions = task.evaluation_criteria.actions
    assert [
        a.arguments["offer_id"]
        for a in actions
        if a.name == "execute_reward_resolution"
    ] == ["offer_610735"]


@pytest.mark.parametrize(
    "outcome,minimum,expected,components",
    [
        ("failed_final", 0, 3975, (("1A", "2B", "4B", "5B", "6B"),)),
        ("failed_final", 18, 3975, (("1A", "2B", "4B", "5B", "6B"),)),
        ("failed_final", 17, 3975, (("1A", "2B", "4B", "5B", "6B"),)),
        ("succeeded", 18, 4000, (("X",), ("Y",))),
    ],
)
def test_bank2_terminal_menu_has_unique_maximum(outcome, minimum, expected, components):
    """Enumerate legal scopes and reserve costs after authoritative X outcome."""
    from itertools import combinations

    from tau2.domains.banking_ru.reward_resolution import (
        selected_amounts,
        validate_eligibility,
    )

    task = next(t for t in get_tasks() if t.id == "Bank 2")
    db = PlatformSession(task, get_environment).env.tools.db
    proof = {
        key: {"contract_id": "rc_610482", "record_id": "pc_740613"}
        for key in ("G", "Y")
    }
    menu = []
    for original in db.reward_resolution_offers.values():
        if not original.selection_budget or not original.selection_budget.shared:
            continue
        if original.id == (
            "offer_610741" if outcome == "failed_final" else "offer_610751"
        ):
            continue
        offer = original.model_copy(deep=True)
        offer.selection_budget.bank = 23
        if offer.id == "offer_610735":
            offer.amount = 45
        for length in range(1, len(offer.components) + 1):
            for subset in combinations([c.id for c in offer.components], length):
                try:
                    validate_eligibility(
                        offer,
                        list(subset),
                        {k: v for k, v in proof.items() if k in subset},
                        transactions=db.transactions,
                        contracts=db.reward_contracts,
                        incidents=db.service_incidents,
                        confirmations=db.partner_confirmations,
                    )
                    amount, allocation = selected_amounts(offer, list(subset))
                    if any(
                        value > {"txn_804219": 2500, "txn_804227": 2210}[tid]
                        for tid, value in allocation.items()
                    ):
                        continue
                    if any(
                        tid == "txn_804219" and payout < minimum * 100
                        for tid, payout in allocation.items()
                    ):
                        continue
                except ValueError:
                    continue
                selected = [c for c in offer.components if c.id in subset]
                menu.append(
                    (
                        subset,
                        set(offer.transaction_ids),
                        amount,
                        sum(round(c.bank_cost * 100) for c in selected),
                        sum(round(c.partner_cost * 100) for c in selected),
                    )
                )
    legal = []
    for length in (1, 2):
        for choices in combinations(menu, length):
            if length == 2 and choices[0][1] & choices[1][1]:
                continue
            if (
                sum(c[3] for c in choices) <= 2300
                and sum(c[4] for c in choices) <= 2000
            ):
                legal.append((sum(c[2] for c in choices), tuple(c[0] for c in choices)))
    assert max(amount for amount, _ in legal) == expected
    assert [parts for amount, parts in legal if amount == expected] == [components]
    assert (3800, (("Z",), ("W",))) in legal
    assert (3600, (("A", "C", "G"),)) not in legal


@pytest.mark.parametrize("delivery", ["received", "unreceived", "late", "replayed"])
def test_composite_requires_received_permission_to_lower_minimum(delivery):
    """A scenario with a binding minimum tests receipt, not an obsolete R path."""
    task = next(t for t in get_tasks() if t.id == "Bank 2").model_copy(deep=True)
    prefs = task.initial_state.initialization_data.agent_data[
        "reward_resolution_preferences"
    ]["gromov_a_1187"]
    prefs["minimum_payouts"] = {"txn_804219": 20}
    prefs["text"] = "По первой покупке хочу не меньше 20 рублей."
    prefs["minimum_payout_reduction_limits"] = {"txn_804219": 19.5}
    actions = [
        a
        for a in task.evaluation_criteria.actions
        if a.action_id
        not in {
            "bank2:v5:consent",
            "bank2:v5:pay",
            "bank2:v11:status",
            "bank2:v10:offers-after-first",
        }
    ]
    consent = next(a for a in actions if a.action_id == "bank2:v12:consent-first")
    change = consent.model_copy(deep=True)
    change.action_id = "test:minimum-change"
    change.argument_values = {"request.type": "reward_resolution_minimum_change"}
    change.arguments = {
        "customer_id": "gromov_a_1187",
        "transaction_id": "txn_804219",
        "request": {
            "type": "reward_resolution_minimum_change",
            "case_id": "case_610903",
            "minimum_amount": 19.5,
        },
    }
    preferences_action = consent.model_copy(deep=True)
    preferences_action.action_id = "test:read-minimum"
    preferences_action.required_for_reward = False
    preferences_action.argument_values = {}
    preferences_action.arguments = {
        "customer_id": "gromov_a_1187",
        "request": {"type": "case_clarification", "topic": "resolution_preferences"},
    }
    actions.insert(actions.index(consent), preferences_action)
    actions.insert(actions.index(consent), change)
    consent.action_id = "bank2:v5:consent"
    consent.arguments["request"]["state_revision"] = 2
    pay = next(a for a in actions if a.name == "execute_reward_resolution")
    pay.action_id = "bank2:v5:pay"
    pay.required_for_reward = True
    pay.arguments.update(
        expected_revision=2, confirmation_id="resolution-confirmation-1"
    )
    next(a for a in actions if a.name == "amend_cashback_investigation").arguments[
        "expected_revision"
    ] = 3
    for evidence in task.evaluation_criteria.result_evidence:
        if evidence.before_action_id == "bank2:v12:pay-first":
            evidence.before_action_id = "bank2:v5:pay"
    task.evaluation_criteria.actions = actions
    task = type(task).model_validate_json(task.model_dump_json())
    session = PlatformSession(task, get_environment, max_errors=0)
    delayed = None
    for action in task.evaluation_criteria.actions:
        if action.action_id == "test:minimum-change" and delivery != "received":
            delayed = session.begin_call(action.name, action.arguments, None)
            result = session.call(action.name, action.arguments, attempt=delayed)
            assert not result.is_error
            assert json.loads(result.content[0].text)["status"] == "accepted"
            if delivery == "replayed":
                assert not session.call(action.name, action.arguments).is_error
            continue
        assert not session.call(action.name, action.arguments).is_error
        if action.action_id == "bank2:v5:consent" and delivery == "late":
            session.result_sent(delayed)
    if delayed is not None and delivery != "late":
        session.result_sent(delayed)
    session.call("done", {})
    for grade in [session.grade(), grade_full_session(session).reward_info]:
        assert grade.reward_breakdown == {
            RewardType.DB: 1,
            RewardType.ACTION: int(delivery in {"received", "replayed"}),
        }, grade.info


@pytest.mark.parametrize("delivery", ["received", "unreceived", "late"])
def test_direct_package_requires_received_consent(delivery):
    """Correct money cannot hide execution before consent reached the agent."""
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    session = PlatformSession(task, get_environment, max_errors=0)
    pending = None
    for action in task.evaluation_criteria.actions:
        if action.action_id == "bank2:v12:consent-first" and delivery != "received":
            pending = session.begin_call(action.name, action.arguments, None)
            reply = session.call(action.name, action.arguments, attempt=pending)
        else:
            reply = session.call(action.name, action.arguments)
        assert not reply.is_error
        if action.name == "execute_reward_resolution" and delivery == "late":
            session.result_sent(pending)
    if pending is not None and delivery == "unreceived":
        session.result_sent(pending)
    session.call("done", {})
    for grade in [session.grade(), grade_full_session(session).reward_info]:
        assert grade.reward_breakdown == {
            RewardType.DB: 1,
            RewardType.ACTION: int(delivery == "received"),
        }, grade.info
