"""Targeted regressions for shared access, reference IDs and period guards."""

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
from tau2.platform.session import PlatformSession

ROOT = Path(__file__).resolve().parents[3]


def access_generator():
    directory = ROOT / "scripts/banking_ru"
    sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location(
        "access_generator_regression", directory / "build_access_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shipped_access_contracts_include_every_generated_selector():
    generator = access_generator()
    tasks = json.loads((BANKING_RU_DATA_DIR / "tasks.json").read_text())
    db = json.loads((BANKING_RU_DATA_DIR / "db.json").read_text())
    assert generator.with_contract(deepcopy(tasks), db) == tasks


@pytest.mark.parametrize("extra_case", [False, True])
def test_bank4_reference_binds_created_case_and_whole_grade(extra_case):
    task = next(t for t in get_tasks() if t.id == "Bank 4")
    from tau2.data_model.tasks import InitializationData, InitialState

    if extra_case:
        existing = next(iter(get_environment().tools.db.cases.values())).model_dump(
            mode="json"
        )
        existing["id"] = "case_9000000"
        task.initial_state = InitialState(
            initialization_data=InitializationData(
                agent_data={"cases": {"case_9000000": existing}}
            )
        )
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)
    session.replay_actions(task.evaluation_criteria.actions)
    assert not session.errors
    created = next(
        c
        for c in session.env.tools.db.cases.values()
        if c.customer_id == "volkov_a_5108" and c.category == "card_loss"
    )
    assert created.status == "notified"
    session.call("done", {})
    assert session.grade().reward == 1.0
    noop = PlatformSession(task, get_environment)
    noop.call("done", {})
    assert noop.grade().reward == 0.0


def test_refresh_ignores_an_unaccepted_discrepancy_in_another_period():
    env = get_environment()
    tools = env.tools
    customer = "gromov_a_1187"
    tools.db.session.verified_customers.append(customer)
    tx = next(
        t
        for t in tools.db.transactions.values()
        if t.customer_id == customer and t.kind == "purchase"
    )
    tx.reward_discrepancy_type = "partner_activation_review"
    tx.date = "2025-01-15"
    tx.posted_at = "2025-01-15"
    # A period without a configured posting cycle uses the shared date semantics.
    assert (
        tools.refresh_cashback_settlement(customer, "2025-02").root["status"]
        == "settled"
    )
    with pytest.raises(ValueError, match="ERR_STATEMENT_NOT_ACCEPTED"):
        tools.refresh_cashback_settlement(customer, "2025-01")
    tools.db.reward_statement_registrations["accepted"] = dict(
        id="accepted",
        customer_id=customer,
        transaction_ids=[tx.id],
        status="accepted",
        case_id="case",
    )
    assert (
        tools.refresh_cashback_settlement(customer, "2025-01").root["status"]
        == "settled"
    )


def test_access_completeness_rejects_a_missing_protected_tool_mapping():
    generator = access_generator()
    mappings = generator.protected_arguments()
    del mappings["get_reward_resolution_components"]
    with pytest.raises(ValueError, match="get_reward_resolution_components"):
        generator.validate_protected_arguments(mappings)


def test_access_generator_patch_applies_to_temporary_task_only(
    tmp_path, monkeypatch, capsys
):
    import subprocess

    generator = access_generator()
    tasks = json.loads((BANKING_RU_DATA_DIR / "tasks.json").read_text())[:2]
    tasks[1]["evaluation_criteria"]["tool_access_policy"]["tool_arguments"] = {}
    untouched = deepcopy(tasks[0])
    task_file = tmp_path / "tasks.json"
    task_file.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
    (tmp_path / "db.json").write_bytes((BANKING_RU_DATA_DIR / "db.json").read_bytes())
    monkeypatch.setattr(generator, "TASK_FILE", task_file)
    monkeypatch.setattr(generator, "DATA", tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["build_access_contract.py", "--task-id", tasks[1]["id"]]
    )
    assert generator.main() == 0
    patch = capsys.readouterr().out
    assert f'@@     "id": "{tasks[1]["id"]}",' in patch
    applied = subprocess.run(
        ["apply_patch"], input=patch, text=True, capture_output=True
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    patched = json.loads(task_file.read_text())
    assert patched[0] == untouched
    assert (
        patched[1]["evaluation_criteria"]["tool_access_policy"]["tool_arguments"]
        == generator.protected_arguments()
    )


@pytest.mark.parametrize(
    "variant,expected",
    [("clean", 1.0), ("foreign", 0.0), ("unknown", 1.0), ("foreign_nested", 0.0)],
)
def test_generated_access_policy_controls_whole_grade_after_recovery(variant, expected):
    from tests.utils.banking_full_grade import grade_full_session

    task = next(t for t in get_tasks() if t.id == "Bank 4")
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)
    session.replay_actions(task.evaluation_criteria.actions)
    if variant in {"foreign", "unknown"}:
        foreign_offer = next(iter(session.env.tools.db.reward_resolution_offers))
        result = session.call(
            "get_reward_resolution_components",
            {
                "offer_id": foreign_offer
                if variant == "foreign"
                else "offer-does-not-exist"
            },
        )
        assert result.is_error
    elif variant == "foreign_nested":
        result = session.call(
            "ask_client",
            {
                "customer_id": "volkov_a_5108",
                "request": {
                    "type": "client_action",
                    "tool_name": "submit_reward_statement",
                    "arguments": {
                        "case_id": "case_610903",
                        "transaction_ids": ["txn_552284"],
                    },
                },
            },
        )
        assert result.is_error
    session.call("done", {})
    assert session.grade().reward == expected
    assert grade_full_session(session).reward_info.reward == expected


@pytest.mark.parametrize("invalid_source", ["missing", "ambiguous"])
def test_invalid_declared_reference_binding_cannot_produce_partial_gold_hash(
    invalid_source,
):
    from tau2.evaluator.evaluator_env import _reference_db_hashes

    task = next(t for t in get_tasks() if t.id == "Bank 4")
    binding = next(
        b
        for b in task.evaluation_criteria.result_argument_bindings
        if b.argument_name == "case_id"
    )
    if invalid_source == "missing":
        binding.result_path = ["missing_case_id"]
    else:
        binding.result_path = None
        binding.result_pattern = r"(?P<value>card_2201|volkov_a_5108)"
    with pytest.raises(ValueError, match="Reference binding"):
        _reference_db_hashes(
            get_environment,
            task,
            None,
            None,
            [],
            {"task_id": task.id, "solo_mode": True},
            True,
        )


@pytest.mark.parametrize("shape", ["list", "nested_tuple", "nested_dict"])
def test_reference_regex_binding_serializes_nested_typed_tool_results(shape):
    from pydantic import BaseModel

    from tau2.data_model.tasks import Action, ResultArgumentBinding
    from tau2.environment.environment import Environment
    from tau2.environment.reference_bindings import reference_arguments

    class DocumentReceipt(BaseModel):
        id: str

    receipt = DocumentReceipt(id="document-created")
    result = {
        "list": [receipt],
        "nested_tuple": {"items": (receipt,)},
        "nested_dict": [{"document": receipt}],
    }[shape]
    binding = ResultArgumentBinding(
        binding_id="received-document",
        source_action_id="source",
        target_action_id="target",
        argument_name="document_id",
        result_pattern=r'"id": "(?P<value>document-created)"',
    )
    action = Action(
        action_id="target",
        name="deliver_case_document",
        arguments={"document_id": "stale"},
    )
    # Both replay paths must see the same wire representation as the actual tool.
    expected = reference_arguments(
        action, action.arguments, [binding], {"source": Environment.to_json_str(result)}
    )
    assert (
        reference_arguments(action, action.arguments, [binding], {"source": result})
        == expected
        == {"document_id": "document-created"}
    )
