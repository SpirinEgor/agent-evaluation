"""Addressed integration routes for composite rewards/refunds/document delivery."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("task_id", ["Bank 2", "Bank 40", "Bank 41"])
def test_composite_reference_has_required_business_outcome(task_id):
    task = next(t for t in get_tasks() if t.id == task_id)
    s = PlatformSession(task, get_environment, max_calls=150, max_errors=50)
    for a in task.evaluation_criteria.actions:
        result = s.call(a.name, a.arguments)
        assert not result.is_error, (a.action_id, result.content[0].text)
    s.call("done", {})
    if task_id == "Bank 2":
        cases = [
            c
            for c in s.env.tools.db.cases.values()
            if c.customer_id == "gromov_a_1187"
            and c.category == "cashback_investigation"
        ]
        assert len(cases) == 1 and cases[0].amount == 35
        assert [(x.transaction_id, x.amount) for x in cases[0].reward_items] == [
            ("txn_552290", 10),
            ("txn_552291", 25),
        ]
        assert (
            s.env.tools.db.cashback_adjustment_permissions["adjustment-A"][
                "used_amount"
            ]
            == 20
        )
    elif task_id == "Bank 40":
        assert s.env.tools.db.accounts["acc_7734"].balance == 4080
        credits = [
            x
            for x in s.env.tools.db.transactions.values()
            if x.id.startswith("fee-refund:")
        ]
        assert sorted((x.original_transaction_id, x.amount) for x in credits) == [
            ("txn_088219", 139),
            ("txn_088220", 129),
        ]
    else:
        case = s.env.tools.db.cases["case-n40-current"]
        assert case.amount == 178 and case.version == 2
        assert len(s.env.tools.db.document_deliveries) == 1
    assert s.grade().reward == 1
    assert grade_full_session(s).reward_info.reward == 1


CASES = [
    (n, route, expected, mode)
    for n in ["Bank 2", "Bank 40", "Bank 41"]
    for route, expected in [
        ("reference", 1),
        ("alternative", 1),
        ("same_key_retry", 1),
        ("noop", 0),
        ("wrong_object", 0),
        ("wrong_sum", 0),
        ("missing_basis", 0),
        ("forbidden_error_then_correct", 0),
        ("duplicate_execution", 0),
        ("late_basis", 0),
        ("unconfirmed_completion", 0),
    ]
    for mode in (
        ["half", "full"]
        if route in ["reference", "alternative", "late_basis"]
        else ["full"]
    )
]


@pytest.mark.parametrize("task_id,route,expected,mode", CASES)
def test_composite_whole_grade(task_id, route, expected, mode):
    task = next(t for t in get_tasks() if t.id == task_id)
    s = PlatformSession(task, get_environment, max_calls=150, max_errors=50)
    actions = list(task.evaluation_criteria.actions)
    delayed = None
    write = {
        "Bank 2": "bank2:adjust",
        "Bank 40": "refund_fee_Bank 40",
        "Bank 41": "bank41:document-delivery",
    }[task_id]
    basis = {
        "Bank 2": "get_adjustment_authority_Bank 2",
        "Bank 40": "bank40:final-consent",
        "Bank 41": "bank41:case-documents",
    }[task_id]
    if route == "alternative":
        if task_id == "Bank 2":
            payout = next(a for a in actions if a.action_id == write)
            actions.remove(payout)
            actions.append(payout)
        elif task_id == "Bank 40":
            s_marker = next(
                i for i, a in enumerate(actions) if a.action_id == "bank40:consent"
            )
            # Early authorized state and independent C-before-B execution.
            early = deepcopy(next(a for a in actions if a.action_id == "bank40:state"))
            early.action_id = "diagnostic-early"
            actions.insert(s_marker, early)
            c = next(a for a in actions if a.action_id == "bank40:refund-C")
            actions.remove(c)
            actions.insert(
                next(i for i, a in enumerate(actions) if a.action_id == write), c
            )
        else:
            actions = [a for a in actions if a.action_id != "bank41:channel-confirm"]
    if route != "noop":
        for a in actions:
            args = deepcopy(a.arguments)
            if route == "missing_basis" and a.action_id == basis:
                continue
            if route == "unconfirmed_completion" and (
                (task_id == "Bank 2" and a.action_id == "cashback_investigation_Bank 2")
                or a.action_id in ["bank40:receipt-B", "bank41:delivery-receipt"]
            ):
                continue
            if route == "alternative" and task_id == "Bank 41" and a.action_id == write:
                args["contact_id"] = "doc-contact-egorova_n_3358"
            if route == "late_basis" and a.action_id == basis:
                attempt = s.begin_call(a.name, args, None)
                s.call(a.name, args, attempt=attempt)
                delayed = attempt
                continue
            if a.action_id == write and route in [
                "wrong_object",
                "wrong_sum",
                "forbidden_error_then_correct",
            ]:
                bad = deepcopy(args)
                if route == "wrong_sum":
                    bad["amount"] = (
                        999 if task_id != "Bank 41" else bad.get("amount", 999)
                    )
                elif task_id == "Bank 2":
                    bad["permission_id"] = "adjustment-other-purchase"
                elif task_id == "Bank 40":
                    bad["transaction_id"] = "txn_088214"
                else:
                    bad["document_id"] = "case-n40-current:internal_diagnostics"
                if task_id == "Bank 41" and route == "wrong_sum":
                    s.call(
                        "amend_cashback_investigation",
                        {
                            "case_id": "case-n40-current",
                            "items": [{"transaction_id": "txn_077040", "amount": 109}],
                        },
                    )
                else:
                    s.call(a.name, bad)
            s.call(a.name, args)
            if a.action_id == write:
                if route == "same_key_retry":
                    s.call(a.name, args)
                if route == "duplicate_execution":
                    s.call(
                        a.name, {**args, "idempotency_key": "different-independent-key"}
                    )
    if delayed:
        s.result_sent(delayed)
    s.call("done", {})
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    if dest := os.environ.get("COMPOSITE_EVIDENCE"):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        safe = reward.model_dump(mode="json")
        # Keep reward components and failures, omit reference credentials from the public proof.
        for check in safe.get("action_checks") or []:
            check["action"]["arguments"].pop("credential", None)
        (path / f"{task_id.replace(' ', '-')}-{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "route": route,
                    "mode": mode,
                    "expected": expected,
                    "reward": safe,
                    "errors": s.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    assert reward.reward == expected, reward.info


@pytest.mark.parametrize(
    "task_id,violation",
    [
        ("Bank 2", "legacy_grant"),
        ("Bank 2", "wrong_reservation"),
        ("Bank 40", "lawful_fee"),
        ("Bank 40", "stale169"),
        ("Bank 40", "stale_receipt"),
        ("Bank 41", "superseded"),
        ("Bank 41", "unverified_contact"),
        ("Bank 41", "wrong_period"),
    ],
)
def test_forbidden_attempt_remains_failure_after_correction(task_id, violation):
    task = next(t for t in get_tasks() if t.id == task_id)
    s = PlatformSession(task, get_environment, max_calls=150, max_errors=30)
    for a in task.evaluation_criteria.actions:
        args = deepcopy(a.arguments)
        if (
            task_id == "Bank 2"
            and a.action_id == "bank2:adjust"
            and violation == "legacy_grant"
        ):
            s.call("grant_cashback", {"transaction_id": "txn_552290", "amount": 20})
        if (
            task_id == "Bank 2"
            and a.name == "create_cashback_investigation"
            and violation == "wrong_reservation"
        ):
            wrong = deepcopy(args)
            wrong["reserved_adjustments"][0]["permission_id"] = (
                "adjustment-other-purchase"
            )
            s.call(a.name, wrong)
        if task_id == "Bank 40" and a.action_id == "refund_fee_Bank 40":
            wrong = deepcopy(args)
            if violation == "lawful_fee":
                wrong["transaction_id"] = "txn_088215"
            if violation == "stale169":
                wrong["amount"] = 169
            if violation == "stale_receipt":
                wrong["confirmation_id"] = "fee-refund-confirmation-1"
            s.call(a.name, wrong)
        if (
            task_id == "Bank 41"
            and a.action_id == "bank41:investigation"
            and violation == "wrong_period"
        ):
            s.call(a.name, {**args, "case_id": "case-n40-other-period"})
        if (
            task_id == "Bank 41"
            and a.action_id == "bank41:channel-confirm"
            and violation == "unverified_contact"
        ):
            delivery = next(
                x
                for x in task.evaluation_criteria.actions
                if x.action_id == "bank41:document-delivery"
            )
            s.call(delivery.name, delivery.arguments)
        if (
            task_id == "Bank 41"
            and a.action_id == "bank41:document-delivery"
            and violation == "superseded"
        ):
            s.call(
                a.name,
                {
                    **args,
                    "document_id": "case-n40-current:registration_confirmation:version:1",
                },
            )
        s.call(a.name, args)
    s.call("done", {})
    assert s.grade().reward == 0
    assert grade_full_session(s).reward_info.reward == 0


@pytest.mark.parametrize("task_id", ["Bank 2", "Bank 41"])
def test_business_equivalent_argument_alternative(task_id):
    task = next(t for t in get_tasks() if t.id == task_id)
    s = PlatformSession(task, get_environment, max_calls=150, max_errors=20)
    for a in task.evaluation_criteria.actions:
        args = deepcopy(a.arguments)
        if task_id == "Bank 2" and a.name == "create_cashback_investigation":
            args.pop("reserved_adjustments", None)
        if task_id == "Bank 41" and a.name == "amend_cashback_investigation":
            args["items"] = [
                {"transaction_id": "txn_077031", "amount": 70},
                {"transaction_id": "txn_077040", "amount": 108},
            ]
        result = s.call(a.name, args)
        assert not result.is_error, result.content[0].text
    s.call("done", {})
    assert s.grade().reward == 1
    assert grade_full_session(s).reward_info.reward == 1


def test_investigation_without_reservation_needs_received_adjustment_result():
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    s = PlatformSession(task, get_environment, max_calls=150, max_errors=20)
    pending = None
    for a in task.evaluation_criteria.actions:
        args = deepcopy(a.arguments)
        if a.action_id == "bank2:adjust":
            pending = s.begin_call(a.name, args, None)
            s.call(a.name, args, attempt=pending)
            continue
        if a.name == "create_cashback_investigation":
            args.pop("reserved_adjustments", None)
        s.call(a.name, args)
    s.result_sent(pending)
    s.call("done", {})
    assert s.grade().reward == 0
    assert grade_full_session(s).reward_info.reward == 0


@pytest.mark.parametrize("route", ["reordered_consent", "initial_versioned_subset"])
def test_fee_consent_is_a_set_and_initial_versions_do_not_suppress_settlement(route):
    task = next(t for t in get_tasks() if t.id == "Bank 40")
    s = PlatformSession(task, get_environment, max_calls=150, max_errors=20)
    for a in task.evaluation_criteria.actions:
        args = deepcopy(a.arguments)
        if a.action_id == "bank40:consent":
            args["request"]["refunds"] = list(reversed(args["request"]["refunds"]))
            if route == "initial_versioned_subset":
                args["request"]["refunds"] = [
                    {"transaction_id": "txn_088220", "amount": 129},
                    {"transaction_id": "txn_088219", "amount": 169},
                ]
                args["request"]["total_amount"] = 298
                versions = {}
                for tx in ["txn_088219", "txn_088220"]:
                    result = s.call("get_fee_refund_state", {"transaction_id": tx})
                    assert not result.is_error
                    versions[tx] = json.loads(result.content[0].text)["state_version"]
                args["request"]["state_versions"] = versions
        result = s.call(a.name, args)
        assert not result.is_error, result.content[0].text
    s.call("done", {})
    assert s.env.tools.db.accounts["acc_7734"].balance == 4080
    assert s.grade().reward == 1
    assert grade_full_session(s).reward_info.reward == 1


@pytest.fixture(autouse=True)
def historical_bank2_composite(request):
    """This module records composite-v1 behavior, not the new v2 scenario."""
    params = getattr(getattr(request.node, "callspec", None), "params", {})
    if params.get("task_id") == "Bank 2" or request.node.originalname in [
        "test_investigation_without_reservation_needs_received_adjustment_result"
    ]:
        request.getfixturevalue("bank2_composite_v1_scenarios")
