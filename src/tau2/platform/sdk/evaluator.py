"""SDK grading via the existing whole banking grader, not a second rubric."""

from collections import defaultdict
from datetime import datetime

from platform_sdk import (
    AggregateContext,
    EvalContext,
    EvalInputs,
    MetricInfo,
    RunContext,
    RunEvaluation,
    TaskInfo,
    ToolCall,
    Value,
    VirtualClock,
)

from tau2.domains.banking_ru.environment import prepare_task
from tau2.platform.sdk.benchmark import BankingBenchmark
from tau2.platform.session import SessionStatus


class BankingEvaluator:
    run_metrics = {
        "reward": MetricInfo("Итог DB × ACTION", "higher"),
        "DB": MetricInfo("Конечное банковское состояние", "higher"),
        "ACTION": MetricInfo("Допустимость наблюдаемой траектории", "higher"),
        "protocol_completion": MetricInfo("Получен агентский done", "higher"),
        "bank_calls": MetricInfo("Банковские вызовы", "lower"),
        "discovery_calls": MetricInfo("Вызовы discovery", "lower"),
    }
    experiment_metrics = {"success_rate": MetricInfo("Средний балл задач", "higher")}

    def evaluate(self, inputs: EvalInputs, ctx: EvalContext) -> dict[str, Value]:
        if inputs.report is None or inputs.journal is None:
            raise ValueError("Banking requires report and action_journal")
        report = inputs.report
        seed = report.get("seed")
        if type(seed) is not int or report.get("version") != "banking_sdk_v1":
            raise ValueError("Invalid banking state report")
        task = inputs.task
        info = TaskInfo(id=task.id, split=task.split, input=task.input)
        context = RunContext(
            llm={},
            agents={},
            clock=VirtualClock(
                datetime.fromisoformat(task.input.get("clock", "1970-01-01"))
            ),
        )
        bench = BankingBenchmark(info, seed, context)
        previous_seq, previous_turn = -1, 0
        applied_turns = set()
        for record in inputs.journal:
            if record.seq <= previous_seq or record.turn < previous_turn:
                raise ValueError("Journal is not ordered")
            previous_seq, previous_turn = record.seq, record.turn
            if not record.applied:
                continue
            # The benchmark declares parallel_tool_calls: reject. A result of
            # this turn is never an information source for another same-turn call.
            if record.turn in applied_turns or not isinstance(record.action, ToolCall):
                raise ValueError("Journal violates the tools-only sequential contract")
            applied_turns.add(record.turn)
            result = bench.on_action(record.action)
            if result.content != record.content:
                raise ValueError(f"Replay observation mismatch at action {record.seq}")
        if bench.report_state() != report:
            raise ValueError("Final state differs from the replayed journal")
        session = bench.session
        expected_reason = (
            session.status.value if session.status is not SessionStatus.OPEN else None
        )
        if inputs.domain_reason != expected_reason:
            raise ValueError("Domain stop reason differs from the banking state")
        if inputs.stop_reason not in {"done", "max_turns", "run_timeout"}:
            raise ValueError("Non-measurement outcome cannot be graded")
        if (inputs.stop_reason == "done") != (session.status is not SessionStatus.OPEN):
            raise ValueError("Platform stop reason differs from the banking state")
        session.task = prepare_task(
            {
                **task.input["scenario"],
                "user_scenario": {"instructions": ""},
                "evaluation_criteria": task.gold["evaluation_criteria"],
            }
        )
        reward = session.grade()
        ctx.log_artifact("banking-reward.json", reward.model_dump_json(indent=2))
        ctx.log_artifact(
            "banking-trajectory.json",
            session.to_simulation_run().model_dump_json(indent=2),
        )
        if reward.grading_status == "needs_review":
            raise ValueError(
                "Banking grading needs review; do not publish a numeric score"
            )
        breakdown = reward.reward_breakdown or {}
        return {
            "reward": reward.final_reward,
            "DB": breakdown.get("DB", 0.0),
            "ACTION": breakdown.get("ACTION", 0.0),
            "protocol_completion": session.status is SessionStatus.DONE,
            "bank_calls": len(session.log),
            "discovery_calls": session.discovery_calls,
        }

    def aggregate(
        self, results: list[RunEvaluation], ctx: AggregateContext
    ) -> dict[str, Value]:
        by_task = defaultdict(list)
        for result in results:
            by_task[result.task_id].append(result.values["reward"])
        complete = [
            sum(values) / len(values)
            for task_id, values in by_task.items()
            if not ctx.incomplete.get(task_id)
        ]
        return {
            "success_rate": sum(complete) / len(complete) if complete else 0.0,
            "tasks_complete": len(complete),
            "partial": ctx.partial,
        }
