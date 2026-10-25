"""Source dependencies follow selected argument values and first attempts."""

import json

import pytest

from tau2.data_model.tasks import Action, ResultEvidence
from tau2.evaluator.evaluator_action import _result_evidence_failures
from tests.test_evaluator_access_contract import observed, pair


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "variant,want_failure",
    [
        ("unselected", False),
        ("received", False),
        ("missing", True),
        ("late", True),
        ("first_error", True),
        ("same_batch", True),
    ],
)
def test_selection_scoped_receipt(mode, variant, want_failure):
    action = Action(action_id="pay", name="pay", arguments={}, compare_args=[])
    source = ResultEvidence(
        evidence_id="selected-x",
        tool_names=["read"],
        record_id="x",
        expected_fields={"amount": 4},
        before_action_id="pay",
        only_if_target_attempted=True,
        target_argument_options={"component_ids.*": ["x"]},
    )
    read, result = pair(
        "read", {}, id="read", content=json.dumps({"id": "x", "amount": 4})
    )
    pay, paid = pair(
        "pay", {"component_ids": ["y" if variant == "unselected" else "x"]}, id="pay"
    )
    batches = []
    if variant in ["received", "first_error"]:
        batches.append(([read], [result]))
    if variant == "same_batch":
        batches.append(([read, pay], [result, paid]))
    else:
        batches.append(([pay], [paid]))
    if variant == "late":
        batches.append(([read], [result]))
    if variant == "first_error":
        early, error = pair("pay", {"component_ids": ["x"]}, id="early", error=True)
        batches.insert(0, ([early], [error]))
    assert (
        bool(_result_evidence_failures(observed(batches, mode), [action], [source]))
        == want_failure
    )


@pytest.mark.parametrize("mode", ["half", "full"])
def test_local_component_key_is_scoped_to_its_offer(mode):
    action = Action(action_id="pay", name="pay", arguments={}, compare_args=[])
    source = ResultEvidence(
        evidence_id="offer1-x",
        tool_names=["read"],
        record_id="x",
        expected_fields={"amount": 4},
        before_action_id="pay",
        only_if_target_attempted=True,
        target_argument_options={"offer_id": ["offer1"], "component_ids.*": ["x"]},
    )
    pay, paid = pair("pay", {"offer_id": "offer2", "component_ids": ["x"]}, id="pay")
    assert (
        _result_evidence_failures(observed([([pay], [paid])], mode), [action], [source])
        == []
    )
