"""Run the maintained offline banking groups, without markers or deselection."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BANK = Path("tests/test_domains/test_banking_ru")
BASELINE = str(BANK / "test_whole_full_catalog_cases.py")
TOOLS = str(BANK / "test_tool_contract_catalog.py")
CORPUS = tuple(
    str(BANK / name)
    for name in (
        "test_db_banking_ru.py",
        "test_file_corpus_whole.py",
        "test_file_read_full_api.py",
        "test_release_package.py",
    )
)
GROUPS = {
    "tasks": (BASELINE,),
    "tools": (TOOLS,),
    "evaluator": (
        "tests/test_evaluator_action_contract.py",
        "tests/test_file_read_action_matching.py",
        "tests/test_trace_evidence.py",
        "tests/test_results_format.py",
        "tests/test_evaluator_access_contract.py",
        "tests/test_evaluator_raw_confidentiality.py",
        "tests/test_evaluator_business_process.py",
        "tests/test_db_action_contract_engine.py",
        "tests/test_db_state_checks.py",
        "tests/test_reward_business_trace.py",
        "tests/test_banking_business_trace.py",
    ),
    "infra": (
        *(
            "tests/test_platform/" + name
            for name in (
                "test_bank32_solution_isolation.py",
                "test_banking_preflight.py",
                "test_banking_startup_discovery.py",
                "test_client.py",
                "test_codex_container.py",
                "test_codex_broker.py",
                "test_codex_harness.py",
                "test_main.py",
                "test_langfuse_export.py",
                "test_mcp_bridge.py",
                "test_mcp_systems.py",
                "test_bank_directory.py",
                "test_directory_session.py",
                "test_knowledge_mcp.py",
                "test_service.py",
                "test_session.py",
            )
        ),
        "tests/test_banking_repo_check.py",
        *CORPUS,
    ),
}
# Frozen maintained inventory. Removed experiments remain in Git history;
# adding a file does not silently expand the basic test contract.
# See tests/fixtures/banking_ru/README.md for the boundary and retained guarantees.
GROUPS["regressions"] = tuple(
    str(BANK / name)
    for name in (
        "test_case_document_service.py",
        "test_cashback_investigation_tools.py",
        "test_cashback_review.py",
        "test_cashback_settlement_state.py",
        "test_discovery_workflow.py",
        "test_document_revision_delivery.py",
        "test_reward_workflow_protocols.py",
        "test_structured_operation_requests.py",
        "test_structured_escalation.py",
        "test_optional_operation_questions.py",
        "test_file_read_repair_contract.py",
        "test_fixture_integrity.py",
        "test_standalone_fixture.py",
        "test_bank2_adjustment_authority.py",
        "test_settlement_api.py",
        "test_bank40_source_equivalence.py",
        "test_resolution_compatibility.py",
        "test_component_eligibility.py",
        "test_resolution_component_pages.py",
        "test_resolution_deductions.py",
        "test_resolution_dependencies.py",
        "test_resolution_funding_pools.py",
        "test_resolution_unknown.py",
        "test_resolution_preferences.py",
        "test_resolution_percentage_deductions.py",
        "test_resolution_selection.py",
        "test_multisource_runtime.py",
        "test_contract_infrastructure.py",
        "test_banking_db_action_migration.py",
        "test_bank2_linked_goals.py",
        "test_banking_db_action_state_guards.py",
    )
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("all", *GROUPS), default="all")
    parser.add_argument(
        "--task",
        type=int,
        choices=range(1, 62),
        metavar="1..61",
        help="only the two exact reference/noop node IDs",
    )
    args, pytest_args = parser.parse_known_args()
    if pytest_args[:1] == ["--"]:
        pytest_args.pop(0)
    if args.task is not None:
        if args.group != "all":
            parser.error("--task cannot be combined with --group")
        nodes = [
            f"{BASELINE}::test_each_task_original_whole_full_baseline[{route}-Bank {args.task}]"
            for route in ("reference", "noop")
        ]
    else:
        nodes = (
            list(GROUPS[args.group])
            if args.group != "all"
            else [node for group in GROUPS.values() for node in group]
        )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        *nodes,
        *pytest_args,
    ]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
