"""Banking preflight and evidence gates for the supported Codex harness."""

import argparse
import asyncio
import gzip
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    """Read plain or compressed author evidence."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def write(path, value):
    """Persist a phase receipt; never overwrite model attempt directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sha(path):
    """Hash the exact bytes of an input or evidence file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_files(paths):
    """Fingerprint explicit inputs; exclude disposable Python caches only."""
    result = {}
    for path in map(Path, paths):
        if not path.exists():
            raise ValueError(f"Missing input: {path}")
        for item in sorted(path.rglob("*")) if path.is_dir() else [path]:
            if (
                "__pycache__" in item.parts
                or ".pytest_cache" in item.parts
                or item.suffix == ".pyc"
            ):
                continue
            if item.is_symlink():
                raise ValueError(f"Symlink input is not permitted: {item}")
            if item.is_file():
                result[str(item.absolute())] = sha(item)
    return result


def path_for(value):
    return ROOT / value


def inputs(spec):
    paths = [path_for(p) for p in spec["inputs"]] + [
        ROOT / "src/tau2",
        ROOT / "scripts/banking_ru",
        ROOT / "tests",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        Path(sys.executable).resolve(),
    ]
    paths += [path_for(spec["data_dir"])]
    if spec.get("solution_contract"):
        paths.append(path_for(spec["solution_contract"]))
    boundary = spec["boundary"]
    paths += [path_for(boundary[k]) for k in ["receipt", "anchor_version"]]
    paths += [
        path_for(boundary["receipt"]).parent / n
        for n in [
            "image.json",
            "cli-request.json",
            "request-contract.json",
            "broker-evidence.json",
            "isolation.json",
        ]
    ]
    files = hash_files(paths)
    files["SPEC"] = hashlib.sha256(
        json.dumps(spec, sort_keys=True).encode()
    ).hexdigest()
    return files


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def current(spec, out):
    sealed = read(out / "seal.json")
    if sealed["files"] != inputs(spec):
        raise ValueError(
            "Inputs changed after seal; create a new candidate, do not reuse gates"
        )
    return sealed["fingerprint"]


def requires_review(value):
    """Never turn an unresolved evaluator verdict into a validated result."""
    if isinstance(value, dict):
        return value.get("grading_status") == "needs_review" or any(
            requires_review(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(requires_review(item) for item in value)
    return False


def check_control(
    path,
    expected,
    *,
    expected_basis=None,
    expected_task_id=None,
    expected_task_sha256=None,
):
    """Verify each control against the actual task reward basis in both modes."""
    basis = list(expected_basis or ["DB", "ENV_ASSERTION", "ACTION"])
    allowed = ({"DB", "ACTION"}, {"DB", "ENV_ASSERTION", "ACTION"})
    if len(basis) != len(set(basis)) or set(basis) not in allowed:
        raise ValueError("Unsupported task reward basis")
    report = read(path)
    if expected_task_id is not None and (
        report.get("task_id") != expected_task_id
        or report.get("task_sha256") != expected_task_sha256
    ):
        raise ValueError(f"{path}: wrong task or task snapshot")
    for mode in ("half", "full"):
        result = report[mode]
        if requires_review(result):
            raise ValueError(f"{path}: unresolved grading review in {mode}")
        actual = result["reward_basis"]
        if len(actual) != len(set(actual)) or set(actual) != set(basis):
            raise ValueError(f"{path}: wrong reward basis in {mode}")
        reward = result.get("reward")
        if (
            isinstance(reward, bool)
            or not isinstance(reward, (int, float))
            or not math.isfinite(reward)
            or not 0 <= reward <= 1
        ):
            raise ValueError(f"{path}: invalid reward in {mode}")
        values = result["reward_breakdown"]
        if not isinstance(values, dict) or set(values) != set(basis):
            raise ValueError(f"{path}: unexpected reward breakdown in {mode}")
        try:
            components = [values[key] for key in basis]
            if any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or not 0 <= v <= 1
                for v in components
            ):
                raise ValueError("Invalid component")
            product = math.prod(components)
        except (KeyError, TypeError) as error:
            raise ValueError(
                f"{path}: incomplete reward breakdown in {mode}"
            ) from error
        if result["reward"] != expected or product != expected:
            raise ValueError(
                f"{path}: incorrect {mode} grade: {result['reward']}, {values}"
            )


def task_reward_basis(spec):
    """Use only the target task's declared reward contract from the sealed data."""
    from tau2.domains.banking_ru.task_store import load_tasks

    tasks = load_tasks(path_for(spec["data_dir"]) / "tasks.json")
    selected = [task for task in tasks if task["id"] == spec["task_id"]]
    if len(selected) != 1:
        raise ValueError("Preflight requires exactly one target task")
    criteria = selected[0]["evaluation_criteria"]
    basis = criteria.get("reward_basis")
    if not isinstance(basis, list):
        raise ValueError("Target task must declare its reward basis")
    return basis


def task_snapshot_sha256(spec):
    """Match the canonical task fingerprint recorded by PlatformSession."""
    from tau2.data_model.tasks import Task
    from tau2.domains.banking_ru.task_store import load_tasks

    tasks = load_tasks(path_for(spec["data_dir"]) / "tasks.json")
    selected = [task for task in tasks if task["id"] == spec["task_id"]]
    if len(selected) != 1:
        raise ValueError("Preflight requires exactly one target task")
    task = Task.model_validate(selected[0]).model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(task, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def control_filename(control):
    name = control.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Invalid control name")
    return name + ".json.gz"


def leak_hits(surfaces, private_tokens):
    """A finite leak screen; physical isolation is a separate mandatory gate."""
    patterns = [
        r"solution-contracts",
        r"Solution Contract",
        r"compatibility_certificates",
        r"bank2_resolution_oracle",
        r"AUTHOR_ONLY_[A-Za-z0-9_-]+",
    ]
    patterns += [re.escape(t) for t in private_tokens if t]
    return [
        {"surface": name, "pattern": pattern}
        for name, body in surfaces.items()
        for pattern in patterns
        if re.search(pattern, body, re.I)
    ]


def session_configuration(session):
    return {
        "mcp_layout": session.mcp_layout,
        "max_calls": session.max_calls,
        "max_errors": session.max_errors,
        "max_discovery_calls": session.max_discovery_calls,
    }


def verify_live_surface(
    expected, policy, prompt, schemas, private_tokens, *, configuration
):
    """Fail before model invocation if the actual session differs from the checked one."""
    from tau2.domains.banking_ru.mcp_systems import SINGLE_SERVER

    actual_tools = [tool for response in schemas.values() for tool in response["tools"]]
    if (
        configuration.get("mcp_layout") != "directory"
        or configuration != expected.get("configuration")
        or set(schemas) != {SINGLE_SERVER}
        or len(actual_tools) != 2
        or {tool["name"] for tool in actual_tools} != {"bank_directory", "bank_call"}
    ):
        raise ValueError("Live model surface differs from verified directory preflight")
    by_name = lambda_tool_sort
    if (
        policy != expected["startup"]
        or prompt != expected["prompt"]
        or by_name(actual_tools) != by_name(expected["schemas"])
        or leak_hits({"prompt": prompt, "schemas": json.dumps(schemas)}, private_tokens)
    ):
        raise ValueError("Live model surface differs from verified preflight")


def lambda_tool_sort(tools):
    return sorted(tools, key=lambda tool: tool["name"])


def record_outcome(record):
    """Separate observed zeroes from confirmed substantive model failures."""
    if (
        record.get("confirmed_external_refusal")
        or record.get("classification") == "needs_review"
        or requires_review(record.get("result", {}))
    ):
        return "unreviewed_zero"
    if record.get("technical_failure") or record.get("classification", "").startswith(
        "invalid_"
    ):
        return "technical_failure"
    if record.get("state", {}).get("status") != "done" and not record.get(
        "result", {}
    ).get("business_process_only", False):
        return "technical_failure"
    return "success" if record.get("final_reward") == 1 else "unreviewed_zero"


def decision(attempts):
    outcomes = [a["outcome"] for a in attempts]
    if "false_zero" in outcomes:
        return "repair_evaluator"
    if "success" in outcomes:
        return "retire_candidate"
    if "unreviewed_zero" in outcomes:
        return "audit_required"
    if outcomes.count("substantive_failure") >= 3:
        return "confirmation_ready"
    if "substantive_failure" in outcomes:
        return "repeat_unchanged"
    return "inconclusive"


def attempts(out):
    result = []
    for folder in sorted((out / "attempts").glob("*")):
        if not folder.is_dir():
            continue
        receipt = folder / "outcome.json"
        result.append(
            read(receipt)
            if receipt.exists()
            else {"index": folder.name, "outcome": "unfinished"}
        )
    return result


def allow_attempt(previous, maximum):
    if len(previous) >= maximum:
        raise ValueError("Attempt budget exhausted; every started attempt counts")
    if previous and decision(previous) != "repeat_unchanged":
        raise ValueError(
            "Repeat requires audited substantive failure; success retires the candidate"
        )


def boundary_check(spec):
    """Reuse real apply_patch/CLI-stub proof only for unchanged runtime and image."""
    b = spec["boundary"]
    receipt = read(path_for(b["receipt"]))
    flags = [
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
    if not all(receipt.get(k) is True for k in flags):
        raise ValueError("Unverified model boundary")
    parent = path_for(b["receipt"]).parent
    # Recompute from the complete wire evidence; legacy boolean flags alone
    # accepted extra Codex namespaces and generic developer instructions.
    import importlib.util

    runtime_spec = importlib.util.spec_from_file_location(
        "banking_codex_container", Path(__file__).with_name("codex_container.py")
    )
    runtime = importlib.util.module_from_spec(runtime_spec)
    runtime_spec.loader.exec_module(runtime)
    contract = read(parent / "request-contract.json")
    capture = read(parent / "cli-request.json")
    thread = capture.get("thread_start", {})
    if (
        thread.get("model") != contract["model"]
        or thread.get("baseInstructions") != contract["startup"]
        or thread.get("developerInstructions") != ""
        or thread.get("dynamicTools") != runtime.dynamic_bank_tools(contract["schemas"])
        or thread.get("allowProviderModelFallback") is not False
    ):
        raise ValueError("Codex launch differs from the captured request contract")
    for setting, field in [("name", "model"), ("effort", "effort")]:
        if setting in spec["model"] and spec["model"][setting] != contract[field]:
            raise ValueError(
                "Codex request proof belongs to another model configuration"
            )
    if not capture.get("requests"):
        raise ValueError("Missing complete Codex wire requests")
    runtime.validate_model_request(capture["requests"][0], contract)
    evidence = read(parent / "broker-evidence.json")
    runtime.broker_module().verify_evidence(
        evidence, contract, runtime.validate_model_request
    )
    if capture["requests"] != [
        e["body"] for e in evidence["events"] if e["event"] == "request"
    ]:
        raise ValueError("Wire capture differs from the host-owned inference ledger")
    isolation = read(parent / "isolation.json")
    runtime.validate_inspection(isolation["inspection"])
    probes = isolation["network"]
    if (
        probes["interfaces"] != ["lo"]
        or len(probes["routes"].strip().splitlines()) != 1
        or any(
            probes[k]
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
        )
        or not all(
            isolation.get(k) is True
            for k in [
                "host_canary_unchanged",
                "container_patch_executed",
                "cleanup_verified",
            ]
        )
        or isolation.get("model_calls") != 0
    ):
        raise ValueError("Physical no-network/no-credential boundary is unverified")
    image = spec["model"]["image"]
    if (
        not re.fullmatch(r"sha256:[a-f0-9]{64}", image)
        or read(parent / "image.json")["id"] != image
        or isolation["inspection"].get("Image") != image
    ):
        raise ValueError(
            "Image differs from verified preflight or is not an immutable image ID"
        )
    if (
        thread.get("sandbox") != "danger-full-access"
        or thread.get("approvalPolicy") != "never"
        or thread.get("ephemeral") is not True
    ):
        raise ValueError("Preflight did not verify required full-access CLI")
    anchor = read(path_for(b["anchor_version"]))["files"]
    source = ROOT / "src/tau2"
    actual_source = {str(Path(name).relative_to(ROOT)) for name in hash_files([source])}
    anchored_source = {name for name in anchor if name.startswith("src/tau2/")}
    required = {
        "scripts/banking_ru/run_codex_harness.py",
        "scripts/banking_ru/codex_container.py",
        "scripts/banking_ru/codex_broker.py",
        "scripts/banking_ru/banking_preflight.py",
    }
    names = actual_source | required
    if (
        not actual_source
        or actual_source != anchored_source
        or not required <= anchor.keys()
        or any(sha(ROOT / key) != anchor[key] for key in names)
    ):
        raise ValueError(
            "Runtime/harness changed: a fresh physical preflight is required"
        )
    return {
        "passed": True,
        "files_verified": len(names),
        "image": spec["model"]["image"],
    }


def seal(spec, out):
    for control in spec["controls"]:
        control_filename(control)
    roles = {c["role"] for c in spec["controls"]}
    if not {"positive", "alternative", "noop", "negative"} <= roles:
        raise ValueError(
            "Reference, alternative, no-op and relevant wrong paths are required"
        )
    if len({c["name"] for c in spec["controls"]}) != len(spec["controls"]):
        raise ValueError("Control names must be distinct")
    if any(
        type(c["expected"]) is not int
        or c["expected"] != int(c["role"] in {"positive", "alternative"})
        for c in spec["controls"]
    ):
        raise ValueError("Invalid control expectation")
    if not spec["hypothesis"] or not spec["references"] or not spec["unique_outcome"]:
        raise ValueError("Declare the hypothesis, primary references and outcome proof")
    if any(
        not r["url"].startswith("https://") or not r["mechanism"]
        for r in spec["references"]
    ):
        raise ValueError("References need actual URLs and transferable mechanisms")
    if (out / "seal.json").exists():
        raise ValueError("A sealed candidate is immutable; use a new output directory")
    files = inputs(spec)
    write(out / "spec.json", spec)
    write(
        out / "seal.json",
        {
            "fingerprint": fingerprint(files),
            "files": files,
            "created_at": time.time(),
            "model_calls": 0,
        },
    )


def relative_tree_hash(root):
    """Fingerprint a tree independently of the path where it is copied."""
    root = Path(root).resolve()
    return {
        str(Path(name).relative_to(root)): digest
        for name, digest in hash_files([root]).items()
    }


def validate(spec, out):
    """Run controls against a clean, byte-identical copy of the sealed data."""
    fp = current(spec, out)
    source = path_for(spec["data_dir"]).resolve()
    evidence_env = spec["evidence_env"]
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", evidence_env) or evidence_env in {
        "TAU2_DATA_DIR",
        "PYTHONPATH",
        "HOME",
        "BANKING_PROTOTYPE_DATA_DIR",
        "USE_LANGFUSE",
    }:
        raise ValueError("Invalid control evidence environment variable")
    validation_run = out / "validation-runs" / str(time.time_ns())
    if validation_run.resolve().is_relative_to(source):
        raise ValueError("Validation output must be outside sealed data")
    controls_dir = validation_run / "controls"
    controls_dir.mkdir(parents=True, exist_ok=False)
    started = time.time()
    receipt = {
        "fingerprint": fp,
        "passed": False,
        "model_calls": 0,
        "evidence_dir": str(validation_run),
    }
    try:
        source_hash = relative_tree_hash(source)
        with tempfile.TemporaryDirectory(
            prefix="bank-control-", dir=validation_run
        ) as temp:
            data_root = Path(temp) / "data"
            copied = data_root / "tau2/domains/banking_ru"
            shutil.copytree(source, copied)
            if relative_tree_hash(copied) != source_hash:
                raise ValueError("Validation data differs from sealed data")
            substitutions = {
                "python": sys.executable,
                "repo": str(ROOT),
                "out": str(out),
                "data": str(copied),
            }
            command = []
            for arg in spec["validation_command"]:
                for key, value in substitutions.items():
                    arg = arg.replace("{" + key + "}", value)
                command.append(arg)
            if (
                not command
                or Path(command[0]).resolve() != Path(sys.executable).resolve()
            ):
                raise ValueError("Validation must use the current Python interpreter")
            env = os.environ.copy()
            env.update(
                TAU2_DATA_DIR=str(data_root),
                PYTHONPATH=str(ROOT / "src"),
                BANKING_PROTOTYPE_DATA_DIR=str(copied),
                USE_LANGFUSE="false",
            )
            env[evidence_env] = str(controls_dir)
            receipt["command"] = command
            with (validation_run / "validation.log").open("w") as log:
                subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=spec.get("validation_timeout", 600),
                    check=True,
                )
            if relative_tree_hash(copied) != source_hash:
                raise ValueError("Validation modified its sealed data copy")
        basis = task_reward_basis(spec)
        task_sha256 = task_snapshot_sha256(spec)
        for control in spec["controls"]:
            check_control(
                controls_dir / control_filename(control),
                control["expected"],
                expected_basis=basis,
                expected_task_id=spec["task_id"],
                expected_task_sha256=task_sha256,
            )
        current(spec, out)
        receipt.update(passed=True, controls=spec["controls"])
    except Exception as exc:
        receipt["error"] = str(exc)
        raise
    finally:
        receipt["elapsed_seconds"] = round(time.time() - started, 3)
        write(validation_run / "receipt.json", receipt)
        write(out / "validation.json", receipt)


def surface_child(spec, out):
    """Runs only in an author subprocess with a separate throwaway bank session."""
    from run_codex_harness import build_prompt

    from tau2.domains.banking_ru.environment import get_environment, get_tasks
    from tau2.platform.session import PlatformSession

    task = next(t for t in get_tasks() if t.id == spec["task_id"])
    session = PlatformSession(
        task,
        get_environment,
        max_calls=spec["model"]["max_calls"],
        max_errors=spec["model"]["max_errors"],
        mcp_layout="directory",
        max_discovery_calls=spec["model"].get("max_discovery_calls"),
    )
    schemas = [t.model_dump(mode="json") for t in session.mcp_tools]
    startup = session.policy
    single = session.env.get_policy()
    corpus = session.env.tools.corpus
    bodies = {name: corpus.read(name)["content"] for name in corpus.documents}
    visible = {
        "startup": startup,
        "single_startup": single,
        "schemas": json.dumps(schemas),
        "discovered_schemas": json.dumps(
            [
                tool.model_dump(mode="json")
                for tools in session.system_tools.values()
                for tool in tools
            ]
        ),
        "prompt": build_prompt(
            policy=startup,
            ticket=task.agent_ticket(),
            max_calls=session.max_calls,
            max_errors=session.max_errors,
            max_discovery_calls=session.max_discovery_calls,
        ),
        **bodies,
    }
    hits = leak_hits(visible, spec.get("private_tokens", []))
    for token in spec.get("forbidden_startup_tokens", []):
        if token in startup or token in single or token in json.dumps(schemas):
            hits.append({"surface": "startup_or_schema", "token": token})
    hidden = [
        "/tasks.json",
        "/db.json",
        "/scripts/banking_ru/bank2_resolution_oracle.py",
        *(
            [str(path_for(spec["solution_contract"]))]
            if spec.get("solution_contract")
            else []
        ),
        str(ROOT / "AGENTS.md"),
        str(out / "AUTHOR_ONLY_canary.txt"),
    ]

    def knowledge_call(arguments):
        return session.call(
            "bank_call",
            {
                "system_id": "knowledge",
                "tool_name": "corpus_command",
                "arguments": arguments,
            },
        )

    opened = session.call(
        "bank_directory",
        {
            "operation": "open_tool",
            "system_id": "knowledge",
            "tool_name": "corpus_command",
        },
    )
    if opened.is_error:
        raise ValueError("Cannot open knowledge schema for isolation checks")
    denied = {
        p: knowledge_call({"command": "cat " + shlex.quote(p)}).is_error for p in hidden
    }
    native_bypass_denied = session.call("file_read", {"path": "/AGENTS.md"}).is_error
    canary = (out / "AUTHOR_ONLY_canary.txt").read_text().strip()
    searched = knowledge_call({"command": "grep -n " + shlex.quote(canary) + " /"})
    search_result = (
        json.loads(searched.content[0].text) if not searched.is_error else None
    )
    search_clean = search_result is not None and search_result.get("matches") == []
    write(
        out / "surface-packet.json",
        {
            "startup": startup,
            "single_startup": single,
            "configuration": session_configuration(session),
            "prompt": visible["prompt"],
            "schemas": schemas,
            "corpus": [
                {"path": n, "sha256": hashlib.sha256(b.encode()).hexdigest()}
                for n, b in bodies.items()
            ],
        },
    )
    write(
        out / "surface-result.json",
        {
            "passed": not hits
            and all(denied.values())
            and search_clean
            and native_bypass_denied,
            "leak_hits": hits,
            "hidden_reads_denied": denied,
            "native_bypass_denied": native_bypass_denied,
            "canary_not_searchable": search_clean,
            "limits": "Finite content screen and MCP probes; physical CLI boundary checked separately",
            "model_calls": 0,
        },
    )


def surface(spec, out):
    fp = current(spec, out)
    boundary = boundary_check(spec)
    (out / "AUTHOR_ONLY_canary.txt").write_text(
        "AUTHOR_ONLY_" + os.urandom(16).hex() + "\n"
    )
    with tempfile.TemporaryDirectory(prefix="bank-prototype-surface-") as temp:
        root = Path(temp) / "data"
        copied = root / "tau2/domains/banking_ru"
        shutil.copytree(path_for(spec["data_dir"]), copied)
        if relative_tree_hash(copied) != relative_tree_hash(path_for(spec["data_dir"])):
            raise ValueError("Surface data differs from sealed data")
        env = os.environ.copy()
        env.update(
            TAU2_DATA_DIR=str(root), PYTHONPATH=str(ROOT / "src"), USE_LANGFUSE="false"
        )
        with (out / "surface.log").open("w") as log:
            subprocess.run(
                [sys.executable, __file__, "_surface", "--out", str(out)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=90,
            )
    result = read(out / "surface-result.json")
    result.update(
        fingerprint=fp,
        boundary=boundary,
        packet_sha256=sha(out / "surface-packet.json"),
    )
    write(out / "surface.json", result)
    current(spec, out)
    if not result["passed"]:
        raise ValueError("Visible surface or hidden-read probe failed")


async def probe(spec, out):
    """Reuse supported harness with a fresh bank server, session and isolated container."""
    fp = current(spec, out)
    for phase in ["validation", "surface"]:
        receipt = read(out / (phase + ".json"))
        if receipt.get("passed") is not True or receipt.get("fingerprint") != fp:
            raise ValueError(f"Missing/stale {phase} gate")
    surface_receipt = read(out / "surface.json")
    if surface_receipt.get("packet_sha256") != sha(out / "surface-packet.json"):
        raise ValueError("Surface packet changed after verification")
    boundary = boundary_check(spec)
    previous = attempts(out)
    allow_attempt(previous, spec["max_attempts"])
    folder = out / "attempts" / f"{len(previous) + 1:02}"
    folder.mkdir(parents=True, exist_ok=False)
    write(
        folder / "manifest.json",
        {
            "fingerprint": fp,
            "started_at": time.time(),
            "model": spec["model"],
            "new_server_session_container": True,
        },
    )
    from run_codex_harness import (
        attach_persisted_journal,
        build_prompt,
        run_one_attempt,
        temporary_run_directory,
    )

    from tau2.platform.client import PlatformClient

    record = None
    try:
        with temporary_run_directory(prefix="bank-prototype-model-") as temp:
            root = Path(temp)
            copied = root / "data/tau2/domains/banking_ru"
            shutil.copytree(path_for(spec["data_dir"]), copied)
            if relative_tree_hash(copied) != relative_tree_hash(
                path_for(spec["data_dir"])
            ):
                raise ValueError("Model data differs from sealed data")
            work = root / "work"
            work.mkdir()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            model = spec["model"]
            env = os.environ.copy()
            env.update(
                TAU2_DATA_DIR=str(root / "data"),
                PYTHONPATH=str(ROOT / "src"),
                USE_LANGFUSE="false",
            )
            command = [
                sys.executable,
                "-m",
                "tau2.platform",
                "--domain",
                "banking_ru",
                "--split",
                "base",
                "--mcp-layout",
                "directory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--max-calls",
                str(model["max_calls"]),
                "--max-errors",
                str(model["max_errors"]),
                "--results-dir",
                str(folder / "platform"),
            ]
            if model.get("max_discovery_calls") is not None:
                command.extend(
                    ["--max-discovery-calls", str(model["max_discovery_calls"])]
                )
            write(folder / "server-command.json", command)
            with (folder / "server.log").open("w") as log:
                server = subprocess.Popen(
                    command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
                )
                try:
                    for _ in range(200):
                        if server.poll() is not None:
                            raise RuntimeError("Bank server exited before readiness")
                        try:
                            with urllib.request.urlopen(
                                f"http://127.0.0.1:{port}/health", timeout=0.3
                            ):
                                break
                        except OSError:
                            await asyncio.sleep(0.1)
                    else:
                        raise RuntimeError("Bank server readiness timeout")
                    client = PlatformClient(f"http://127.0.0.1:{port}")
                    original = client.open

                    async def capture(*args, **kwargs):
                        opened = await original(*args, **kwargs)
                        try:
                            actual_prompt = build_prompt(
                                policy=opened.policy,
                                ticket=opened.ticket,
                                mcp_urls=opened.mcp_urls,
                                max_calls=opened.max_calls,
                                max_errors=opened.max_errors,
                                max_discovery_calls=opened.max_discovery_calls,
                            )
                            expected = read(out / "surface-packet.json")
                            schemas = {}
                            async with client.connect_systems(opened) as systems:
                                for name, tool in systems.items():
                                    schemas[name] = (
                                        await tool.list_tools()
                                    ).model_dump(mode="json")
                            verify_live_surface(
                                expected,
                                opened.policy,
                                actual_prompt,
                                schemas,
                                spec.get("private_tokens", []),
                                configuration=session_configuration(opened),
                            )
                            (folder / "prompt.txt").write_text(actual_prompt)
                            (folder / "startup.txt").write_text(opened.policy)
                            write(folder / "schemas.json", schemas)
                            current(spec, out)
                            boundary_check(spec)
                            return opened
                        except Exception:
                            try:
                                await client.close(opened.token)
                            except Exception:
                                pass
                            raise

                    client.open = capture
                    try:
                        record = await run_one_attempt(
                            client=client,
                            task_id=spec["task_id"],
                            model=model["name"],
                            effort=model["effort"],
                            timeout_seconds=model["timeout_seconds"],
                            work_root=work,
                            mcp_layout="directory",
                            container_image=model["image"],
                            expected_image_id=boundary["image"],
                        )
                        # Persist before journal enrichment or server cleanup can fail.
                        write(folder / "record.json", record)
                        if "run" in record:
                            attach_persisted_journal(record, folder / "platform")
                    finally:
                        primary_error = sys.exc_info()[1]
                        try:
                            await client._http.aclose()
                        except Exception as error:
                            if primary_error is None:
                                raise
                            primary_error.add_note(
                                f"HTTP cleanup also failed: {error!r}"
                            )
                finally:
                    primary_error = sys.exc_info()[1]
                    try:
                        server.terminate()
                        try:
                            server.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            server.kill()
                            server.wait(timeout=5)
                    except Exception as error:
                        if primary_error is None:
                            raise
                        primary_error.add_note(f"Server cleanup also failed: {error!r}")
        write(folder / "record.json", record)
        current(spec, out)
        write(
            folder / "outcome.json",
            {"outcome": record_outcome(record), "fingerprint": fp},
        )
    except Exception as exc:
        if record is not None:
            write(folder / "record.json", record)
        write(
            folder / "outcome.json",
            {
                "outcome": "technical_failure",
                "error": str(exc),
                "error_notes": getattr(exc, "__notes__", []),
                "fingerprint": fp,
            },
        )
        raise
    finally:
        if os.getenv("BANKING_LANGFUSE_ENABLED", "").lower() == "true":
            # Telemetry is outside the model container and never changes grading.
            try:
                telemetry_record = folder / "record.json"
                if record is None:
                    telemetry_record = folder / "telemetry-failure.json"
                    write(
                        telemetry_record,
                        {"task_id": spec["task_id"], "technical_failure": True},
                    )
                exported = subprocess.run(
                    [
                        "uv",
                        "run",
                        "--script",
                        str(ROOT / "src/tau2/platform/langfuse_export.py"),
                        "--record",
                        str(telemetry_record),
                        "--model",
                        spec["model"]["name"],
                        "--metadata-json",
                        json.dumps(
                            {"fingerprint": fp, "attempt_index": len(previous) + 1}
                        ),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                write(folder / "langfuse-export.json", json.loads(exported.stdout))
            except Exception as telemetry_error:
                print(
                    f"Langfuse export failed: {type(telemetry_error).__name__}",
                    file=sys.stderr,
                )


def review(spec, out, index, evidence):
    """Attach an independent, explicit trajectory verdict; no keyword judge."""
    fp = current(spec, out)
    folder = out / "attempts" / f"{index:02}"
    report = read(evidence)
    if report["fingerprint"] != fp or report["record_sha256"] != sha(
        folder / "record.json"
    ):
        raise ValueError("Review is not bound to this candidate and attempt")
    if report["verdict"] not in [
        "substantive_failure",
        "false_zero",
        "technical_failure",
        "success",
    ]:
        raise ValueError("Unknown audit verdict")
    if not report["reason"] or not report["evidence_files"]:
        raise ValueError("Audit needs a reason and actual evidence files")
    hashes = hash_files([path_for(p) for p in report["evidence_files"]])
    if not hashes:
        raise ValueError("Empty audit evidence")
    if (
        report["verdict"] == "substantive_failure"
        and record_outcome(read(folder / "record.json")) != "unreviewed_zero"
    ):
        raise ValueError("Cannot relabel a timeout or success as a substantive failure")
    write(
        folder / "review.json",
        {**report, "evidence_hashes": hashes, "review_file_sha256": sha(evidence)},
    )
    write(folder / "outcome.json", {"outcome": report["verdict"], "fingerprint": fp})


def compress_verified(path):
    """Delete an original log only after lossless compression and hash verification."""
    path = Path(path)
    target = path.with_suffix(path.suffix + ".gz")
    if target.exists():
        raise ValueError(f"Existing compressed evidence: {target}")
    digest = sha(path)
    with path.open("rb") as src, gzip.open(target, "wb") as dst:
        shutil.copyfileobj(src, dst)
    with gzip.open(target, "rb") as stream:
        if hashlib.sha256(stream.read()).hexdigest() != digest:
            raise ValueError("Compressed evidence differs")
    before = path.stat().st_size
    path.unlink()
    return {
        "path": str(target),
        "sha256": digest,
        "before_bytes": before,
        "after_bytes": target.stat().st_size,
    }


def cleanup(out):
    if any(a["outcome"] == "unfinished" for a in attempts(out)):
        raise ValueError("An unfinished attempt must be inspected before cleanup")
    rows = []
    for log in out.glob("attempts/*/server.log"):
        rows.append(compress_verified(log))
    temp = out / "check-tmp"
    if temp.exists():
        shutil.rmtree(temp)
    write(
        out / f"cleanup-{time.time_ns()}.json",
        {"operations": rows, "raw_attempts_retained": True},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=[
            "seal",
            "validate",
            "surface",
            "_surface",
            "probe",
            "review",
            "status",
            "cleanup",
        ],
    )
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    spec = read(args.spec) if args.phase == "seal" else read(out / "spec.json")
    if args.phase == "seal":
        seal(spec, out)
    elif args.phase == "validate":
        validate(spec, out)
    elif args.phase == "surface":
        surface(spec, out)
    elif args.phase == "_surface":
        surface_child(spec, out)
    elif args.phase == "probe":
        asyncio.run(probe(spec, out))
    elif args.phase == "review":
        review(spec, out, args.attempt, args.evidence)
    elif args.phase == "cleanup":
        cleanup(out)
    previous = attempts(out)
    print(
        json.dumps(
            {
                "phase": args.phase,
                "decision": decision(previous),
                "attempts": previous,
                "artifact_bytes": sum(
                    p.stat().st_size for p in out.rglob("*") if p.is_file()
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
