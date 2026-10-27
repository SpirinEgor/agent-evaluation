"""Shared discoverable workflow: real tools, no model or host shell."""

import json
from pathlib import Path

import pytest


def test_client_action_is_a_separate_submission_not_bank_acceptance():
    from tau2.domains.banking_ru.client_requests import StructuredIdentityBankingTools
    from tau2.domains.banking_ru.data_model import BankingDB

    assert "reward_client_submissions" in BankingDB.model_fields
    assert hasattr(StructuredIdentityBankingTools, "register_reward_statement")


def test_discovery_api_is_shared_not_a_task_flag():
    from tau2.domains.banking_ru.file_pilot import FileBankingTools

    assert hasattr(FileBankingTools, "unlock_discoverable_agent_tool")
    assert hasattr(FileBankingTools, "call_discoverable_agent_tool")
    assert hasattr(FileBankingTools, "give_discoverable_user_tool")


def test_real_prompt_declares_call_budget():
    import importlib.util

    path = (
        Path(__file__).resolve().parents[3] / "scripts/banking_ru/run_codex_harness.py"
    )
    spec = importlib.util.spec_from_file_location("budget_harness", path)
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    assert "max_calls" in __import__("inspect").signature(h.build_prompt).parameters
    prompt = h.build_prompt(policy="ROLE", ticket="TICKET", max_calls=100, max_errors=5)
    assert "100" in prompt and "5" in prompt and "done" in prompt


@pytest.fixture
def workflow_tools(tmp_path):
    import shutil

    from tau2.domains.banking_ru.client_requests import StructuredIdentityBankingTools
    from tau2.domains.banking_ru.data_model import BankingDB
    from tau2.domains.banking_ru.file_corpus import FileCorpus

    FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/banking_ru"

    parent = Path(shutil.copytree(FIXTURES / "reward_api", tmp_path / "workflow"))
    corpus = FileCorpus(parent)
    corpus.manifest["discoverable_workflows"] = True
    db = BankingDB.model_validate_json((parent / "db.json").read_text())
    db.session.verified_customers.append("gromov_a_1187")
    db.transactions["txn_804227"].reward_discrepancy_type = "partner_activation_review"
    return StructuredIdentityBankingTools(db, corpus)


def test_statement_submission_registration_acceptance_are_distinct(workflow_tools):
    t = workflow_tools
    c = "gromov_a_1187"
    case = "case_610903"
    b = "txn_804227"
    t.give_discoverable_user_tool("submit_reward_statement")
    submitted = t.perform_customer_action(
        c, "submit_reward_statement", dict(case_id=case, transaction_ids=[b])
    )
    assert (
        submitted["status"] == "submitted" and not t.db.reward_statement_registrations
    )
    registered = t.register_reward_statement(c, submitted["id"], case).root
    assert registered["status"] == "registered"
    with pytest.raises(ValueError, match="NOT_ACCEPTED"):
        t.refresh_cashback_settlement(c, "2026-07")
    assert (
        t.get_reward_statement_status(c, registered["id"]).root["status"] == "accepted"
    )
    assert t.refresh_cashback_settlement(c, "2026-07").root["status"] == "settled"


def test_unnecessary_statement_is_forbidden_even_after_recovery(workflow_tools):
    t = workflow_tools
    c = "gromov_a_1187"
    t.give_discoverable_user_tool("submit_reward_statement")
    with pytest.raises(ValueError, match="UNNECESSARY"):
        t.perform_customer_action(
            c,
            "submit_reward_statement",
            dict(case_id="case_610903", transaction_ids=["txn_804219"]),
        )
    row = t.perform_customer_action(
        c,
        "submit_reward_statement",
        dict(case_id="case_610903", transaction_ids=["txn_804227"]),
    )
    reg = t.register_reward_statement(c, row["id"], "case_610903").root
    t.get_reward_statement_status(c, reg["id"])
    assert t.assert_reward_statements(c) is False


def make_session(t, max_calls=100):
    from tau2.data_model.tasks import Task
    from tau2.environment.environment import Environment
    from tau2.platform.session import PlatformSession

    task = Task(
        id="workflow-api",
        user_scenario={"instructions": "Shared workflow protocol probe"},
    )
    return PlatformSession(
        task,
        lambda **kw: Environment(
            domain_name="banking_ru", policy="ROLE", tools=t, solo_mode=True
        ),
        max_calls=max_calls,
    )


def test_wrapper_keeps_original_mcp_and_canonical_bank_trace(workflow_tools):
    t = workflow_tools
    s = make_session(t)
    assert "get_reward_resolution_offers" not in {x.name for x in s.mcp_tools}
    assert not s.call(
        "unlock_discoverable_agent_tool", dict(name="get_reward_resolution_offers")
    ).is_error
    assert not s.call(
        "call_discoverable_agent_tool",
        dict(
            name="get_reward_resolution_offers",
            arguments=dict(customer_id="gromov_a_1187", case_id="case_610903"),
        ),
    ).is_error
    calls = [c for m in s._messages for c in getattr(m, "tool_calls", []) or []]
    assert calls[-1].name == "get_reward_resolution_offers"
    assert s.mcp_events[-1]["original_name"] == "call_discoverable_agent_tool"
    assert s.mcp_events[-1]["canonical_name"] == "get_reward_resolution_offers"
    assert s.call(
        "get_reward_resolution_offers",
        dict(customer_id="gromov_a_1187", case_id="case_610903"),
    ).is_error


def test_budget_feedback_is_separate_from_bank_fact(workflow_tools):
    s = make_session(workflow_tools, max_calls=3)
    r = s.call("get_bank_date", {})
    assert json.loads(r.content[0].text)["date"] == "2026-08-28"
    assert len(r.content) == 2 and "2" in r.content[1].text


def test_corpus_commands_never_read_the_host(workflow_tools):
    t = workflow_tools
    with pytest.raises(ValueError):
        t.corpus_command("cat /etc/passwd")
    with pytest.raises(ValueError):
        t.corpus_command("sh -c id")
    result = t.corpus_command("grep -E 'partner_activation' /knowledge").root
    assert "documents" in result


def test_new_owner_selectors_have_shared_access_contract():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))
    from build_access_contract import protected_arguments

    mapping = protected_arguments()
    assert mapping.get("register_reward_statement") == {
        "customer_id": "customers",
        "case_id": "cases",
        "submission_id": "reward_client_submissions",
    }
    assert (
        mapping["ask_client"]["request.arguments.transaction_ids.*"] == "transactions"
    )


def test_statement_cannot_be_attached_to_another_period_case(workflow_tools):
    t = workflow_tools
    t.give_discoverable_user_tool("submit_reward_statement")
    with pytest.raises(ValueError, match="CASE_SCOPE"):
        t.perform_customer_action(
            "gromov_a_1187",
            "submit_reward_statement",
            dict(case_id="case-standard-other-period", transaction_ids=["txn_804227"]),
        )


def test_opened_operation_keeps_native_serialization(workflow_tools):
    from tau2.environment.environment import Environment

    t = workflow_tools
    t.unlock_discoverable_agent_tool("get_reward_resolution_components")
    args = dict(offer_id="offer_670261", offset=0, limit=1)
    assert Environment.to_json_str(
        t.call_discoverable_agent_tool("get_reward_resolution_components", args)
    ) == Environment.to_json_str(t.get_reward_resolution_components(**args))


def test_prepared_wrapper_requests_keep_actual_execution_order(workflow_tools):
    t = workflow_tools
    s = make_session(t)
    t.unlock_discoverable_agent_tool("get_reward_resolution_components")
    a = dict(
        name="get_reward_resolution_components",
        arguments=dict(offer_id="offer_650221", offset=0, limit=1),
    )
    b = dict(
        name="get_reward_resolution_components",
        arguments=dict(offer_id="offer_670261", offset=0, limit=1),
    )
    first = s.begin_call("call_discoverable_agent_tool", a, None)
    second = s.begin_call("call_discoverable_agent_tool", b, None)
    s.call("call_discoverable_agent_tool", b, attempt=second)
    s.result_sent(second)
    s.call("call_discoverable_agent_tool", a, attempt=first)
    s.result_sent(first)
    calls = [c for m in s._messages for c in getattr(m, "tool_calls", []) or []]
    assert [c.execution_order for c in calls] == [1, 0]


def test_wrapper_trace_is_valid_even_with_direct_catalog(workflow_tools):
    t = workflow_tools
    t.corpus.manifest["discoverable_workflows"] = False
    s = make_session(t)
    t.unlock_discoverable_agent_tool("get_reward_resolution_offers")
    assert not s.call(
        "call_discoverable_agent_tool",
        dict(
            name="get_reward_resolution_offers",
            arguments=dict(customer_id="gromov_a_1187", case_id="case_610903"),
        ),
    ).is_error
    calls = [c for m in s._messages for c in getattr(m, "tool_calls", []) or []]
    assert calls[-1].name == "get_reward_resolution_offers"


def test_rejected_outer_arguments_remain_in_audit_trace(workflow_tools):
    s = make_session(workflow_tools)
    args = dict(
        name="get_reward_resolution_offers",
        arguments=dict(customer_id="gromov_a_1187", case_id="case_610903"),
        outer_note="audit-visible-marker",
    )
    assert s.call("call_discoverable_agent_tool", args).is_error
    calls = [c for m in s._messages for c in getattr(m, "tool_calls", []) or []]
    assert calls[-1].arguments.get("outer_note") == "audit-visible-marker"


def test_discovery_obeys_declared_numeric_types(workflow_tools):
    t = workflow_tools
    t.unlock_discoverable_agent_tool("get_reward_resolution_components")
    with pytest.raises(ValueError):
        t.call_discoverable_agent_tool(
            "get_reward_resolution_components",
            dict(offer_id="offer_670261", offset="0", limit=1),
        )


def test_locked_write_is_an_attempt_but_cannot_mutate_on_replay(workflow_tools):
    s = make_session(workflow_tools)
    args = dict(
        name="execute_reward_resolution",
        arguments=dict(offer_id="offer_670261", case_id="case_610903"),
    )
    assert s.call("call_discoverable_agent_tool", args).is_error
    messages = [m for m in s._messages if getattr(m, "role", None) == "tool"]
    calls = [c for m in s._messages for c in getattr(m, "tool_calls", []) or []]
    assert calls[-1].name == "execute_reward_resolution"
    assert messages[-1].execution_rejected is True
