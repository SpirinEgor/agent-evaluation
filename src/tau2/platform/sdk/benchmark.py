"""Execution-only SDK bridge. Never imports task collectors or graders."""

import hashlib
from copy import deepcopy
from typing import Any

from platform_sdk import (
    Action,
    ActionResult,
    Benchmark,
    RunContext,
    TaskInfo,
    TaskStart,
    ToolCall,
)
from platform_sdk.tools import ToolSpec

from tau2.data_model.tasks import Task
from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.bank_directory import directory_tools
from tau2.platform.session import PlatformSession, SessionStatus

SCENARIO_FIELDS = {"id", "initial_state", "max_bank_calls"}


class BankingBenchmark(Benchmark):
    # Reuse the exact bank schemas, including additionalProperties semantics.
    # SDK decorators would regenerate a different schema and discard bad calls.
    tools = {
        t.name: ToolSpec(t.name, t.description, deepcopy(t.input_schema))
        for t in directory_tools()
    }

    def __init__(self, task: TaskInfo, seed: int, ctx: RunContext) -> None:
        super().__init__(task, seed, ctx)
        scenario = task.input["scenario"]
        if set(scenario) - SCENARIO_FIELDS or scenario["id"] != task.id:
            raise ValueError("Invalid execution scenario projection")
        runtime_task = Task.model_validate(
            {**scenario, "user_scenario": {"instructions": ""}}
        )
        namespace = hashlib.sha256(f"banking-sdk-v1:{task.id}:{seed}".encode())
        self.session = PlatformSession(
            runtime_task,
            get_environment,
            mcp_layout="directory",
            token=namespace.hexdigest()[:32],
            now=ctx.clock.now,
            cursor_secret=namespace.digest(),
        )

    def on_start(self) -> TaskStart:
        return TaskStart(
            instructions=self.session.policy,
            opening=self.task.input["ticket"],
        )

    def on_action(self, action: Action) -> ActionResult:
        if not isinstance(action, ToolCall):
            raise ValueError("Banking exposes only the tools channel")
        if self.session.status is not SessionStatus.OPEN:
            raise ValueError("Action applied after episode termination")
        result = self.session.call(action.name, action.arguments)
        terminal = self.session.status is not SessionStatus.OPEN
        return ActionResult(
            content=result.content[0].text,
            done=terminal,
            domain_reason=self.session.status.value if terminal else None,
        )

    def report_state(self) -> dict[str, Any]:
        # Author/evaluator artifact, never returned to the agent.
        return {
            "version": "banking_sdk_v1",
            "seed": self.seed,
            "status": self.session.status.value,
            "db": self.session.env.tools.db.model_dump(mode="json"),
            "bank_calls": len(self.session.log),
            "discovery_calls": self.session.discovery_calls,
        }
