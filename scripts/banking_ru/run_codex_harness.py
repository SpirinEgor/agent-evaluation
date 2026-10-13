"""Bounded, one-attempt Codex harness for banking_ru MCP sessions."""

import argparse
import asyncio
import json
import re
import tempfile
import time
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
) -> str:
    """Classify an attempt without mistaking protocol cleanup for model failure."""
    if timed_out:
        return "invalid_timeout"
    if closed_by_harness or state["status"] != "done":
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
    """Return the hermetic Codex invocation for one already-open MCP session."""
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
        "approval_policy": "never",
        "default_permissions": "banking_mcp_only",
        "permissions.banking_mcp_only.network.enabled": False,
    }
    for key, value in overrides.items():
        command.extend(["--config", f"{key}={json.dumps(value)}"])
    # apply_patch remains a built-in even with shell disabled. Deny local reads
    # of the home directory (Wiki, chats, skills) and the evaluator checkout;
    # only the fresh agent workspace is writable. MCP has its own allowlist.
    denied = {
        Path.home().resolve(),
        Path(__file__).resolve().parents[2],
        *(path.resolve() for path in auditor_roots),
    }
    filesystem = {
        "/": "read",
        ":workspace_roots": "write",
        **{str(path): "deny" for path in sorted(denied)},
    }
    filesystem_toml = (
        "{"
        + ",".join(f"{json.dumps(k)}={json.dumps(v)}" for k, v in filesystem.items())
        + "}"
    )
    command.extend(
        ["--config", "permissions.banking_mcp_only.filesystem=" + filesystem_toml]
    )
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
        "--skip-git-repo-check",
        "-C",
        str(workdir),
        "-",
    ]


def build_prompt(
    *, policy: str, ticket: str, mcp_urls: dict[str, str] | None = None
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
            ticket,
            (
                "Когда выполните необходимые действия или решите, что "
                "дальше действовать нельзя, обязательно вызовите инструмент `done`."
            ),
        ]
    )


async def finish_attempt(client, opened, state: dict) -> dict:
    """Collect a result and close the session without laundering an open run.

    The platform requires ``done`` before it will expose a reward.  When Codex
    omitted that call, the harness performs the minimal cleanup solely to
    retrieve diagnostics, and returns an explicit marker so callers classify
    the outcome as invalid rather than as a model failure.
    """
    closed_by_harness = state["status"] != "done"
    try:
        if closed_by_harness:
            async with client.connect(opened, opened.server_for("done")) as mcp:
                await mcp.call_tool("done", {})
        result = await client.result(state["token"])
        return {"closed_by_harness": closed_by_harness, "result": result}
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
    )
    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(prompt.encode("utf-8")), timeout=timeout_seconds
        )
    except TimeoutError:
        timed_out = True
        process.kill()
        stdout, stderr = await process.communicate()
    return {
        "pid": process.pid,
        "command": command,
        "prompt": prompt,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - started, 3),
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


async def run_one_attempt(
    *,
    client: PlatformClient,
    task_id: str,
    model: str,
    effort: str,
    timeout_seconds: float,
    work_root: Path,
    mcp_layout: str = "systems",
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
            run = await invoke_codex(
                build_codex_command(
                    model=model,
                    effort=effort,
                    mcp_urls=opened.mcp_urls,
                    workdir=Path(temp_dir),
                ),
                build_prompt(
                    policy=opened.policy, ticket=opened.ticket, mcp_urls=opened.mcp_urls
                ),
                timeout_seconds,
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
        }

    return {
        "task_id": task_id,
        "token": opened.token,
        "max_calls": opened.max_calls,
        "max_errors": opened.max_errors,
        "mcp_layout": opened.mcp_layout,
        "mcp_systems": list(opened.mcp_urls),
        "state": state,
        "result": finish["result"],
        "run": run,
        "classification": classify_result(
            state,
            finish["result"],
            timed_out=run["timed_out"],
            exit_code=run["exit_code"],
            closed_by_harness=finish["closed_by_harness"],
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
