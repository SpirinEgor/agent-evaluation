"""Pipeline gates must never turn broken validation or isolation into model difficulty."""

import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "banking_preflight", ROOT / "scripts/banking_ru/banking_preflight.py"
)


def module():
    value = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(value)
    return value


def grade(path, reward):
    value = {
        "reward": reward,
        "reward_basis": ["DB", "ENV_ASSERTION", "ACTION"],
        "reward_breakdown": {"DB": reward, "ENV_ASSERTION": 1, "ACTION": 1},
    }
    with gzip.open(path, "wt") as stream:
        json.dump({"half": value, "full": value}, stream)


def test_control_cannot_pass_just_because_command_exited_zero(tmp_path):
    p = tmp_path / "reference.json.gz"
    grade(p, 0)
    with pytest.raises(ValueError):
        module().check_control(p, 1)


def test_control_accepts_product_zero_without_requiring_each_factor_zero(tmp_path):
    p = tmp_path / "noop.json.gz"
    grade(p, 0)
    module().check_control(p, 0)


def test_control_checks_both_modes_and_all_three_components(tmp_path):
    p = tmp_path / "reference.json.gz"
    grade(p, 1)
    module().check_control(p, 1)
    with gzip.open(p, "rt") as stream:
        value = json.load(stream)
    value["full"]["reward_breakdown"]["ACTION"] = 0
    with gzip.open(p, "wt") as stream:
        json.dump(value, stream)
    with pytest.raises(ValueError):
        module().check_control(p, 1)


def test_input_mutation_invalidates_cached_gate(tmp_path):
    p = tmp_path / "input"
    p.write_text("first")
    original = module().hash_files([p])
    p.write_text("second")
    assert original != module().hash_files([p])


def test_fingerprint_rejects_symlink_inputs(tmp_path):
    p = tmp_path / "input"
    p.write_text("first")
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(ValueError):
        module().hash_files([link])


def test_leak_scan_blocks_author_solution_but_allows_business_rules():
    m = module()
    assert (
        m.leak_hits({"knowledge": "Лимит57.12, правило округления и документы"}, [])
        == []
    )
    assert m.leak_hits(
        {"startup": "Read /artifacts/solution-contracts/Bank-2/Solution Contract.md"},
        [],
    )
    assert m.leak_hits({"schema": "AUTHOR_ONLY_49338"}, ["AUTHOR_ONLY_49338"])


def test_no_claim_of_difficulty_from_timeout_or_unreviewed_zero():
    m = module()
    assert m.decision([{"outcome": "technical_failure"}]) == "inconclusive"
    assert m.decision([{"outcome": "unreviewed_zero"}] * 3) == "audit_required"
    assert m.decision([{"outcome": "substantive_failure"}] * 3) == "confirmation_ready"


def test_one_success_overrides_selected_failures():
    assert (
        module().decision(
            [{"outcome": "substantive_failure"}] * 3 + [{"outcome": "success"}]
        )
        == "retire_candidate"
    )


def test_codex_refusal_counts_as_failure_but_needs_audit():
    assert (
        module().record_outcome({"confirmed_external_refusal": True, "final_reward": 0})
        == "unreviewed_zero"
    )


def test_harness_cleanup_and_timeouts_do_not_become_substantive_failures():
    m = module()
    assert (
        m.record_outcome({"technical_failure": True, "final_reward": 0})
        == "technical_failure"
    )
    assert (
        m.record_outcome({"final_reward": 0, "state": {"status": "done"}})
        == "unreviewed_zero"
    )
    assert (
        m.record_outcome({"final_reward": 1, "state": {"status": "done"}}) == "success"
    )


def test_repeat_requires_independent_review_and_stops_after_success():
    m = module()
    with pytest.raises(ValueError):
        m.allow_attempt([{"outcome": "unreviewed_zero"}], 3)
    with pytest.raises(ValueError):
        m.allow_attempt([{"outcome": "success"}], 3)
    with pytest.raises(ValueError):
        m.allow_attempt([{"outcome": "substantive_failure"}] * 3, 3)
    m.allow_attempt([{"outcome": "substantive_failure"}], 3)
    m.allow_attempt([], 3)


def test_gzip_cleanup_preserves_original_hash(tmp_path):
    p = tmp_path / "server.log"
    p.write_text("evidence\n" * 100)
    before = module().sha(p)
    result = module().compress_verified(p)
    assert result["sha256"] == before
    assert not p.exists()
    assert (
        gzip.decompress(Path(result["path"]).read_bytes())
        == ("evidence\n" * 100).encode()
    )


@pytest.mark.parametrize("basis", [["DB", "ACTION"], ["DB", "ENV_ASSERTION", "ACTION"]])
def test_control_uses_the_task_reward_basis(tmp_path, basis):
    p = tmp_path / "control.json.gz"
    row = {
        "reward": 1.0,
        "reward_basis": basis,
        "reward_breakdown": {key: 1.0 for key in basis},
    }
    with gzip.open(p, "wt") as stream:
        json.dump({"half": row, "full": row}, stream)
    module().check_control(p, 1, expected_basis=basis)
    other = ["DB", "ACTION"] if len(basis) == 3 else ["DB", "ENV_ASSERTION", "ACTION"]
    with pytest.raises(ValueError):
        module().check_control(p, 1, expected_basis=other)


def test_task_reward_basis_comes_from_selected_task(tmp_path):
    data = tmp_path / "bank"
    data.mkdir()
    (data / "tasks.json").write_text(
        json.dumps(
            [
                {
                    "id": "Bank 2",
                    "evaluation_criteria": {"reward_basis": ["DB", "ACTION"]},
                },
                {
                    "id": "Bank 11",
                    "evaluation_criteria": {
                        "reward_basis": ["DB", "ENV_ASSERTION", "ACTION"]
                    },
                },
            ]
        )
    )
    m = module()
    assert m.task_reward_basis({"data_dir": str(data), "task_id": "Bank 2"}) == [
        "DB",
        "ACTION",
    ]
    assert m.task_reward_basis({"data_dir": str(data), "task_id": "Bank 11"}) == [
        "DB",
        "ENV_ASSERTION",
        "ACTION",
    ]


@pytest.mark.parametrize("failed_phase", ["validation", "surface"])
def test_probe_rejects_missing_or_failed_receipts_before_attempt(
    tmp_path, monkeypatch, failed_phase
):
    import asyncio

    m = module()
    monkeypatch.setattr(m, "current", lambda *_: "fingerprint")
    monkeypatch.setattr(
        m,
        "read",
        lambda path: {
            "passed": path.name != f"{failed_phase}.json",
            "fingerprint": "fingerprint",
        },
    )
    with pytest.raises(ValueError, match="Missing/stale"):
        asyncio.run(m.probe({}, tmp_path))
    assert not (tmp_path / "attempts").exists()


def test_success_without_done_in_business_profile_keeps_success():
    record = {
        "final_reward": 1,
        "state": {"status": "open"},
        "result": {"business_process_only": True},
    }
    assert module().record_outcome(record) == "success"
    record["result"]["business_process_only"] = False
    assert module().record_outcome(record) == "technical_failure"


def test_control_rejects_needs_review(tmp_path):
    p = tmp_path / "control.json.gz"
    row = {
        "reward": 1.0,
        "reward_basis": ["DB", "ACTION"],
        "reward_breakdown": {"DB": 1.0, "ACTION": 1.0},
        "grading_status": "needs_review",
    }
    with gzip.open(p, "wt") as stream:
        json.dump({"half": row, "full": row}, stream)
    with pytest.raises(ValueError, match="review"):
        module().check_control(p, 1, expected_basis=["DB", "ACTION"])


def test_unreviewed_success_is_not_claimed_as_success():
    record = {
        "final_reward": 1,
        "state": {"status": "done"},
        "classification": "needs_review",
    }
    assert module().record_outcome(record) == "unreviewed_zero"


def test_seal_includes_preflight_code(tmp_path, monkeypatch):
    m = module()
    (tmp_path / "data").mkdir()
    (tmp_path / "solution").write_text("fixture")
    proof = tmp_path / "proof"
    proof.mkdir()
    for name in (
        "receipt.json",
        "image.json",
        "cli-request.json",
        "request-contract.json",
        "broker-evidence.json",
        "isolation.json",
        "anchor.json",
    ):
        (proof / name).write_text("{}")
    spec = {
        "inputs": [],
        "data_dir": str(tmp_path / "data"),
        "solution_contract": str(tmp_path / "solution"),
        "boundary": {
            "receipt": str(proof / "receipt.json"),
            "anchor_version": str(proof / "anchor.json"),
        },
    }
    files = m.inputs(spec)
    assert str(Path(m.__file__).absolute()) in files


def test_validation_does_not_reuse_old_control_reports(tmp_path, monkeypatch):
    m = module()
    old = tmp_path / "controls"
    old.mkdir()
    grade(old / "reference.json.gz", 1)
    monkeypatch.setattr(m, "current", lambda *_: "fingerprint")
    monkeypatch.setattr(
        m, "task_reward_basis", lambda _: ["DB", "ENV_ASSERTION", "ACTION"]
    )
    monkeypatch.setattr(m, "task_snapshot_sha256", lambda _: "snapshot")
    monkeypatch.setattr(m.subprocess, "run", lambda *args, **kwargs: None)
    source = tmp_path / "source"
    source.mkdir()
    (source / "db.json").write_text("sealed")
    spec = {
        "data_dir": str(source),
        "validation_command": ["{python}", "ignored"],
        "evidence_env": "BANK_TEST_EVIDENCE",
        "task_id": "Bank 2",
        "controls": [{"name": "reference", "expected": 1}],
    }
    with pytest.raises(FileNotFoundError):
        m.validate(spec, tmp_path)
    assert json.loads((tmp_path / "validation.json").read_text())["passed"] is False


def test_live_surface_must_match_checked_prompt_tools_and_limits():
    from copy import deepcopy

    m = module()
    config = {
        "mcp_layout": "directory",
        "max_calls": 100,
        "max_errors": 0,
        "max_discovery_calls": None,
    }
    tools = [{"name": "bank_directory"}, {"name": "bank_call"}]
    expected = {
        "startup": "policy",
        "prompt": "checked",
        "schemas": tools,
        "configuration": config,
    }
    schemas = {"banking_ru_pilot": {"tools": tools}}
    m.verify_live_surface(
        expected, "policy", "checked", schemas, [], configuration=config
    )
    for changed in ("prompt", "tools", "layout", "discovery", "old_packet", "endpoint"):
        packet, actual = deepcopy(expected), deepcopy(schemas)
        effective = deepcopy(config)
        prompt = "checked"
        if changed == "prompt":
            prompt = "different"
        elif changed == "tools":
            actual["banking_ru_pilot"]["tools"].append({"name": "file_read"})
        elif changed == "layout":
            effective["mcp_layout"] = "systems"
        elif changed == "discovery":
            effective["max_discovery_calls"] = 20
        elif changed == "old_packet":
            packet.pop("configuration")
        else:
            actual["crm"] = actual.pop("banking_ru_pilot")
        with pytest.raises(ValueError, match="surface"):
            m.verify_live_surface(
                packet, "policy", prompt, actual, [], configuration=effective
            )


def test_boundary_rejects_new_runtime_file(tmp_path, monkeypatch):
    import hashlib

    m = module()
    monkeypatch.setattr(m, "ROOT", tmp_path)
    files = [
        "src/tau2/runtime.py",
        "scripts/banking_ru/run_codex_harness.py",
        "scripts/banking_ru/codex_container.py",
        "scripts/banking_ru/codex_broker.py",
        "scripts/banking_ru/banking_preflight.py",
    ]
    anchor = {}
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        anchor[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    proof = tmp_path / "proof"
    proof.mkdir()
    image = "sha256:" + "a" * 64
    (proof / "receipt.json").write_text(
        json.dumps(
            {
                key: True
                for key in [
                    "host_boundary_pass",
                    "apply_patch_confined_to_container",
                    "no_host_mounts",
                    "external_tools_disabled",
                    "instructions_and_skills_not_in_request",
                    "network_none",
                    "host_only_auth",
                    "multi_step_guard_checked",
                    "cleanup_verified",
                ]
            }
        )
    )
    from test_platform.test_codex_broker import valid_evidence

    contract, evidence = valid_evidence()
    from test_platform.test_codex_container import real_directory_request_contract

    real_contract, real_request = real_directory_request_contract()
    contract["schemas"] = real_contract["schemas"]
    for event in evidence["events"]:
        if event["event"] == "request":
            event["body"]["input"][0] = real_request["input"][0]
    requests = [e["body"] for e in evidence["events"] if e["event"] == "request"]
    (proof / "broker-evidence.json").write_text(json.dumps(evidence))
    (proof / "isolation.json").write_text(
        json.dumps(
            {
                "inspection": {
                    "Image": image,
                    "Mounts": [],
                    "HostConfig": {
                        "NetworkMode": "none",
                        "ReadonlyRootfs": True,
                        "Privileged": False,
                        "CapDrop": ["ALL"],
                        "SecurityOpt": ["no-new-privileges:true"],
                    },
                },
                "network": dict(
                    interfaces=["lo"],
                    routes="header\n",
                    **{
                        k: False
                        for k in [
                            "auth",
                            "host",
                            "socket",
                            "host_tcp",
                            "lan_tcp",
                            "public_tcp",
                            "host_alias",
                            "secret_env",
                        ]
                    },
                ),
                "host_canary_unchanged": True,
                "container_patch_executed": True,
                "cleanup_verified": True,
                "model_calls": 0,
            }
        )
    )
    (proof / "request-contract.json").write_text(json.dumps(contract))
    (proof / "image.json").write_text(json.dumps({"id": image}))
    (proof / "cli-request.json").write_text(
        json.dumps(
            {
                "requests": requests,
                "thread_start": {
                    "model": "gpt-6.1-sol",
                    "baseInstructions": "BANK STARTUP",
                    "developerInstructions": "",
                    "allowProviderModelFallback": False,
                    "dynamicTools": [
                        dict(
                            type="function",
                            name=t["name"],
                            description=t["description"],
                            inputSchema=t["inputSchema"],
                            deferLoading=False,
                        )
                        for t in contract["schemas"]
                    ],
                    "sandbox": "danger-full-access",
                    "approvalPolicy": "never",
                    "ephemeral": True,
                },
            }
        )
    )
    (proof / "anchor.json").write_text(json.dumps({"files": anchor}))
    spec = {
        "boundary": {
            "receipt": "proof/receipt.json",
            "anchor_version": "proof/anchor.json",
        },
        "model": {"image": image},
    }
    assert m.boundary_check(spec)["passed"]
    capture = json.loads((proof / "cli-request.json").read_text())
    capture["thread_start"]["baseInstructions"] = "STALE STARTUP"
    (proof / "cli-request.json").write_text(json.dumps(capture))
    with pytest.raises(ValueError, match="launch differs"):
        m.boundary_check(spec)
    capture["thread_start"]["baseInstructions"] = "BANK STARTUP"
    (proof / "cli-request.json").write_text(json.dumps(capture))
    # A trusted-looking receipt must not override contaminated wire evidence.
    capture = json.loads((proof / "cli-request.json").read_text())
    capture["requests"][0]["input"].append(
        {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "HOST INSTRUCTIONS"}],
        }
    )
    (proof / "cli-request.json").write_text(json.dumps(capture))
    with pytest.raises(ValueError, match="unexpected instructions"):
        m.boundary_check(spec)
    capture["requests"][0]["input"].pop()
    (proof / "cli-request.json").write_text(json.dumps(capture))
    (tmp_path / "src/tau2/added.py").write_text("new runtime")
    with pytest.raises(ValueError, match="fresh physical preflight"):
        m.boundary_check(spec)


def test_probe_rejects_changed_surface_packet(tmp_path, monkeypatch):
    import asyncio

    m = module()
    monkeypatch.setattr(m, "current", lambda *_: "fingerprint")
    (tmp_path / "validation.json").write_text(
        json.dumps({"passed": True, "fingerprint": "fingerprint"})
    )
    (tmp_path / "surface.json").write_text(
        json.dumps(
            {"passed": True, "fingerprint": "fingerprint", "packet_sha256": "old"}
        )
    )
    (tmp_path / "surface-packet.json").write_text("changed")
    with pytest.raises(ValueError, match="Surface packet changed"):
        asyncio.run(m.probe({}, tmp_path))
    assert not (tmp_path / "attempts").exists()


def test_control_is_bound_to_target_task_and_snapshot(tmp_path):
    p = tmp_path / "control.json.gz"
    row = {
        "reward": 1.0,
        "reward_basis": ["DB", "ACTION"],
        "reward_breakdown": {"DB": 1.0, "ACTION": 1.0},
    }
    with gzip.open(p, "wt") as stream:
        json.dump(
            {"task_id": "Bank 40", "task_sha256": "wrong", "half": row, "full": row},
            stream,
        )
    with pytest.raises(ValueError, match="task"):
        module().check_control(
            p,
            1,
            expected_basis=["DB", "ACTION"],
            expected_task_id="Bank 2",
            expected_task_sha256="expected",
        )


def test_control_filename_cannot_escape_run_directory():
    with pytest.raises(ValueError, match="control"):
        module().control_filename({"name": "../../old-report"})


@pytest.mark.parametrize("task_id", ["Bank 2", "Bank 12"])
@pytest.mark.parametrize("route,expected", [("reference", 1), ("noop", 0)])
def test_real_control_report_matches_task_contract(tmp_path, task_id, route, expected):
    from copy import deepcopy

    from tau2.domains.banking_ru.environment import get_environment, get_tasks
    from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
    from tau2.platform.session import PlatformSession
    from tests.utils.banking_preflight_control import write_control_report

    task = next(task for task in get_tasks() if task.id == task_id)
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)
    if route == "reference":
        session.replay_actions(deepcopy(task.evaluation_criteria.actions))
    session.call("done", {})
    assert session.errors == 0
    path = tmp_path / "control.json.gz"
    write_control_report(session, path)
    m = module()
    spec = {"task_id": task_id, "data_dir": str(BANKING_RU_DATA_DIR)}
    m.check_control(
        path,
        expected,
        expected_basis=m.task_reward_basis(spec),
        expected_task_id=task_id,
        expected_task_sha256=m.task_snapshot_sha256(spec),
    )


def test_real_directory_schemas_match_checked_surface():
    import sys

    from tau2.domains.banking_ru.environment import get_environment, get_tasks
    from tau2.platform.session import PlatformSession

    sys.path.insert(0, str(ROOT / "scripts/banking_ru"))
    try:
        from run_codex_harness import build_prompt
    finally:
        sys.path.pop(0)
    task = next(task for task in get_tasks() if task.id == "Bank 2")
    session = PlatformSession(
        task, get_environment, max_calls=100, max_errors=0, mcp_layout="directory"
    )
    prompt = build_prompt(
        policy=session.policy,
        ticket=task.agent_ticket(),
        max_calls=session.max_calls,
        max_errors=session.max_errors,
    )
    assert "+7 926 118-77-30" in prompt
    assert "Сообщение клиента:" in prompt
    expected = {
        "startup": session.policy,
        "configuration": module().session_configuration(session),
        "prompt": prompt,
        "schemas": [tool.model_dump(mode="json") for tool in session.mcp_tools],
    }
    systems = {
        "banking_ru_pilot": {
            "tools": [t.model_dump(mode="json") for t in session.mcp_tools]
        }
    }
    module().verify_live_surface(
        expected,
        session.policy,
        prompt,
        systems,
        [],
        configuration=module().session_configuration(session),
    )


def test_control_rejects_boolean_reward(tmp_path):
    p = tmp_path / "control.json.gz"
    row = {
        "reward": True,
        "reward_basis": ["DB", "ACTION"],
        "reward_breakdown": {"DB": 1.0, "ACTION": 1.0},
    }
    with gzip.open(p, "wt") as stream:
        json.dump({"half": row, "full": row}, stream)
    with pytest.raises(ValueError, match="reward"):
        module().check_control(p, 1, expected_basis=["DB", "ACTION"])


def test_validation_runs_against_isolated_copy_of_sealed_data(tmp_path, monkeypatch):
    m = module()
    original = tmp_path / "source"
    original.mkdir()
    (original / "db.json").write_text("sealed db")
    monkeypatch.setattr(m, "current", lambda *_: "fingerprint")
    monkeypatch.setattr(m, "task_reward_basis", lambda _: ["DB", "ACTION"])
    monkeypatch.setattr(m, "task_snapshot_sha256", lambda _: "snapshot")
    observed = []

    def fake_run(command, **kwargs):
        env = kwargs["env"]
        root = Path(env["TAU2_DATA_DIR"])
        data = root / "tau2/domains/banking_ru"
        assert data != original and (data / "db.json").read_text() == "sealed db"
        assert env["BANKING_PROTOTYPE_DATA_DIR"] == str(data)
        assert env["PYTHONPATH"] == str(m.ROOT / "src")
        observed.append(data)
        row = {
            "reward": 1,
            "reward_basis": ["DB", "ACTION"],
            "reward_breakdown": {"DB": 1, "ACTION": 1},
        }
        with gzip.open(
            Path(env["BANK_TEST_EVIDENCE"]) / "reference.json.gz", "wt"
        ) as stream:
            json.dump(
                {
                    "task_id": "Bank 2",
                    "task_sha256": "snapshot",
                    "half": row,
                    "full": row,
                },
                stream,
            )

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    out = tmp_path / "output"
    out.mkdir()
    spec = {
        "data_dir": str(original),
        "validation_command": ["{python}", "ignored"],
        "evidence_env": "BANK_TEST_EVIDENCE",
        "task_id": "Bank 2",
        "controls": [{"name": "reference", "expected": 1}],
    }
    m.validate(spec, out)
    assert observed and not observed[0].exists()
    assert (original / "db.json").read_text() == "sealed db"
    assert json.loads((out / "validation.json").read_text())["passed"] is True


def test_validation_rejects_changed_copied_database(tmp_path, monkeypatch):
    m = module()
    source = tmp_path / "source"
    source.mkdir()
    (source / "db.json").write_text("sealed")
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(m, "current", lambda *_: "fingerprint")

    def mutate(command, **kwargs):
        Path(kwargs["env"]["BANKING_PROTOTYPE_DATA_DIR"], "db.json").write_text(
            "modified"
        )

    monkeypatch.setattr(m.subprocess, "run", mutate)
    spec = {
        "data_dir": str(source),
        "validation_command": ["{python}", "ignored"],
        "evidence_env": "BANK_TEST_EVIDENCE",
        "task_id": "Bank 2",
        "controls": [],
    }
    with pytest.raises(ValueError, match="modified"):
        m.validate(spec, out)
    assert (source / "db.json").read_text() == "sealed"
    assert json.loads((out / "validation.json").read_text())["passed"] is False


def test_validation_runs_real_grades_on_copied_dataset(tmp_path, monkeypatch):
    from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR

    m = module()
    monkeypatch.setattr(m, "current", lambda *_: "test-fingerprint")
    out = tmp_path / "preflight"
    out.mkdir()
    code = "\n".join(
        [
            "import os, sys",
            "from copy import deepcopy",
            "from pathlib import Path",
            "sys.path.insert(0, os.getcwd())",
            "from tau2.domains.banking_ru.environment import get_environment, get_tasks",
            "from tau2.platform.session import PlatformSession",
            "from tests.utils.banking_preflight_control import write_control_report",
            "task = next(t for t in get_tasks() if t.id == 'Bank 2')",
            "out = Path(os.environ['BANK_CONTROL_OUTPUT'])",
            "for route in ('reference', 'noop'):",
            "    session = PlatformSession(task, get_environment, max_calls=100, max_errors=0)",
            "    if route == 'reference': session.replay_actions(deepcopy(task.evaluation_criteria.actions))",
            "    session.call('done', {})",
            "    assert session.errors == 0",
            "    write_control_report(session, out / (route + '.json.gz'))",
        ]
    )
    spec = {
        "task_id": "Bank 2",
        "data_dir": str(BANKING_RU_DATA_DIR),
        "validation_command": ["{python}", "-c", code],
        "evidence_env": "BANK_CONTROL_OUTPUT",
        "controls": [
            {"name": "reference", "expected": 1},
            {"name": "noop", "expected": 0},
        ],
    }
    m.validate(spec, out)
    receipt = json.loads((out / "validation.json").read_text())
    assert receipt["passed"] is True
    assert (Path(receipt["evidence_dir"]) / "controls/reference.json.gz").is_file()


def test_control_rejects_extra_reward_component(tmp_path):
    p = tmp_path / "control.json.gz"
    row = {
        "reward": 1.0,
        "reward_basis": ["DB", "ACTION"],
        "reward_breakdown": {"DB": 1.0, "ACTION": 1.0, "ENV_ASSERTION": 0.0},
    }
    with gzip.open(p, "wt") as stream:
        json.dump({"half": row, "full": row}, stream)
    with pytest.raises(ValueError, match="breakdown"):
        module().check_control(p, 1, expected_basis=["DB", "ACTION"])


def test_surface_child_uses_directory_and_probes_gateway_without_solution_file(
    tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/banking_ru"))
    m = module()
    (tmp_path / "AUTHOR_ONLY_canary.txt").write_text("AUTHOR_ONLY_surface_fixture")
    m.surface_child(
        {
            "task_id": "Bank 2",
            "model": {"max_calls": 100, "max_errors": 0, "max_discovery_calls": 30},
        },
        tmp_path,
    )
    packet = m.read(tmp_path / "surface-packet.json")
    result = m.read(tmp_path / "surface-result.json")
    assert packet["configuration"] == {
        "mcp_layout": "directory",
        "max_calls": 100,
        "max_errors": 0,
        "max_discovery_calls": 30,
    }
    assert {t["name"] for t in packet["schemas"]} == {"bank_directory", "bank_call"}
    assert result["passed"] and result["model_calls"] == 0
    assert result["canary_not_searchable"]
    assert all(result["hidden_reads_denied"].values())
    assert result["native_bypass_denied"]


@pytest.mark.parametrize("failure_stage", ["journal", "http_cleanup", "server_cleanup"])
def test_probe_persists_runtime_before_postprocessing_and_cleanup_errors(
    tmp_path, monkeypatch, failure_stage
):
    import asyncio
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace

    from tau2.platform import client as client_module
    from test_platform.test_codex_harness import load_harness

    h = load_harness()
    m = module()
    evidence = {
        "stdout": "MODEL_TRANSCRIPT",
        "stderr": "MODEL_STDERR",
        "broker_evidence": {"events": ["captured"]},
    }
    record = {
        "task_id": "Bank 2",
        "run": evidence,
        "classification": "luna_pass",
        "final_reward": 1.0,
    }
    for phase in ["validation", "surface"]:
        m.write(tmp_path / (phase + ".json"), {"passed": True, "fingerprint": "fp"})
    m.write(tmp_path / "surface-packet.json", {})
    surface = m.read(tmp_path / "surface.json")
    surface["packet_sha256"] = m.sha(tmp_path / "surface-packet.json")
    m.write(tmp_path / "surface.json", surface)
    source = tmp_path / "source"
    source.mkdir()
    (source / "fixture.txt").write_text("SELF_CONTAINED")
    monkeypatch.setattr(m, "current", lambda *a: "fp")
    monkeypatch.setattr(m, "boundary_check", lambda *a: {"image": "fixture"})
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda *a, **k: nullcontext())
    monkeypatch.delenv("BANKING_LANGFUSE_ENABLED", raising=False)

    class Server:
        def __init__(self, *a, **k):
            pass

        def poll(self):
            return None

        def terminate(self):
            if failure_stage in ["journal", "server_cleanup"]:
                raise RuntimeError("server cleanup failed")

        def wait(self, **kwargs):
            return 0

    class Client:
        def __init__(self, *a, **k):
            self._http = self

        async def open(self, *a, **k):
            pass

        async def aclose(self):
            if failure_stage in ["journal", "http_cleanup"]:
                raise RuntimeError("http cleanup failed")

    async def stub(**kwargs):
        return record

    def attach(*args):
        # The durable copy must exist before this potentially failing stage.
        saved = m.read(tmp_path / "attempts/01/record.json")
        assert saved["run"] == evidence
        if failure_stage == "journal":
            raise RuntimeError("journal failed")

    if failure_stage == "journal":
        work = tmp_path / "model-work"
        work.mkdir()

        def cleanup():
            raise RuntimeError("temporary directory cleanup failed")

        monkeypatch.setattr(
            h.tempfile,
            "TemporaryDirectory",
            lambda **kwargs: SimpleNamespace(name=str(work), cleanup=cleanup),
        )
    monkeypatch.setattr(m.subprocess, "Popen", Server)
    monkeypatch.setattr(client_module, "PlatformClient", Client)
    monkeypatch.setitem(
        sys.modules,
        "run_codex_harness",
        SimpleNamespace(
            run_one_attempt=stub,
            build_prompt=lambda **k: "BANK TICKET",
            attach_persisted_journal=attach,
            temporary_run_directory=h.temporary_run_directory,
        ),
    )
    spec = {
        "max_attempts": 1,
        "task_id": "Bank 2",
        "data_dir": str(source),
        "model": {
            "name": "local-stub",
            "effort": "low",
            "max_calls": 100,
            "max_errors": 0,
            "timeout_seconds": 1,
            "image": "fixture",
        },
    }
    expected = (
        "journal failed"
        if failure_stage == "journal"
        else failure_stage.replace("_", " ") + " failed"
    )
    with pytest.raises(RuntimeError, match=expected):
        asyncio.run(m.probe(spec, tmp_path))
    assert m.read(tmp_path / "attempts/01/record.json")["run"] == evidence
    outcome = m.read(tmp_path / "attempts/01/outcome.json")
    assert outcome["outcome"] == "technical_failure" and outcome["error"] == expected
    if failure_stage == "journal":
        assert len(outcome["error_notes"]) == 3


def test_physical_schema_proof_rejects_toy_and_stale_schemas():
    from test_platform.test_codex_container import (
        real_directory_request_contract,
        request_contract,
    )

    m = module()
    with pytest.raises(ValueError, match="real banking directory schemas"):
        m.verify_directory_schema_proof(request_contract()[0])
    contract, _ = real_directory_request_contract()
    m.verify_directory_schema_proof(contract)
    contract["schemas"][0]["inputSchema"]["properties"]["tool_names"].pop("maxItems")
    with pytest.raises(ValueError, match="real banking directory schemas"):
        m.verify_directory_schema_proof(contract)
