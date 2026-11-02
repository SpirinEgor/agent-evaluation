"""Bounded, one-attempt Codex harness for banking_ru MCP sessions."""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from tau2.platform.client import PlatformClient


def classify_result(
    state: dict,
    result: dict,
    *,
    timed_out: bool,
    exit_code: int,
    closed_by_harness: bool,
    external_refusals: int = 0,
) -> str:
    """Classify an attempt without mistaking protocol cleanup for model failure."""
    if external_refusals:
        return "codex_refusal_fail"
    if timed_out:
        return "invalid_timeout"
    if (closed_by_harness or state["status"] != "done") and not result.get(
        "business_process_only", False
    ):
        return "invalid_missing_done"
    if exit_code != 0:
        return "invalid_harness"
    if result.get("grading_status") == "needs_review":
        return "needs_review"
    return "pass" if result["reward"] == 1.0 else "fail"


async def run_batch(
    task_ids: list[str],
    concurrency: int,
    run_one: Callable[[str], Awaitable[dict | str]],
) -> list[dict | str]:
    """Run independent tasks with an explicit upper bound on concurrency."""
    if not 1 <= concurrency <= 4:
        raise ValueError("concurrency must be between 1 and 4")

    gate = asyncio.Semaphore(concurrency)

    async def guarded(task_id: str) -> dict | str:
        async with gate:
            return await run_one(task_id)

    return await asyncio.gather(*(guarded(task_id) for task_id in task_ids))


def build_codex_command(
    *,
    model: str,
    effort: str,
    mcp_urls: dict[str, str],
    workdir: Path,
    auditor_roots: tuple[Path, ...] = (),
) -> list[str]:
    """Return the full-access invocation for one already-open MCP session.

    ``auditor_roots`` remains for caller compatibility; full access does not
    enforce host path denials. The execution environment must isolate them.
    """
    command = [
        "codex",
        "exec",
        "--model",
        model,
        "--config",
        f"model_reasoning_effort={json.dumps(effort)}",
    ]
    # Auditor files and ancestor AGENTS.md must never reach the model. All
    # business reads go through the allowlisted banking MCP corpus instead.
    overrides = {
        "project_doc_max_bytes": 0,
        "web_search": "disabled",
        "features.skip_host_skill_discovery": True,
        "features.shell_tool": False,
        "features.shell_snapshot": False,
        # Current Codex transports MCP through this isolated orchestration
        # runtime; disabling it also removes the banking tools.
        "features.code_mode_host": True,
        "features.view_image": False,
        "features.image_generation": False,
        "features.workspace_dependencies": False,
        "features.apps": False,
        "features.plugins": False,
        "features.browser_use": False,
        "features.in_app_browser": False,
        "features.computer_use": False,
        "features.multi_agent": False,
        "features.hooks": False,
        "features.memories": False,
    }
    for key, value in overrides.items():
        command.extend(["--config", f"{key}={json.dumps(value)}"])
    # Full access is the project launch policy. Isolation is provided by the
    # bank's MCP allowlist and tool surface, not by Codex approval or sandbox.
    command.append("--dangerously-bypass-approvals-and-sandbox")
    # skip_host_skill_discovery alone does not suppress the CLI's skill catalog.
    roots = {Path.home() / ".codex/skills", Path.home() / ".agents/skills"}
    for parent in (workdir.resolve(), *workdir.resolve().parents):
        roots.update({parent / ".codex/skills", parent / ".agents/skills"})
    skills = {
        path.resolve()
        for root in roots
        if root.is_dir()
        for path in root.rglob("SKILL.md")
    }
    skills_toml = (
        "["
        + ",".join(
            "{path=" + json.dumps(str(path)) + ",enabled=false}"
            for path in sorted(skills)
        )
        + "]"
    )
    command.extend(["--config", "skills.config=" + skills_toml])
    for name, url in mcp_urls.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"Invalid MCP system name: {name}")
        command.extend(["--config", f"mcp_servers.{name}.url={json.dumps(url)}"])
    return command + [
        "--ignore-user-config",
        "--ephemeral",
        "--json",
        "--skip-git-repo-check",
        "-C",
        str(workdir),
        "-",
    ]


def build_prompt(
    *,
    policy: str,
    ticket: str,
    mcp_urls: dict[str, str] | None = None,
    max_calls: int | None = None,
    max_errors: int | None = None,
    max_discovery_calls: int | None = None,
) -> str:
    """Compose the entire visible contract for one isolated task attempt."""
    return "\n\n".join(
        [
            (
                "Вы — сотрудник банка в симуляции. Работайте с датой и состоянием "
                "выданного банковского сценария: системная дата компьютера не "
                "является датой банковской среды. Решите обращение через выданные MCP "
                "банковских систем. Не используйте "
                "никаких других инструментов или источников."
            ),
            "Доступны bank_directory для поиска систем и инструментов и открытия схем, "
            "bank_call — для вызова открытой операции. Операция done вызывается через bank_call.",
            (
                f"Ресурсный бюджет: максимум {max_calls} банковских вызовов; "
                + (
                    f"максимум {max_errors} ошибок. "
                    if max_errors and max_errors > 0
                    else "ошибки без отдельного лимита. "
                )
                + (
                    f"Discovery учитывается отдельно: максимум {max_discovery_calls} вызовов. "
                    if max_discovery_calls is not None
                    else "Discovery учитывается отдельно; числовой лимит не задан. "
                )
                + "Завершите работу через done до исчерпания бюджета."
                if max_calls is not None
                else ""
            ),
            ticket,
            (
                "Когда выполните необходимые действия или решите, что "
                "дальше действовать нельзя, вызовите операцию `done` через bank_call."
            ),
        ]
    )


@contextmanager
def temporary_run_directory(**kwargs):
    """Keep the original failure/evidence if directory removal also fails."""
    directory = tempfile.TemporaryDirectory(**kwargs)
    try:
        yield directory.name
    finally:
        primary_error = sys.exc_info()[1]
        try:
            directory.cleanup()
        except Exception as error:
            if primary_error is None:
                raise
            primary_error.add_note(
                f"Temporary directory cleanup also failed: {error!r}"
            )


async def finish_attempt(client, opened, state: dict) -> dict:
    """Collect a result and close the session without laundering an open run.

    An open session must be closed before the platform exposes its reward.
    Keep its original state and protocol outcome independently of that reward.
    Business-process grading may accept completed work without model done.
    """
    closed_by_harness = state["status"] == "open"
    try:
        if closed_by_harness:
            async with client.connect(opened, opened.server_for("done")) as mcp:
                if opened.mcp_layout == "directory":
                    calls = [
                        (
                            "bank_directory",
                            {
                                "operation": "open_tool",
                                "system_id": "support",
                                "tool_name": "done",
                            },
                        ),
                        (
                            "bank_call",
                            {
                                "system_id": "support",
                                "tool_name": "done",
                                "arguments": {},
                            },
                        ),
                    ]
                else:
                    calls = [("done", {})]
                for name, arguments in calls:
                    response = await mcp.call_tool(name, arguments)
                    if response.is_error:
                        raise RuntimeError(f"Harness completion failed at {name}")
        result = await client.result(state["token"])
        return {
            "closed_by_harness": closed_by_harness,
            "state_before_cleanup": dict(state),
            "result": result,
        }
    finally:
        primary_error = sys.exc_info()[1]
        try:
            await client.close(state["token"])
        except Exception as error:
            if primary_error is None:
                raise
            primary_error.add_note(f"Session cleanup also failed: {error!r}")


async def invoke_codex(command: list[str], prompt: str, timeout_seconds: float) -> dict:
    """Run one non-interactive Codex process and retain its complete transcript."""
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )
    timed_out = False
    observations = []
    captured = {"stdout": [], "stderr": []}

    async def read_output(channel, stream):
        while line := await stream.readline():
            captured[channel].append(line)
            observations.append(
                dict(
                    channel=channel,
                    line=len(captured[channel]),
                    observed_at=datetime.now(UTC).isoformat(),
                )
            )

    process.stdin.write(prompt.encode("utf-8"))
    await process.stdin.drain()
    process.stdin.close()
    readers = asyncio.gather(
        read_output("stdout", process.stdout),
        read_output("stderr", process.stderr),
        process.wait(),
    )
    try:
        await asyncio.wait_for(asyncio.shield(readers), timeout=timeout_seconds)
    except TimeoutError:
        timed_out = True
        process.kill()
        await readers
    return {
        "pid": process.pid,
        "command": command,
        "prompt": prompt,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout": b"".join(captured["stdout"]).decode("utf-8", errors="replace"),
        "stderr": b"".join(captured["stderr"]).decode("utf-8", errors="replace"),
        "output_observations": observations,
    }


def verify_container_image(inspected: dict, expected_image_id: str) -> None:
    """Bind execution to the immutable image verified by preflight."""
    if inspected.get("Image") != expected_image_id:
        raise ValueError("Container image differs from the verified image")


async def invoke_isolated_codex(
    command: list[str],
    prompt: str,
    timeout_seconds: float,
    image: str = "banking-codex-isolated:0.166.0-alpha.5.1",
    policy: str = "",
    *,
    expected_image_id: str | None = None,
    inference_forward=None,
) -> dict:
    """Run full-access Codex in a fresh container without any host mounts."""
    spec = importlib.util.spec_from_file_location(
        "banking_codex_container", Path(__file__).with_name("codex_container.py")
    )
    container = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(container)
    name = "banking-codex-" + uuid.uuid4().hex
    run = None
    try:
        inspected = await asyncio.to_thread(
            container.prepare_container, image, name, policy
        )
        if expected_image_id is not None:
            verify_container_image(inspected, expected_image_id)
        run = await container.invoke_app_server(
            name,
            command,
            prompt,
            policy,
            timeout_seconds,
            inference_forward=inference_forward,
        )
        run["container"] = {
            "name": name,
            "id": inspected["Id"],
            "image_id": inspected["Image"],
            "create_command": container.create_command(image, name),
            "inspection": inspected,
            "inner_command": run["app_server_command"],
            "requested_command": command,
            "thread_start": run["thread_start"],
            "auth_delivery": "host-only subscription broker; no guest credentials",
        }
        return run
    except BaseException as error:
        if run is not None:
            error.partial_run = run
        raise
    finally:
        primary_error = sys.exc_info()[1]
        try:
            await asyncio.to_thread(container.remove_container, name)
        except Exception as error:
            if run is not None:
                error.partial_run = run
            if primary_error is None:
                raise
            primary_error.add_note(f"Container cleanup also failed: {error!r}")
        else:
            if run is not None and "container" in run:
                run["container"]["cleanup_verified"] = True


def collect_attempt_journal(
    stdout: str,
    stderr: str,
    mcp_events: list[dict],
    observations: list[dict] | None = None,
) -> dict:
    """Retain structured CLI attempts; never infer missing arguments from refusal prose.

    CLI item IDs and platform call IDs belong to different namespaces. Delivery
    is matched only on an unambiguous exact tool/argument pair. Code-mode calls
    not exposed as MCP items remain unknown rather than guessed from JavaScript.
    """
    observed_at = datetime.now(UTC).isoformat()
    rows = {}
    times = {(o["channel"], o["line"]): o["observed_at"] for o in observations or []}

    def protected(value, key=""):
        if key.lower() in {
            "credential",
            "code",
            "otp",
            "pan",
            "cvv",
            "expiry",
            "code_word",
        }:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {k: protected(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [protected(v, key) for v in value]
        # Free prose may carry secrets; preserve structured selectors/numbers.
        if key in {"question", "content", "body", "text", "reason"}:
            return "[REDACTED TEXT]"
        return value

    def rejection_source(message):
        if (
            "mcp tool call requires approval, but approval policy is never"
            in message.lower()
        ):
            return "approval_policy"
        if any(
            signature in message.lower()
            for signature in (
                "this action was rejected due to unacceptable risk",
                "tool execution rejected",
            )
        ):
            return "external_review"
        return None

    for number, line in enumerate(stdout.splitlines(), 1):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item", {})
        if item.get("type") == "agent_message" and item.get("model_refusal") is True:
            ident = f"model-refusal-{number}"
            rows[ident] = dict(
                id=item.get("id"),
                journal_id=ident,
                tool=None,
                server=None,
                timestamp=None,
                observed_at=times.get(("stdout", number), observed_at),
                arguments=None,
                external_decision="rejected",
                rejection_source="model_refusal",
                blocking_layer="model",
                rejection_observed_at=times.get(("stdout", number)),
                reason="[REDACTED TEXT]",
                delivered_to_mcp=False,
            )
            continue
        if item.get("type") != "mcp_tool_call":
            continue
        ident = item.get("id")
        if ident is None:
            continue
        row = rows.setdefault(
            ident,
            dict(
                id=ident,
                tool=None,
                server=None,
                timestamp=None,
                observed_at=times.get(("stdout", number), observed_at),
                arguments=None,
                external_decision="unknown",
                rejection_source=None,
                blocking_layer=None,
                rejection_observed_at=None,
                reason=None,
                delivered_to_mcp=None,
            ),
        )
        row["tool"] = item.get("tool", row["tool"])
        row["server"] = item.get("server", row["server"])
        row["timestamp"] = item.get("timestamp", row["timestamp"])
        if "arguments" in item:
            row["arguments"] = protected(item["arguments"])
            matches = [
                m
                for m in mcp_events
                if m.get("original_name", m.get("canonical_name")) == row["tool"]
                and m.get("arguments") == item["arguments"]
            ]
            if len(matches) == 1:
                row["delivered_to_mcp"] = True
                row["mcp_call_id"] = matches[0].get("call_id")
        if item.get("result") is not None:
            row["delivered_to_mcp"] = True
            if item["result"].get("isError") is True:
                row["blocking_layer"] = "mcp_result"
        error = item.get("error")
        message = (
            error.get("message", "") if isinstance(error, dict) else str(error or "")
        )
        source = rejection_source(message)
        if source is not None:
            row["rejection_source"] = source
            row["blocking_layer"] = (
                "codex_approval" if source == "approval_policy" else "external_review"
            )
            row["rejection_observed_at"] = times.get(("stdout", number))
            row["reason"] = message
            row["external_decision"] = "rejected"
            if row["delivered_to_mcp"] is not True:
                row["delivered_to_mcp"] = False
    # Legacy code-mode stderr can expose only a refusal. Exact arguments, ID,
    # tool and actual event time are unavailable in that channel.
    stderr_lines = stderr.splitlines()
    for number, line in enumerate(stderr_lines, 1):
        # The signature is the event; an optional following explanation must
        # neither be required nor create a second observation of that event.
        source = rejection_source(line)
        if source is None:
            continue
        ident = f"unattributed-refusal-{len(rows)}"
        rows[ident] = dict(
            id=None,
            journal_id=ident,
            tool=None,
            server=None,
            timestamp=None,
            observed_at=times.get(("stderr", number), observed_at),
            arguments=None,
            external_decision="rejected",
            rejection_source=source,
            blocking_layer=(
                "codex_approval" if source == "approval_policy" else "external_review"
            ),
            rejection_observed_at=times.get(("stderr", number)),
            reason=(
                line + "\n" + stderr_lines[number]
                if number < len(stderr_lines)
                and "Reason:" in stderr_lines[number]
                and rejection_source(stderr_lines[number]) is None
                else line
            ),
            delivered_to_mcp=None,
        )
    return dict(
        attempts=list(rows.values()),
        external_refusals=sum(
            r["external_decision"] == "rejected" for r in rows.values()
        ),
        refusal_count_unit="observations; stdout/stderr linkage may be unknown",
        # Complete fields in observed items cannot establish that every
        # attempted call was intercepted, including calls blocked before MCP.
        argument_capture_status="unverified",
        argument_capture_complete=None,
        observed_argument_fields_complete=(
            all(r["arguments"] is not None for r in rows.values()) if rows else None
        ),
        boundary="CLI structured MCP items; code-mode pre-MCP argument capture is not confirmed",
    )


def broker_journal_verification(run):
    """Keep old CLI capture flags separate from verified host RPC provenance."""
    if run is None:
        return {"status": "unavailable"}
    evidence = run.get("broker_evidence")
    contract = run.get("request_contract")
    if evidence is None or contract is None:
        return {"status": "unavailable"}
    spec = importlib.util.spec_from_file_location(
        "banking_codex_container", Path(__file__).with_name("codex_container.py")
    )
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    try:
        verified = runtime.broker_module().verify_trajectory(
            evidence, contract, runtime.validate_model_request
        )
    except (ValueError, KeyError, TypeError):
        return {"status": "unverified"}
    return {"status": "verified", "scope": "host_authorized_bank_rpc", **verified}


def attach_persisted_journal(record: dict, results_dir: Path) -> None:
    """Bind CLI observations to the server-owned terminal session journal."""
    token = record.get("token")
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
        raise ValueError("Invalid platform session token")
    path = results_dir / f"{token}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("info", {}).get("mcp_events")
    if (
        payload.get("id") != token
        or payload.get("task_id") != record.get("task_id")
        or not isinstance(events, list)
        or not all(isinstance(event, dict) for event in events)
    ):
        raise ValueError("Persisted platform journal has wrong session provenance")
    run = record["run"]
    journal = collect_attempt_journal(
        run["stdout"], run["stderr"], events, run.get("output_observations", [])
    )
    if bool(journal["external_refusals"]) != bool(
        record.get("confirmed_external_refusal")
    ):
        raise ValueError("Refusal evidence changed while linking the platform journal")
    record["attempt_journal"] = journal
    record["platform_event_status"] = "linked"
    record["platform_event_path"] = "platform/" + path.name
    record["platform_event_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    record["event_observation"] = {
        "argument_capture_status": journal["argument_capture_status"],
        "argument_capture_complete": journal["argument_capture_complete"],
        "boundary": journal["boundary"],
        "unknown_cases_require_audit": True,
        "broker_trajectory": broker_journal_verification(run),
    }


async def run_one_attempt(
    *,
    client: PlatformClient,
    task_id: str,
    model: str,
    effort: str,
    timeout_seconds: float,
    work_root: Path,
    mcp_layout: str = "directory",
    container_image: str = "banking-codex-isolated:0.166.0-alpha.5.1",
    expected_image_id: str | None = None,
) -> dict:
    """Open, run, classify and close one session exactly once."""
    if mcp_layout != "directory":
        raise ValueError("Supported banking harness requires directory layout")
    opened = await client.open(
        task_id, participant=f"codex-{model}-{effort}", mcp_layout=mcp_layout
    )
    run = None
    try:
        from tau2.domains.banking_ru.mcp_systems import SINGLE_SERVER

        if opened.mcp_layout != "directory" or set(opened.mcp_urls) != {SINGLE_SERVER}:
            raise ValueError("Platform did not provide the directory-only MCP surface")
        with temporary_run_directory(
            prefix=f"codex-{task_id}-", dir=work_root
        ) as temp_dir:
            (Path(temp_dir) / "AGENTS.md").write_text(opened.policy, encoding="utf-8")
            run = await invoke_isolated_codex(
                build_codex_command(
                    model=model,
                    effort=effort,
                    mcp_urls=opened.mcp_urls,
                    workdir=Path(temp_dir),
                ),
                build_prompt(
                    policy=opened.policy,
                    ticket=opened.ticket,
                    mcp_urls=opened.mcp_urls,
                    max_calls=opened.max_calls,
                    max_errors=opened.max_errors,
                    max_discovery_calls=opened.max_discovery_calls,
                ),
                timeout_seconds,
                container_image,
                opened.policy,
                **(
                    {"expected_image_id": expected_image_id}
                    if expected_image_id is not None
                    else {}
                ),
            )
        state = await client.state(opened.token)
        finish = await finish_attempt(client, opened, state)
        journal = collect_attempt_journal(
            run["stdout"],
            run["stderr"],
            finish["result"].get("mcp_events", []),
            run["output_observations"],
        )
        return {
            "task_id": task_id,
            "ticket": opened.ticket,
            "token": opened.token,
            "max_calls": opened.max_calls,
            "max_errors": opened.max_errors,
            "max_discovery_calls": opened.max_discovery_calls,
            "discovery": finish["result"].get("discovery", state.get("discovery")),
            "mcp_layout": opened.mcp_layout,
            "mcp_systems": list(opened.mcp_urls),
            "state": state,
            "closed_by_harness": finish["closed_by_harness"],
            "protocol_status": "done" if state["status"] == "done" else "missing_done",
            "result": finish["result"],
            "run": run,
            "attempt_journal": journal,
            "platform_event_status": "not_loaded",
            "bank_reward": finish["result"].get("reward"),
            "confirmed_external_refusal": bool(journal["external_refusals"]),
            "final_reward": 0.0
            if journal["external_refusals"]
            else finish["result"].get("reward"),
            "technical_failure": bool(run["timed_out"] or run["exit_code"] != 0),
            "event_observation": {
                "argument_capture_status": journal["argument_capture_status"],
                "argument_capture_complete": journal["argument_capture_complete"],
                "boundary": journal["boundary"],
                "unknown_cases_require_audit": True,
                "broker_trajectory": broker_journal_verification(run),
            },
            "attempt_reward": 0.0
            if journal["external_refusals"]
            else finish["result"].get("reward"),
            "classification": classify_result(
                state,
                finish["result"],
                timed_out=run["timed_out"],
                exit_code=run["exit_code"],
                closed_by_harness=finish["closed_by_harness"],
                external_refusals=journal["external_refusals"],
            ),
        }
    except Exception as error:
        saved_run = run if run is not None else getattr(error, "partial_run", None)
        # A failed harness cannot be passed off as an unsolved task.  Closing
        # the session is still best-effort so transient network faults do not
        # leak live MCP mounts on the platform.
        try:
            await client.close(opened.token)
        except Exception as cleanup_error:
            error.add_note(f"Session cleanup also failed: {cleanup_error!r}")
        return {
            "task_id": task_id,
            **({"run": saved_run} if saved_run is not None else {}),
            "token": opened.token,
            "classification": "invalid_harness",
            "error": repr(error),
            "error_notes": getattr(error, "__notes__", []),
            "bank_reward": None,
            "confirmed_external_refusal": None,
            "final_reward": None,
            "technical_failure": True,
            "event_observation": {
                "argument_capture_complete": None,
                "unknown_cases_require_audit": True,
                "broker_trajectory": broker_journal_verification(saved_run),
            },
        }


def write_json(path: Path, value: object) -> None:
    """Write a UTF-8 evidence artifact, creating only its explicit parent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_preflight():
    """Load the shared authoring gates even when imported outside the scripts path."""
    path = Path(__file__).with_name("banking_preflight.py")
    spec = importlib.util.spec_from_file_location("banking_preflight", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def async_main(args: argparse.Namespace) -> int:
    """Run a single attempt only from a sealed and validated local snapshot."""
    if getattr(args, "preflight_dir", None) is None:
        raise ValueError("A sealed preflight directory is required")
    out = args.preflight_dir.resolve()
    gates = load_preflight()
    spec = gates.read(out / "spec.json")
    await gates.probe(spec, out)
    previous = gates.attempts(out)
    print(
        json.dumps(
            {
                "preflight_dir": str(out),
                "decision": gates.decision(previous),
                "attempts": previous,
            },
            ensure_ascii=False,
        )
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    # The supported entrypoint also exposes the preparation, inspection and
    # audit phases; the probe path is gated identically in both entrypoints.
    if len(sys.argv) > 1 and sys.argv[1] == "preflight":
        sys.argv.pop(1)
        load_preflight().main()
        return 0
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
