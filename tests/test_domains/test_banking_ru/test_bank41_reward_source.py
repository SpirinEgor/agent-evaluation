"""Bank41 exact operative N40 sources replace the redundant rewards policy."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["HALF", "FULL"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("reference", 1),
        ("atomic_only", 1),
        ("bundle_only", 1),
        ("overview_only", 0),
        ("partial_bundle", 0),
        ("unrelated", 0),
        ("noop", 0),
        ("early_private", 0),
        ("unauthorized_reward", 0),
    ],
)
def test_bank41_reward_source(mode, route, expected):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 41"))
    actions = deepcopy(task.evaluation_criteria.actions)
    if route != "reference":
        actions = [
            a
            for a in actions
            if not (
                a.name == "file_read"
                and a.arguments.get("path") == "/policies/fees_rewards.md"
            )
        ]
    if route in ["bundle_only", "overview_only", "partial_bundle", "unrelated"]:
        actions = [
            a
            for a in actions
            if not (
                a.name == "file_read"
                and a.arguments.get("path", "").startswith("/knowledge/kb_3")
            )
        ]
        if route != "overview_only":
            source = deepcopy(
                next(
                    a
                    for a in actions
                    if a.name == "file_read"
                    and a.arguments.get("path") == "/knowledge/kb_final_41.md"
                )
            )
            source.arguments = {
                "path": "/knowledge/kb_final_41_part_1.md"
                if route != "unrelated"
                else "/knowledge/kb_final_44.md"
            }
            if route == "partial_bundle":
                source.arguments.update(start_line=1, end_line=1)
            actions.insert(0, source)
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    if route == "early_private":
        session.call("get_accounts", {"customer_id": "egorova_n_3358"})
    if route != "noop":
        session.replay_actions(actions)
    if route == "unauthorized_reward":
        session.call("grant_cashback", {"transaction_id": "txn_077031", "amount": 130})
    session.call("done", {})
    reward = (
        session.grade() if mode == "HALF" else grade_full_session(session).reward_info
    )
    if dest := os.environ.get("BANK41_EVIDENCE"):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "route": route,
                "mode": mode,
                "expected": expected,
                "reward": reward.model_dump(mode="json"),
                "errors": session.errors,
                "calls": [
                    {
                        "call": c.model_dump(mode="json"),
                        "result": r.model_dump(mode="json"),
                    }
                    for c, r in session.log
                ],
            },
            ensure_ascii=False,
            indent=2,
        ).replace("мускат", "[MASKED]")
        (path / f"{route}-{mode}.json").write_text(payload)
    assert reward.final_reward == expected, reward.info


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "hash",
        "body",
        "incomplete",
        "error",
        "id",
        "role",
        "unreceived",
        "late",
        "early_failed_target",
    ],
)
def test_bank41_exact_alternative_receipt(mutation):
    """Source OR retains precise content/provenance and first-attempt bounds."""
    from tau2.evaluator.evaluator_action import _result_evidence_calls
    from tau2.evaluator.trace_evidence import evidence_from_messages

    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 41"))
    evidence = next(
        e
        for e in task.evaluation_criteria.result_evidence
        if e.evidence_id == "bank41:reward-policy"
    )
    # Consult-only has no dependent protected write; exercise the existing
    # first-attempt mechanism with a declared protected read boundary.
    evidence.before_action_id = "get_transactions_0_Bank 41"
    target = next(
        a
        for a in task.evaluation_criteria.actions
        if a.action_id == evidence.before_action_id
    )
    session = PlatformSession(task, get_environment)
    if mutation == "early_failed_target":
        session.call(target.name, target.arguments)
    session.call("file_read", {"path": "/knowledge/kb_320.md"})
    session.call(target.name, target.arguments)
    observed = evidence_from_messages(session.trajectory()).observed
    index = next(i for i, c in enumerate(observed) if c.call.name == "file_read")
    item = observed[index]
    result = item.result
    if mutation in ["hash", "body", "incomplete"]:
        payload = json.loads(result.content)
        key = {"hash": "sha256", "body": "content", "incomplete": "complete"}[mutation]
        payload[key] = False if mutation == "incomplete" else "wrong"
        result = result.model_copy(
            update={"content": json.dumps(payload, ensure_ascii=False)}
        )
    if mutation == "error":
        result = result.model_copy(update={"error": True})
    if mutation == "id":
        result = result.model_copy(update={"id": "wrong"})
    if mutation == "role":
        result = result.model_copy(update={"requestor": "user"})
    if mutation == "unreceived":
        result = None
    observed[index] = item.model_copy(
        update={
            "result": result,
            "result_position": 999 if mutation == "late" else item.result_position,
        }
    )
    assert bool(
        _result_evidence_calls(observed, task.evaluation_criteria.actions, evidence)
    ) is (mutation == "none")


def test_bank41_migration_scoped_and_idempotent():
    from scripts.banking_ru.build_bank41_reward_source_contract import with_contract

    tasks = json.loads((BANKING_RU_DATA_DIR / "tasks.json").read_text())
    original = deepcopy(tasks)
    selected = next(t for t in original if t["id"] == "Bank 41")
    next(
        e
        for e in selected["evaluation_criteria"]["result_evidence"]
        if e["evidence_id"] == "bank41:reward-policy"
    ).pop("alternatives", None)
    migrated = with_contract(original)
    assert migrated == with_contract(migrated)
    assert [t for t in original if t["id"] != "Bank 41"] == [
        t for t in migrated if t["id"] != "Bank 41"
    ]
    assert (
        selected["evaluation_criteria"]["tool_access_policy"]
        == next(t for t in migrated if t["id"] == "Bank 41")["evaluation_criteria"][
            "tool_access_policy"
        ]
    )


def test_bank41_generator_uses_selected_data_dir(tmp_path):
    import subprocess
    import sys

    from scripts.banking_ru.build_bank41_reward_source_contract import DATA

    corpus = tmp_path / "corpus/knowledge"
    corpus.mkdir(parents=True)
    (corpus / "kb_320.md").write_text("selected temporary corpus")
    tasks = json.loads((DATA / "tasks.json").read_text())
    (tmp_path / "tasks.json").write_text(json.dumps(tasks))
    subprocess.run(
        [
            sys.executable,
            "scripts/banking_ru/build_bank41_reward_source_contract.py",
            "--data-dir",
            str(tmp_path),
        ],
        check=True,
    )
    changed = json.loads((tmp_path / "tasks.json").read_text())
    selected = next(t for t in changed if t["id"] == "Bank 41")
    reward = next(
        e
        for e in selected["evaluation_criteria"]["result_evidence"]
        if e["evidence_id"] == "bank41:reward-policy"
    )
    assert (
        reward["alternatives"][0]["expected_fields"]["content"]
        == "selected temporary corpus"
    )


def test_bank41_existing_builder_remains_current():
    from scripts.banking_ru.build_sources18_repair_contract import with_contract

    tasks = json.loads((BANKING_RU_DATA_DIR / "tasks.json").read_text())
    assert with_contract(tasks) == tasks
