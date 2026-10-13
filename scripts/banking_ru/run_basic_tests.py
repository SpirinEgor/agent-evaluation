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
        "test_bounded_corpus_build.py",
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
    ),
    "infra": ("tests/test_platform", *CORPUS),
}
# Resolve inventory relative to the repo even when invoked from another cwd.
GROUPS["regressions"] = tuple(
    str(path.relative_to(ROOT))
    for path in sorted((ROOT / BANK).glob("test_*.py"))
    if str(path.relative_to(ROOT)) not in {BASELINE, TOOLS, *CORPUS}
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
