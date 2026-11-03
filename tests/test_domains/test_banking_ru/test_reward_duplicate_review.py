"""Scope, evidence versions and atomic recovery of duplicate-payment restrictions."""

from copy import deepcopy

import pytest

from tau2.domains.banking_ru.cashback_state import revision
from tau2.domains.banking_ru.environment import get_environment
from tau2.domains.banking_ru.reward_duplicate_review import (
    public_reviews,
    require_unrestricted,
    resolve,
)
from tau2.environment.tool import ToolExecutionRejected
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.test_bank2_linked_goals import (
    OP,
    C,
    data,
    task2,
)
from tests.utils.banking_full_grade import grade_full_session

REVIEW = "review_610903"
BASIS = "decision_mixed_01"


def pending():
    session = PlatformSession(task2(), get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        if action.name == "resolve_reward_duplicate_review":
            break
        data(session.call(action.name, deepcopy(action.arguments)))
    return session


def test_pending_scope_and_no_effect_rejection():
    session = pending()
    db = session.env.tools.db
    assert db.reward_duplicate_reviews[REVIEW].status == "pending"
    require_unrestricted(db, C, ["txn_804219", "txn_804205"])
    with pytest.raises(ToolExecutionRejected, match="REVIEW_PENDING"):
        require_unrestricted(db, C, ["txn_804227"])
    # Prepare a valid updated intent first, to exercise the restriction rather than
    # the pre-existing overpayment check. Client consent does not lift this hold.
    actions = session.task.evaluation_criteria.actions
    for name in ("ask_client", "amend_reward_resolution"):
        action = next(
            a
            for a in actions
            if a.action_id
            == (
                "bank2:v17:consent-amendment"
                if name == "ask_client"
                else "bank2:v17:amend-resolution"
            )
        )
        args = deepcopy(action.arguments)
        if name == "ask_client":
            args["request"]["state_revision"] = revision(db, C, "2026-07")
        else:
            args["expected_revision"] = revision(db, C, "2026-07")
        data(session.call(name, args))
    before = db.model_dump()
    assert session.call(
        "retry_reward_resolution", {"resolution_operation_id": OP}
    ).is_error
    assert db.model_dump() == before


@pytest.mark.parametrize(
    "defect",
    ["old_review", "old_basis", "other_reference", "other_period", "unknown_basis"],
)
def test_invalid_evidence_cannot_release_or_change_state(defect):
    session = pending()
    tools = session.env.tools
    args = dict(review_id=REVIEW, basis_id=BASIS, expected_revision=2, basis_revision=1)
    if defect == "old_review":
        args["expected_revision"] = 1
    elif defect == "old_basis":
        tools.db.payment_bases[BASIS].revision = 2
    elif defect == "other_reference":
        tools.db.payment_bases[BASIS].external_reference = "different"
    elif defect == "other_period":
        tools.db.payment_bases[BASIS].period = "2026-06"
    else:
        args["basis_id"] = "unknown"
    before = tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected):
        resolve(tools, **args)
    assert tools.db.model_dump() == before


def test_repeat_is_idempotent_and_changed_basis_reopens_authority():
    session = pending()
    tools = session.env.tools
    answer = resolve(tools, REVIEW, BASIS, 2, 1)
    before = tools.db.model_dump()
    assert resolve(tools, REVIEW, BASIS, 2, 1) == answer
    assert tools.db.model_dump() == before
    tools.db.payment_bases[BASIS].revision = 2
    assert public_reviews(tools.db, C, "case_610903")[0]["status"] == "pending"
    with pytest.raises(ToolExecutionRejected):
        require_unrestricted(tools.db, C, ["txn_804227"])
    with pytest.raises(ToolExecutionRejected):
        resolve(tools, REVIEW, BASIS, 2, 1)
    result = resolve(tools, REVIEW, BASIS, 3, 2)
    assert result["status"] == "cleared"
    assert result["decision_basis_revision"] == 2


def test_real_duplicate_does_not_get_automatic_clearance():
    session = pending()
    tools = session.env.tools
    line = tools.db.payment_bases[BASIS].lines[0]
    line.purchase_id = "txn_804227"
    line.reward_program = "partner_cashback"
    assert resolve(tools, REVIEW, BASIS, 2, 1)["status"] == "duplicate_confirmed"
    with pytest.raises(ToolExecutionRejected):
        require_unrestricted(tools.db, C, ["txn_804227"])


@pytest.mark.parametrize(
    "route",
    ["no_extra_read", "safe_recovery", "read_basis_late", "duplicate_resolution"],
)
def test_allowed_paths_through_whole_grader(route):
    task = task2()
    session = PlatformSession(task, get_environment, max_errors=0)
    basis_action = next(
        a for a in task.evaluation_criteria.actions if a.name == "get_payment_basis"
    )
    for action in task.evaluation_criteria.actions:
        if action.name == "get_reward_duplicate_reviews" and route == "no_extra_read":
            continue  # The execution response already supplied the published review.
        if action.name == "resolve_reward_duplicate_review":
            if route == "safe_recovery":
                before = session.env.tools.db.model_dump()
                assert session.call(
                    action.name, {**action.arguments, "expected_revision": 1}
                ).is_error
                assert session.call(
                    "retry_reward_resolution", {"resolution_operation_id": OP}
                ).is_error
                assert session.env.tools.db.model_dump() == before
            if route == "read_basis_late":
                data(session.call(basis_action.name, basis_action.arguments))
        data(session.call(action.name, deepcopy(action.arguments)))
        if (
            action.name == "resolve_reward_duplicate_review"
            and route == "duplicate_resolution"
        ):
            before = session.env.tools.db.model_dump()
            data(session.call(action.name, deepcopy(action.arguments)))
            assert session.env.tools.db.model_dump() == before
    session.call("done", {})
    result = grade_full_session(session).reward_info
    assert result.reward == 1, result.info


def test_asynchronous_submission_is_blocked_before_reserving_money():
    from tau2.domains.banking_ru.reward_duplicate_review import RewardDuplicateReview
    from tests.test_domains.test_banking_ru.test_resolution_unknown import (
        ready,
        shared_pay,
    )

    env = ready("succeeded")
    offer = env.tools.db.reward_resolution_offers["X"]
    env.tools.db.reward_duplicate_reviews["restriction"] = RewardDuplicateReview(
        id="restriction",
        customer_id=offer.customer_id,
        case_id=offer.case_id,
        period=offer.period,
        trigger_offer_id="other",
        transaction_id=offer.transaction_ids[0],
        reward_program="standard_cashback",
        external_reference="ref",
        source_document_id="doc",
        import_document_id="import",
        status="pending",
    )
    before = env.tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected, match="REVIEW_PENDING"):
        shared_pay(env, "X")
    assert env.tools.db.model_dump() == before


def test_decision_after_amendment_requires_fresh_consent():
    session = pending()
    tools = session.env.tools
    consent_action = next(
        a
        for a in session.task.evaluation_criteria.actions
        if a.action_id == "bank2:v17:consent-amendment"
    )
    amend_action = next(
        a
        for a in session.task.evaluation_criteria.actions
        if a.name == "amend_reward_resolution"
    )
    consent = deepcopy(consent_action.arguments)
    consent["request"]["state_revision"] = revision(tools.db, C, "2026-07")
    data(session.call("ask_client", consent))
    amend = deepcopy(amend_action.arguments)
    amend["expected_revision"] = consent["request"]["state_revision"]
    data(session.call("amend_reward_resolution", amend))
    data(
        session.call(
            "resolve_reward_duplicate_review",
            dict(
                review_id=REVIEW, basis_id=BASIS, expected_revision=2, basis_revision=1
            ),
        )
    )
    before = tools.db.model_dump()
    rejected = session.call("retry_reward_resolution", {"resolution_operation_id": OP})
    assert rejected.is_error and "CONSENT_STALE" in rejected.content[0].text
    assert tools.db.model_dump() == before
    consent["request"]["state_revision"] = revision(tools.db, C, "2026-07")
    response = data(session.call("ask_client", consent))
    amend.update(
        expected_revision=consent["request"]["state_revision"],
        confirmation_id=response["confirmation_id"],
    )
    data(session.call("amend_reward_resolution", amend))
    assert (
        data(session.call("retry_reward_resolution", {"resolution_operation_id": OP}))[
            "total_amount"
        ]
        == 35.9
    )


def test_cross_customer_and_unpublished_decisions_are_atomic():
    session = pending()
    tools = session.env.tools
    review = tools.db.reward_duplicate_reviews[REVIEW]
    review.status = "queued"
    before = tools.db.model_dump()
    with pytest.raises(ToolExecutionRejected):
        resolve(tools, REVIEW, BASIS, 2, 1)
    assert tools.db.model_dump() == before
    review.status = "pending"
    tools.db.payment_bases[BASIS].customer_id = "egorova_n_3358"
    before = tools.db.model_dump()
    with pytest.raises(ValueError):
        resolve(tools, REVIEW, BASIS, 2, 1)
    assert tools.db.model_dump() == before
