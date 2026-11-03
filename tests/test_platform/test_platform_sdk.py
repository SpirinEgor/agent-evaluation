"""The real optional platform SDK, current Bank 2; no provider calls."""

# ruff: noqa: E402 -- optional SDK dependencies must be checked before imports.

import asyncio
import importlib.util
import json
import os
import runpy
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import pytest

sdk = pytest.importorskip(
    "platform_sdk", reason="Install the platform SDK to test integration"
)
pytest.importorskip("platform_harness")

from platform_harness.context import build_context
from platform_harness.server import WireService, build_server

from tau2.domains.banking_ru.environment import get_tasks
from tau2.domains.banking_ru.mcp_systems import TOOL_OWNERS
from tau2.environment.reference_bindings import reference_arguments
from tau2.platform.a2a import build_app
from tau2.platform.sdk.benchmark import BankingBenchmark
from tau2.platform.sdk.evaluator import BankingEvaluator
from tau2.platform.sdk.tasks import collect


@pytest.fixture(scope="module")
def task():
    return next(t for t in collect() if t.id == "Bank 2")


def benchmark(task, seed=0):
    info = sdk.task_info_from_payload(sdk.task_input_projection(task))
    return BankingBenchmark(info, seed, build_context(info, {}))


def path(task, extra=None):
    """Author-only scripted solver, with results bound through the public API."""
    criteria = next(t for t in get_tasks() if t.id == task.id).evaluation_criteria
    received, opened = {}, set()
    for action in criteria.actions:
        owner = TOOL_OWNERS[action.name]
        if action.name not in opened:
            yield sdk.ToolCall(
                "bank_directory",
                {
                    "operation": "open_tool",
                    "system_id": owner,
                    "tool_name": action.name,
                },
            )
            opened.add(action.name)
        arguments = reference_arguments(
            action, action.arguments, criteria.result_argument_bindings, received
        )
        content = yield sdk.ToolCall(
            "bank_call",
            {
                "system_id": owner,
                "tool_name": action.name,
                "arguments": arguments,
            },
        )
        received[action.action_id] = content
    if extra is not None:
        yield sdk.ToolCall(
            "bank_directory",
            {
                "operation": "open_tool",
                "system_id": extra.arguments["system_id"],
                "tool_name": extra.arguments["tool_name"],
            },
        )
        yield extra
    yield sdk.ToolCall(
        "bank_directory",
        {
            "operation": "open_tool",
            "system_id": "support",
            "tool_name": "done",
        },
    )
    yield sdk.ToolCall(
        "bank_call",
        {
            "system_id": "support",
            "tool_name": "done",
            "arguments": {},
        },
    )


def execute(bench, plan):
    journal = []
    content = None
    while True:
        try:
            action = plan.send(content)
        except StopIteration:
            break
        result = bench.on_action(action)
        content = result.content
        journal.append(
            sdk.ActionRecord(len(journal) + 1, len(journal) + 1, action, content)
        )
    return journal


class Sink:
    def __init__(self):
        self.artifacts = {}

    def log_artifact(self, name, content):
        self.artifacts[name] = content


def grade(task, bench, journal, stop="done"):
    report = bench.report_state()
    inputs = sdk.EvalInputs(
        task,
        stop,
        None if report["status"] == "open" else report["status"],
        report,
        journal,
    )
    sink = Sink()
    return BankingEvaluator().evaluate(inputs, sink), inputs, sink


def test_projection_schemas_and_deterministic_pagination(task):
    assert len(collect()) == 61
    assert set(task.input["scenario"]) == {"id", "initial_state", "max_bank_calls"}
    assert not hasattr(
        sdk.task_info_from_payload(sdk.task_input_projection(task)), "gold"
    )
    a, b = benchmark(task, 17), benchmark(task, 17)
    assert set(a.tools) == {"bank_directory", "bank_call"}
    assert a.on_start().opening == task.input["ticket"]
    assert "evaluation_criteria" not in a.on_start().instructions
    action = sdk.ToolCall("bank_directory", {"operation": "list_systems"})
    x, y = a.on_action(action), b.on_action(action)
    assert x == y and a.report_state() == b.report_state()
    cursor = json.loads(x.content)["next_cursor"]
    assert cursor
    next_page = sdk.ToolCall(
        "bank_directory", {"operation": "list_systems", "cursor": cursor}
    )
    assert a.on_action(next_page) == b.on_action(next_page)
    assert "INVALID_CURSOR" in benchmark(task, 18).on_action(next_page).content


def test_reference_and_tampered_report_journal(task):
    bench = benchmark(task)
    journal = execute(bench, path(task))
    values, inputs, sink = grade(task, bench, journal)
    assert values["reward"] == values["DB"] == values["ACTION"] == 1
    assert values["bank_calls"] <= 50 and values["protocol_completion"]
    assert set(sink.artifacts) == {"banking-reward.json", "banking-trajectory.json"}
    bad_report = deepcopy(inputs.report)
    bad_report["db"]["customers"].clear()
    with pytest.raises(ValueError, match="Final state"):
        BankingEvaluator().evaluate(replace(inputs, report=bad_report), Sink())
    bad_journal = [replace(journal[0], content="fabricated"), *journal[1:]]
    with pytest.raises(ValueError, match="observation mismatch"):
        BankingEvaluator().evaluate(replace(inputs, journal=bad_journal), Sink())


def test_noop_missing_done_and_recovery(task):
    bench = benchmark(task)
    values, _, _ = grade(task, bench, [], stop="max_turns")
    assert values["reward"] == values["DB"] == values["ACTION"] == 0
    assert not values["protocol_completion"]
    noop = iter(
        [
            sdk.ToolCall(
                "bank_directory",
                {"operation": "open_tool", "system_id": "support", "tool_name": "done"},
            ),
            sdk.ToolCall(
                "bank_call",
                {"system_id": "support", "tool_name": "done", "arguments": {}},
            ),
        ]
    )

    # iterators do not support result-dependent send; this path needs no results.
    def noop_plan():
        for action in noop:
            yield action

    noop_journal = execute(bench, noop_plan())
    assert grade(task, bench, noop_journal)[0]["reward"] == 0
    assert bench.report_state()["status"] == "done"
    bench = benchmark(task)

    def recovery_plan():
        yield sdk.ToolCall("bank_call", {"invented": True})
        yield from path(task)

    journal = execute(bench, recovery_plan())
    values, inputs, _ = grade(task, bench, journal)
    assert values["reward"] == 1
    with pytest.raises(ValueError, match="sequential contract"):
        BankingEvaluator().evaluate(
            replace(
                inputs, journal=[journal[0], replace(journal[1], turn=1), *journal[2:]]
            ),
            Sink(),
        )


def test_local_bank_budget_is_not_platform_turn_budget(task):
    bench = benchmark(task)
    bench.session.max_calls = 1
    execute(bench, path_for_find(task))
    assert bench.report_state()["status"] == "budget_exhausted"


@pytest.mark.parametrize("violation", ["foreign_data", "confidentiality"])
def test_sdk_preserves_safety_checks(task, violation):
    bench = benchmark(task)
    own = next(
        a.arguments["customer_id"]
        for a in next(
            t for t in get_tasks() if t.id == task.id
        ).evaluation_criteria.actions
        if a.name == "verify_identity"
    )
    customers = bench.session.env.tools.db.customers
    if violation == "foreign_data":
        extra = sdk.ToolCall(
            "bank_call",
            {
                "system_id": "crm",
                "tool_name": "get_customer_profile",
                "arguments": {"customer_id": next(c for c in customers if c != own)},
            },
        )
    else:
        extra = sdk.ToolCall(
            "bank_call",
            {
                "system_id": "support",
                "tool_name": "reply_to_ticket",
                "arguments": {
                    "customer_id": own,
                    "text": "Ваш OTP: " + customers[own].otp_code,
                },
            },
        )
    journal = execute(bench, path(task, extra))
    values, _, _ = grade(task, bench, journal)
    assert values["ACTION"] == values["reward"] == 0


def test_generated_bundle_excludes_gold_and_runs(task, tmp_path):
    source = os.environ.get("EVAL_PLATFORM_REPO")
    if not source:
        pytest.skip("Set EVAL_PLATFORM_REPO to verify the packaged execution boundary")
    root = Path(__file__).resolve().parents[2]
    builder = runpy.run_path(str(root / "scripts/banking_ru/build_platform_bundle.py"))
    bundle = tmp_path / "bundle"
    builder["build_bundle"](Path(source), bundle)
    assert not (bundle / "execute/src/tau2/evaluator").exists()
    assert not (bundle / "execute/src/tau2/platform/sdk/tasks.py").exists()
    assert not (bundle / "execute/data/tau2/domains/banking_ru/tasks").exists()
    assert (bundle / "evaluate/data/tau2/domains/banking_ru/tasks").exists()
    with pytest.raises(ValueError, match="existing artifacts"):
        builder["build_bundle"](Path(source), bundle)
    task_path = tmp_path / "input.json"
    task_path.write_text(json.dumps(sdk.task_input_projection(task)))
    # Match the Dockerfile's /app layout and the platform's PYTHONPATH override.
    # Do not add a source path: that masked a real image import failure.
    image = tmp_path / "execution-app"
    shutil.copytree(bundle / "execute/src", image)
    shutil.copy2(bundle / "platform_benchmark.py", image)
    env = {
        **os.environ,
        "PYTHONPATH": str(
            Path(source) / "platform/external_contracts/platform-harness"
        ),
        "TAU2_DATA_DIR": str(bundle / "execute/data"),
        "PLATFORM_CONTRACT_VERSION": "2",
        "PLATFORM_TASK_PATH": str(task_path),
        "PLATFORM_SEED": "0",
    }
    # A fresh interpreter loads only the shipped runtime; no root tau2 imports.
    script = (
        "import sys; from pathlib import Path; "
        "from platform_sdk import set_package_root; set_package_root(Path.cwd()); "
        "from platform_harness.main import build_service, read_serve_environment; "
        "s=build_service(read_serve_environment()); s.on_start(); "
        "assert not any(n.startswith('tau2.evaluator') for n in sys.modules)"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=image,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    evaluation_image = tmp_path / "evaluation-app"
    shutil.copytree(bundle / "evaluate/src", evaluation_image)
    for name in ("platform_tasks.py", "platform_evaluator.py"):
        shutil.copy2(bundle / name, evaluation_image)
    eval_env = {
        **env,
        "TAU2_DATA_DIR": str(bundle / "evaluate/data"),
    }
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from platform_tasks import COLLECT; from platform_evaluator import EVALUATOR; "
                "assert len(COLLECT()) == 61; assert 'DB' in EVALUATOR.run_metrics"
            ),
        ],
        cwd=evaluation_image,
        env=eval_env,
        check=True,
        capture_output=True,
        text=True,
    )


def path_for_find(task):
    yield sdk.ToolCall(
        "bank_directory",
        {"operation": "open_tool", "system_id": "crm", "tool_name": "find_customer"},
    )
    yield sdk.ToolCall(
        "bank_call",
        {
            "system_id": "crm",
            "tool_name": "find_customer",
            "arguments": {"phone": "+7 926 118-77-30"},
        },
    )


@contextmanager
def wire(service):
    server = build_server(service, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


@pytest.mark.parametrize("plan_name", ["reference", "noop", "limit"])
def test_real_platform_coordinator_through_a2a(task, tmp_path, plan_name):
    source = os.environ.get("EVAL_PLATFORM_REPO")
    if not source:
        pytest.skip("Set EVAL_PLATFORM_REPO for the actual platform coordinator check")
    sys.path.insert(0, str(Path(source) / "platform"))
    from evalplatform.common.artifacts import ArtifactStore
    from evalplatform.common.buffer import CallBuffer, read_calls
    from evalplatform.common.calls import ATTR_RUN_ID, build_tracer_provider
    from evalplatform.common.journal import select_action_journal
    from evalplatform.runner.a2a import A2AAgent
    from evalplatform.runner.bench import BenchHandle
    from evalplatform.runner.turn_based import TurnBasedRunner
    from evalplatform.runner.types import RunRules
    from platform_harness.eval_service import EvalService, EvaluateJob

    # Use the same native HTTP test server as the A2A adapter checks.
    helper_path = Path(__file__).with_name("test_codex_a2a.py")
    spec = importlib.util.spec_from_file_location("sdk_a2a_http", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    async def engine(exchange):
        assert exchange.setup["instructions"] == bench.on_start().instructions
        assert exchange.setup["opening"] == task.input["ticket"]
        assert {x["function"]["name"] for x in exchange.setup["tools"]} == {
            "bank_directory",
            "bank_call",
        }
        if plan_name == "reference":
            plan = path(task)
        elif plan_name == "noop":

            def done():
                yield sdk.ToolCall(
                    "bank_directory",
                    {
                        "operation": "open_tool",
                        "system_id": "support",
                        "tool_name": "done",
                    },
                )
                yield sdk.ToolCall(
                    "bank_call",
                    {"system_id": "support", "tool_name": "done", "arguments": {}},
                )

            plan = done()
        else:
            plan = path_for_find(task)
        text = None
        while True:
            try:
                action = plan.send(text)
            except StopIteration:
                # run_end must end the runtime, rather than premature model completion.
                await asyncio.Future()
            result = await exchange.call_tool(action.name, action.arguments)
            text = result.content[0].text

    bench = benchmark(task)

    async def run():
        store = ArtifactStore(tmp_path, "run")
        rules = RunRules(1 if plan_name == "limit" else 500, 60, ("tools",), "reject")
        with CallBuffer(tmp_path / "calls.jsonl") as buffer:
            provider = build_tracer_provider(buffer, {ATTR_RUN_ID: str(uuid4())})
            tracer = provider.get_tracer("banking-sdk-stub")
            app = build_app(
                endpoint="http://127.0.0.1/a2a",
                engine=engine,
                artifacts_dir=tmp_path / "agent",
            )
            with wire(WireService(bench, contract_version=2)) as url:
                async with helper.serve(app) as client:
                    agent = A2AAgent(
                        str(client.base_url).rstrip("/") + "/a2a",
                        rules=rules,
                        tracer=tracer,
                    )
                    try:
                        async with BenchHandle(
                            url, tracer=tracer, store=store
                        ) as handle:
                            runner = TurnBasedRunner(
                                bench=handle,
                                agent=agent,
                                rules=rules,
                                registry={
                                    name: asdict(t) for name, t in bench.tools.items()
                                },
                                tracer=tracer,
                                store=store,
                            )
                            outcome = await runner.run()
                            report = await handle.report_state()
                    finally:
                        await agent.__aexit__(None, None, None)
            provider.shutdown()
        rows = select_action_journal(read_calls(tmp_path / "calls.jsonl"), store)
        from platform_harness.wire import parse_action

        journal = [
            sdk.ActionRecord(
                r.seq, r.turn, parse_action(r.action), r.content, applied=r.applied
            )
            for r in rows
        ]
        inputs = sdk.EvalInputs(
            task, outcome.stop_reason, outcome.domain_reason, report, journal
        )
        service = EvalService(
            BankingEvaluator(), EvaluateJob(inputs), contract_version=2
        )
        # Execute the actual evaluation wire, not just a local function call.
        import httpx

        with wire(service) as url:
            async with httpx.AsyncClient() as client:
                response = await client.post(url + "/evaluate")
                response.raise_for_status()
                answer = response.json()
        expected = 1 if plan_name == "reference" else 0
        assert answer["values"]["reward"] == answer["values"]["DB"] == expected
        assert answer["values"]["protocol_completion"] == (plan_name != "limit")
        (tmp_path / "result.json").write_text(
            json.dumps(
                {"outcome": asdict(outcome), **answer}, ensure_ascii=False, indent=2
            )
        )

    asyncio.run(run())
