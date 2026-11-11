"""Offline witnesses for staged exchange, immutable registry and case handover."""

from copy import deepcopy

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.component_eligibility import registry_page
from tau2.domains.banking_ru.environment import get_environment
from tau2.environment.tool import ToolExecutionRejected
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.test_bank2_linked_goals import (
    data,
    invoke,
    task2,
)
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route",
    [
        "correct",
        "missing_archive_basis",
        "missing_withdrawal",
        "mandate_recovery",
        "reverse_document_order",
        "expired_recipient_recover",
        "stale_handover",
        "wrong_assignments_recover",
        "bounded_consent",
        "oversized_consent_recover",
        "no_handover",
        "no_explanation",
        "no_scope",
        "wrong_handover_recover",
        "duplicate_handover_recover",
        "wrong_amount_recover",
        "wrong_explanation_recover",
        "early_stage_recover",
        "scope_leak_recover",
        "safe_reordered_items",
        "empty_message_recover",
    ],
)
def test_v37_whole_grade(mode, route):
    session = PlatformSession(
        task2(), get_environment, max_errors=0, mcp_layout="directory"
    )
    actions = deepcopy(session.task.evaluation_criteria.actions)
    errors = []
    for action in actions:
        args = deepcopy(action.arguments)
        if (
            route == "missing_archive_basis"
            and action.action_id == "bank2:v37:instruction:archive_basis"
        ):
            continue
        if (
            route == "missing_withdrawal"
            and action.action_id == "bank2:v37:instruction:withdraw_paper"
        ):
            continue
        if (
            route == "mandate_recovery"
            and action.action_id == "bank2:v37:instruction:archive_basis"
        ):
            assert invoke(
                session,
                "process_case_instruction",
                {"case_id": "case_610903", "instruction_id": "representative_payee"},
            ).is_error
            data(
                invoke(
                    session,
                    "process_case_instruction",
                    {"case_id": "case_610903", "instruction_id": "applicant_close"},
                )
            )
            data(
                invoke(
                    session,
                    "process_case_instruction",
                    {
                        "case_id": "case_610903",
                        "instruction_id": "representative_status",
                    },
                )
            )
        if (
            route == "reverse_document_order"
            and action.name == "process_case_instruction"
        ):
            args["instruction_id"] = {
                "archive_basis": "current_basis",
                "current_basis": "archive_basis",
            }.get(args["instruction_id"], args["instruction_id"])
        if route == "stale_handover" and action.action_id == "bank2:v37:handover":
            continue
        request = args.get("request", {})
        if request.get("type") == "reward_resolution_confirmation" and not request.get(
            "resolution_operation_id"
        ):
            if route == "bounded_consent":
                request["payout_tolerance"] = 0.01
                request["payouts"][0]["amount"] = round(
                    request["payouts"][0]["amount"] - 0.01, 2
                )
                request["payouts"][1]["amount"] = round(
                    request["payouts"][1]["amount"] + 0.01, 2
                )
            elif route == "oversized_consent_recover":
                bad = deepcopy(args)
                bad["request"]["payout_tolerance"] = 1
                assert invoke(session, action.name, bad).is_error
        if (
            route == "expired_recipient_recover"
            and action.action_id == "bank2:v36:send"
        ):
            bad = deepcopy(args)
            bad["recipient_id"] = "merchant_legacy_desk"
            assert (
                data(invoke(session, action.name, bad))["exchange_result"]["status"]
                == "unmatched"
            )
        if route == "no_handover" and action.name == "record_case_handover":
            continue
        if route == "no_explanation" and action.action_id == "bank2:v37:explanation":
            continue
        if route == "no_scope" and action.action_id == "bank2:v37:exchange-scope":
            continue
        if action.name == "record_case_handover":
            if route == "wrong_assignments_recover":
                bad = deepcopy(args)
                bad["assignments"] = {}
                data(invoke(session, action.name, bad))
            if route == "wrong_handover_recover":
                bad = deepcopy(args)
                bad["items"] = [
                    x for x in bad["items"] if x["transaction_id"] != "txn_804205"
                ]
                data(invoke(session, action.name, bad))
            if route in {"duplicate_handover_recover", "wrong_amount_recover"}:
                bad = deepcopy(args)
                if route == "duplicate_handover_recover":
                    bad["items"].append(deepcopy(bad["items"][0]))
                else:
                    bad["items"][0]["amount"] += 0.01
                data(invoke(session, action.name, bad))
            if route == "safe_reordered_items":
                args["items"].reverse()
                args["source_ids"] = []
        if action.action_id == "bank2:v37:explanation":
            if route == "empty_message_recover":
                bad = deepcopy(args)
                bad["text"] = " "
                assert invoke(session, action.name, bad).is_error
            if route == "wrong_explanation_recover":
                bad = deepcopy(args)
                bad["case_items"][0]["amount"] += 1
                data(invoke(session, action.name, bad))
            if route == "safe_reordered_items":
                args["case_items"].reverse()
                args["text"] = (
                    "Состав оставшихся требований приведён в справке. Передаём на рассмотрение."
                )
        if action.action_id == "bank2:v36:send" and route == "early_stage_recover":
            later = next(
                a for a in actions if a.action_id == "bank2:v37:exchange-scope"
            )
            assert (
                data(invoke(session, later.name, later.arguments))["exchange_result"][
                    "status"
                ]
                == "unmatched"
            )
        if (
            action.action_id == "bank2:v37:exchange-scope"
            and route == "scope_leak_recover"
        ):
            bad = deepcopy(args)
            # The archive recipient has no right to the merchant purchase reference.
            bad["recipient_id"] = "external_user_reconciliation_02"
            bad["exchange_id"] = "exchange_610903_archive"
            bad["reference_fields"] = {"case_reference": "CF-2607-1188"}
            assert data(invoke(session, action.name, bad))["status"] == "sent"
        result = invoke(session, action.name, args)
        if result.is_error:
            errors.append(action.name)
    data(invoke(session, "done", {}))
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    if route in {
        "no_handover",
        "no_explanation",
        "no_scope",
        "stale_handover",
        "missing_archive_basis",
        "missing_withdrawal",
    }:
        if route in {"no_handover", "missing_archive_basis"}:
            assert "execute_reward_resolution" in errors
        else:
            assert not errors
        assert reward.reward_breakdown[RewardType.ACTION] == 0
    elif route in {"scope_leak_recover", "expired_recipient_recover"}:
        assert not errors
        assert reward.reward_breakdown[RewardType.DB] == 1
        assert reward.reward_breakdown[RewardType.ACTION] == 0
    elif route in {
        "duplicate_handover_recover",
        "wrong_amount_recover",
        "wrong_handover_recover",
        "wrong_assignments_recover",
        "wrong_explanation_recover",
        "mandate_recovery",
    }:
        assert not errors
        assert reward.reward_breakdown[RewardType.ACTION] == 0, reward
        if route != "mandate_recovery":
            assert reward.reward_breakdown[RewardType.DB] == 1, reward
        reason = (
            "case_instruction_mandate"
            if route == "mandate_recovery"
            else "case_output_assignments"
            if route == "wrong_assignments_recover"
            else "case_output_items"
        )
        assert reason in str(reward)
    else:
        assert not errors
        assert reward.final_reward == 1, reward


def test_registry_cursor_keeps_snapshot_and_scope():
    task = task2()
    session = PlatformSession(task, get_environment, max_errors=0)
    records = session.env.tools.db.partner_confirmations
    customer = "gromov_a_1187"
    snapshots = {}
    first = registry_page(
        records, customer, snapshots=snapshots, registry_name="partners"
    )
    original = registry_page(
        records,
        customer,
        first["next_cursor"],
        snapshots=snapshots,
        registry_name="partners",
    )
    changed_id = original["records"][0]["id"]
    records[changed_id].status = "rejected"
    old = registry_page(
        records,
        customer,
        first["next_cursor"],
        snapshots=snapshots,
        registry_name="partners",
    )
    assert old == original
    fresh = registry_page(
        records, customer, snapshots=snapshots, registry_name="partners"
    )
    assert fresh["snapshot_id"] != first["snapshot_id"]
    current = registry_page(
        records,
        customer,
        fresh["next_cursor"],
        snapshots=snapshots,
        registry_name="partners",
    )
    assert (
        next(r for r in current["records"] if r["id"] == changed_id)["status"]
        == "rejected"
    )
    old["records"].clear()
    assert (
        registry_page(
            records,
            customer,
            first["next_cursor"],
            snapshots=snapshots,
            registry_name="partners",
        )
        == original
    )
    for overrides in [
        {"customer_id": "foreign"},
        {"receipt_id": "different"},
        {"registry_name": "incidents"},
    ]:
        kwargs = dict(
            customer_id=customer, snapshots=snapshots, registry_name="partners"
        )
        kwargs.update(overrides)
        with pytest.raises(ToolExecutionRejected):
            registry_page(records, cursor=first["next_cursor"], **kwargs)


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "variant", ["accepted", "registered_only", "foreign_acceptance"]
)
def test_initial_statement_coverage(mode, variant):
    task = task2().model_copy(deep=True)
    registrations = task.initial_state.initialization_data.agent_data[
        "reward_statement_registrations"
    ]
    for record in registrations.values():
        if variant == "registered_only":
            record["status"] = "registered"
        elif variant == "foreign_acceptance":
            record["customer_id"] = "egorova_n_3358"
    session = PlatformSession(task, get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        invoke(session, action.name, deepcopy(action.arguments))
    invoke(session, "done", {})
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    if variant == "accepted":
        assert reward.final_reward == 1
    else:
        assert reward.final_reward == 0


def test_historical_binding_is_locked_without_mutation():
    session = PlatformSession(task2(), get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        data(invoke(session, action.name, action.arguments))
        if action.name == "verify_identity":
            break
    db = session.env.tools.db
    before = db.model_dump()
    repair = next(
        a
        for a in session.task.evaluation_criteria.actions
        if a.name == "repair_reward_binding"
    )
    args = deepcopy(repair.arguments)
    args["reprocessing_id"] = "reprocessing_610482_history"
    assert invoke(session, repair.name, args).is_error
    assert db.model_dump() == before


def test_independent_commitment_releases_only_canceled_source():
    from tau2.domains.banking_ru.reward_funding import reserve_snapshot

    session = PlatformSession(task2(), get_environment, max_errors=0)
    db = session.env.tools.db
    offer = db.reward_resolution_offers["offer_610735"]
    before = reserve_snapshot(db, offer)
    assert (before["held_bank"], before["held_partner"]) == (6, 2)
    unrelated = deepcopy(db.reward_funding_commitments["commitment_other_610903"])
    unrelated.update(id="unrelated", case_id="another_case", bank=99, partner=99)
    db.reward_funding_commitments["unrelated"] = unrelated
    assert reserve_snapshot(db, offer) == before
    for action in session.task.evaluation_criteria.actions:
        data(invoke(session, action.name, action.arguments))
        if action.name == "refresh_cashback_settlement":
            break
    after = reserve_snapshot(db, db.reward_resolution_offers[offer.id])
    assert (after["held_bank"], after["held_partner"]) == (0, 2)
    assert (after["remaining_bank"], after["remaining_partner"]) == (23, 20)
    assert not unrelated["released_bank"] and not unrelated["released_partner"]


def test_incoming_evidence_conflict_is_atomic():
    session = PlatformSession(task2(), get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        data(invoke(session, action.name, action.arguments))
        if action.action_id == "bank2:v37:instruction:archive_basis":
            break
    db = session.env.tools.db
    case = db.cases["case_610903"]
    doc = db.documents["doc_current_basis_610903"]
    doc.customer_facts["case_evidence"] = {
        "new_first_fact": {"value": "must_not_be_written", "revision": 1},
        "standard_revision": {"value": "conflict", "revision": 1},
    }
    before = case.model_dump()
    assert invoke(
        session,
        "process_case_instruction",
        {"case_id": case.id, "instruction_id": "current_basis"},
    ).is_error
    assert case.model_dump() == before
    doc.origin = "customer"
    doc.customer_facts["case_evidence"] = {
        "standard_revision": {"value": "posting-v2", "revision": 2}
    }
    assert invoke(
        session,
        "process_case_instruction",
        {"case_id": case.id, "instruction_id": "current_basis"},
    ).is_error
    assert case.model_dump() == before


@pytest.mark.parametrize("mode", ["half", "full"])
def test_equivalent_bank_evidence_is_not_exact_reference_path(mode):
    task = task2()
    initial = task.initial_state.initialization_data.agent_data
    case = initial["cases"]["case_610903"]
    replacement = deepcopy(initial["documents"]["doc_archive_basis_610903"])
    replacement["id"] = "equivalent_archive"
    replacement["customer_facts"]["case_evidence"]["standard_revision"]["revision"] = 3
    initial["documents"][replacement["id"]] = replacement
    case["incoming_instructions"]["equivalent_archive"] = {
        **deepcopy(case["incoming_instructions"]["archive_basis"]),
        "document_id": replacement["id"],
    }
    session = PlatformSession(task, get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        args = deepcopy(action.arguments)
        if action.action_id == "bank2:v37:instruction:archive_basis":
            args["instruction_id"] = "equivalent_archive"
        data(invoke(session, action.name, args))
    data(invoke(session, "done", {}))
    grade = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert grade.final_reward == 1, grade


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "valid_to,expected_action", [("2026-08-27", 0), ("2026-08-28", 1)]
)
def test_v38_mandate_expiry_is_judged_after_effect(mode, valid_to, expected_action):
    task = task2()
    initial = task.initial_state.initialization_data.agent_data
    initial["cases"]["case_610903"]["parties"]["owner_1187"]["valid_to"] = valid_to
    session = PlatformSession(task, get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        data(invoke(session, action.name, deepcopy(action.arguments)))
    data(invoke(session, "done", {}))
    reward = (
        session.grade() if mode == "half" else grade_full_session(session).reward_info
    )
    assert reward.reward_breakdown[RewardType.DB] == 1
    assert reward.reward_breakdown[RewardType.ACTION] == expected_action
    if not expected_action:
        assert "case_instruction_mandate" in str(reward)


def test_v38_unsigned_instruction_is_rejected_without_effect():
    session = PlatformSession(task2(), get_environment, max_errors=0)
    for action in session.task.evaluation_criteria.actions:
        data(invoke(session, action.name, action.arguments))
        if action.name == "verify_identity":
            break
    db = session.env.tools.db
    db.cases["case_610903"].incoming_instructions["withdraw_paper"][
        "signature_verified"
    ] = False
    before = db.model_dump()
    result = invoke(
        session,
        "process_case_instruction",
        {"case_id": "case_610903", "instruction_id": "withdraw_paper"},
    )
    assert result.is_error
    assert db.model_dump() == before
