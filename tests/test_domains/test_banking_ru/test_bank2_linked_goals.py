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
        i for i, a in enumerate(reference) if a.action_id == "bank2:v5:consent"
    )
    actions = reference[:start]
    consent = by_id["bank2:v5:consent"]
    pay = by_id["bank2:v5:pay"]
    offers = by_id["bank2:v5:offers"]
    status = by_id["bank2:v11:status"]
    operation_id = status.arguments["resolution_operation_id"]
    sequence = {
        "reverse": ["X", "resolve", "W", "Z"],
        "early_y": ["Y", "X", "resolve"],
        "relaxed": ["X", "resolve", "R", "Y"],
        "unreceived_minimum": ["X", "resolve", "R", "Y"],
        "late_minimum": ["X", "resolve", "R", "Y"],
        "late_minimum_replay": ["X", "resolve", "R", "Y"],
        "suboptimal": ["Y"],
        "success": ["X", "resolve", "Y"],
        "success_reverse": ["Y", "X", "resolve"],
        "success_pending_y": ["X", "Y", "resolve"],
    }.get(route, ["X", "resolve", "Z", "W"])
    packages = {
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
    remaining = {"txn_804219": 25, "txn_804227": 53}
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
            if key in {"R", "Z"}
            else ("bank2:v10:consent-second", "bank2:v10:pay-second")
            if key in {"Y", "W"}
            else ("bank2:v5:consent", "bank2:v5:pay")
        )
        offer, components, raw_payouts, proof = packages[key]
        payouts = [
            {"transaction_id": tid, "amount": amount} for tid, amount in raw_payouts
        ]
        projected = dict(remaining)
        for tid, amount in raw_payouts:
            projected[tid] -= amount
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


def test_bank2_strict_first_purchase_minimum_selects_zw_reference():
    """A below-minimum replacement must not remain the main accepted reference."""
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    db = PlatformSession(task, get_environment).env.tools.db
    preferences = db.reward_resolution_preferences["gromov_a_1187"].model_dump()
    assert preferences.get("minimum_payouts") == {"txn_804219": 18}
    payouts = [
        action.arguments["offer_id"]
        for action in task.evaluation_criteria.actions
        if action.name == "execute_reward_resolution"
    ]
    assert payouts == ["offer_610741", "offer_610743", "offer_610749"]


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,want_db,want_action",
    [
        ("reference", 1, 1),
        ("reverse", 1, 1),
        ("early_y", 0, 1),
        ("relaxed", 1, 1),
        ("strict_decline", 1, 1),
        ("unreceived_minimum", 1, 0),
        ("late_minimum", 1, 0),
        ("late_minimum_replay", 1, 1),
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
    replacement_consent = deepcopy(
        next(
            a
            for a in _payment_route(task, "relaxed")
            if a.action_id == "bank2:v12:consent-first"
        )
    )
    if route in {
        "relaxed",
        "unreceived_minimum",
        "late_minimum",
        "late_minimum_replay",
    }:
        task = task.model_copy(deep=True)
        task.initial_state.initialization_data.agent_data[
            "reward_resolution_preferences"
        ]["gromov_a_1187"]["minimum_payout_reduction_limits"] = {"txn_804219": 17}
        task.evaluation_criteria.actions = _payment_route(task, "relaxed")
    if route.startswith("success"):
        task = task.model_copy(deep=True)
        task.initial_state.initialization_data.agent_data[
            "reward_resolution_execution_profiles"
        ] = {"offer_610741": "succeeded"}
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
    minimum_delayed = None
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
        if action.action_id == "bank2:v12:minimum-change" and route in {
            "unreceived_minimum",
            "late_minimum",
            "late_minimum_replay",
        }:
            minimum_delayed = session.begin_call(action.name, action.arguments, None)
            result = payload(
                session.call(action.name, action.arguments, attempt=minimum_delayed)
            )
            assert result["status"] == "accepted"
            if route == "late_minimum_replay":
                assert payload(call(action.name, action.arguments)) == result
            continue
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
        if route == "late_minimum" and action.action_id == "bank2:v12:consent-first":
            session.result_sent(minimum_delayed)
            minimum_delayed = None
        if route == "late_status" and action.action_id == "bank2:v12:pay-first":
            session.result_sent(delayed)
            delayed = None
    if late_origin is not None:
        session.result_sent(late_origin)
    if delayed is not None:
        session.result_sent(delayed)
    if minimum_delayed is not None:
        session.result_sent(minimum_delayed)
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
            (7, 31, 22, 18)
            if route.startswith("success")
            else (8, 31, 21, 18)
            if route
            in {"relaxed", "unreceived_minimum", "late_minimum", "late_minimum_replay"}
            else (1, 39, 19, 19)
        )
        assert case.review_status == "registered"
        assert case.responsible_unit == "rewards_review"
        assert case.review_amount == case.amount == first + second + 7
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
    ] == ["offer_610741", "offer_610743", "offer_610749"]


@pytest.mark.parametrize(
    "outcome,minimum,expected,components",
    [
        ("failed_final", 18, 3800, (("Z",), ("W",))),
        ("failed_final", 17, 3900, (("Y",), ("R",))),
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
