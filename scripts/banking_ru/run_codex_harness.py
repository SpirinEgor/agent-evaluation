"""Bounded, one-attempt Codex harness for banking_ru MCP sessions."""

import argparse
import asyncio
import importlib.util
import json
import re
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
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
    return "luna_pass" if result["reward"] == 1.0 else "luna_fail"


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
            policy,
            (
                f"Ресурсный бюджет: максимум {max_calls} банковских вызовов; "
                + (
                    f"максимум {max_errors} ошибок. "
                    if max_errors and max_errors > 0
                    else "ошибки без отдельного лимита. "
                )
                + "Завершите работу через done до исчерпания бюджета."
                if max_calls is not None
                else ""
            ),
            ticket,
            (
                "Когда выполните необходимые действия или решите, что "
                "дальше действовать нельзя, обязательно вызовите инструмент `done`."
            ),
        ]
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
                await mcp.call_tool("done", {})
        result = await client.result(state["token"])
        return {
            "closed_by_harness": closed_by_harness,
            "state_before_cleanup": dict(state),
            "result": result,
        }
    finally:
        await client.close(state["token"])


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


async def invoke_isolated_codex(
    command: list[str],
    prompt: str,
    timeout_seconds: float,
    image: str = "banking-codex-isolated:0.162.0-alpha.17.2",
    policy: str = "",
) -> dict:
    """Run full-access Codex in a fresh container without any host mounts."""
    spec = importlib.util.spec_from_file_location(
        "banking_codex_container", Path(__file__).with_name("codex_container.py")
    )
    container = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(container)
    name = "banking-codex-" + uuid.uuid4().hex
    inner = list(command)
    for i, arg in enumerate(inner):
        if arg.startswith("mcp_servers.") and ".url=" in arg:
            inner[i] = arg.replace("http://127.0.0.1:", "http://host.docker.internal:")
        elif arg.startswith("skills.config="):
            inner[i] = (
                'skills.config=[{path="/root/.codex/skills/.system/imagegen/SKILL.md",enabled=false},{path="/root/.codex/skills/.system/openai-docs/SKILL.md",enabled=false},{path="/root/.codex/skills/.system/review-agent/SKILL.md",enabled=false},{path="/root/.codex/skills/.system/skill-creator/SKILL.md",enabled=false},{path="/root/.codex/skills/.system/skill-installer/SKILL.md",enabled=false}]'
            )
        elif arg == "-C":
            inner[i + 1] = "/workspace"
    try:
        inspected = await asyncio.to_thread(
            container.prepare_container, image, name, policy
        )
        run = await invoke_codex(
            ["docker", "exec", "-i", name, *inner], prompt, timeout_seconds
        )
        run["container"] = {
            "name": name,
            "id": inspected["Id"],
            "image_id": inspected["Image"],
            "create_command": container.create_command(image, name),
            "inspection": inspected,
            "inner_command": inner,
            "auth_delivery": "explicit temporary copy to tmpfs; no host mount",
        }
        return run
    finally:
        await asyncio.to_thread(container.remove_container, name)


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
                if m.get("canonical_name") == row["tool"]
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


async def run_one_attempt(
    *,
    client: PlatformClient,
    task_id: str,
    model: str,
    effort: str,
    timeout_seconds: float,
    work_root: Path,
    mcp_layout: str = "systems",
    container_image: str = "banking-codex-isolated:0.162.0-alpha.17.2",
) -> dict:
    """Open, run, classify and close one session exactly once."""
    opened = await client.open(
        task_id, participant=f"codex-{model}-{effort}", mcp_layout=mcp_layout
    )
    run: dict
    try:
        with tempfile.TemporaryDirectory(
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
                ),
                timeout_seconds,
                container_image,
                opened.policy,
            )
        state = await client.state(opened.token)
        finish = await finish_attempt(client, opened, state)
    except Exception as error:
        # A failed harness cannot be passed off as an unsolved task.  Closing
        # the session is still best-effort so transient network faults do not
        # leak live MCP mounts on the platform.
        try:
            await client.close(opened.token)
        except Exception:
            pass
        return {
            "task_id": task_id,
            "token": opened.token,
            "classification": "invalid_harness",
            "error": repr(error),
            "bank_reward": None,
            "confirmed_external_refusal": None,
            "final_reward": None,
            "technical_failure": True,
            "event_observation": {
                "argument_capture_complete": None,
                "unknown_cases_require_audit": True,
            },
        }

    journal = collect_attempt_journal(
        run["stdout"],
        run["stderr"],
        finish["result"].get("mcp_events", []),
        run["output_observations"],
    )
    return {
        "task_id": task_id,
        "token": opened.token,
        "max_calls": opened.max_calls,
        "max_errors": opened.max_errors,
        "mcp_layout": opened.mcp_layout,
        "mcp_systems": list(opened.mcp_urls),
        "state": state,
        "closed_by_harness": finish["closed_by_harness"],
        "protocol_status": "done" if state["status"] == "done" else "missing_done",
        "result": finish["result"],
        "run": run,
        "attempt_journal": journal,
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


def write_json(path: Path, value: object) -> None:
    """Write a UTF-8 evidence artifact, creating only its explicit parent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def async_main(args: argparse.Namespace) -> int:
    work_root = args.work_root.resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    client = PlatformClient(args.base_url)
    started_at = datetime.now(UTC).isoformat()
    try:
        records = await run_batch(
            args.tasks,
            args.concurrency,
            lambda task_id: run_one_attempt(
                client=client,
                task_id=task_id,
                model=args.model,
                effort=args.effort,
                timeout_seconds=args.timeout,
                work_root=work_root,
                mcp_layout=args.mcp_layout,
                container_image=args.container_image,
            ),
        )
    finally:
        await client._http.aclose()

    report = {
        "started_at": started_at,
        "model": args.model,
        "reasoning_effort": args.effort,
        "mcp_layout": args.mcp_layout,
        "attempts_per_task": 1,
        "concurrency": args.concurrency,
        "timeout_seconds": args.timeout,
        "records": records,
    }
    write_json(args.out, report)
    for record in records:
        write_json(args.artifact_dir / f"{record['task_id']}.json", record)
    counts: dict[str, int] = {}
    for record in records:
        label = record["classification"]
        counts[label] = counts.get(label, 0) + 1
    print(json.dumps({"out": str(args.out), "counts": counts}, ensure_ascii=False))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tasks", nargs="+", required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--effort", default="low")
    parser.add_argument(
        "--mcp-layout", choices=["single", "systems"], default="systems"
    )
    parser.add_argument(
        "--container-image", default="banking-codex-isolated:0.162.0-alpha.17.2"
    )
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--work-root", type=Path, default=Path("/tmp"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
