"""Aggregate Banking.ru competition metrics and rank a common task set."""

import math
from statistics import mean, median

from pydantic import BaseModel, Field, model_validator


class Attempt(BaseModel):
    """One reconciled, scored attempt using timestamps from the coordinator."""

    task_id: str
    reward: int = Field(ge=0, le=1)
    DB: int = Field(ge=0, le=1)
    ACTION: int = Field(ge=0, le=1)
    protocol_completion: bool
    critical_violation: bool
    progress: float = Field(ge=0, le=1)
    agent_tool_errors: int = Field(ge=0)
    valid_tool_calls: int = Field(ge=0)
    unclassified_tool_calls: int = Field(ge=0)
    bank_calls: int = Field(ge=0)
    discovery_calls: int = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def consistent_solution(self):
        if self.reward and (
            self.DB != 1
            or self.ACTION != 1
            or not self.protocol_completion
            or self.progress != 1
        ):
            raise ValueError(
                "A solved task needs DB=1, ACTION=1, done and full progress"
            )
        return self


class TaskBaseline(BaseModel):
    bank_calls: int = Field(ge=0)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)


def duration_statistics(values):
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return {"n": 0, "mean": None, "median": None, "p90": None, "p95": None}
    return {
        "n": count,
        "mean": mean(ordered),
        "median": median(ordered),
        "p90": ordered[math.ceil(0.90 * count) - 1],
        "p95": ordered[math.ceil(0.95 * count) - 1],
    }


def summarize(
    attempts: list[Attempt], baselines: dict[str, TaskBaseline] | None = None
):
    solved = [a for a in attempts if a.reward]
    unresolved = [a.progress for a in attempts if not a.reward]
    valid = sum(a.valid_tool_calls for a in attempts)
    errors = sum(a.agent_tool_errors for a in attempts)
    count = len(attempts)
    summary = {
        "tasks_scored": count,
        "solved": len(solved),
        "solve_rate": len(solved) / count if count else None,
        "DB_rate": mean(a.DB for a in attempts) if count else None,
        "ACTION_rate": mean(a.ACTION for a in attempts) if count else None,
        "completed_through_done": sum(a.protocol_completion for a in attempts),
        "critical_tasks": sum(a.critical_violation for a in attempts),
        "unresolved_progress": mean(unresolved) if unresolved else None,
        "agent_tool_errors": errors,
        "agent_tool_errors_per_task": errors / count if count else None,
        "valid_tool_calls": valid,
        "unclassified_tool_calls": sum(a.unclassified_tool_calls for a in attempts),
        "tool_call_precision": valid / (valid + errors) if valid + errors else None,
        "bank_calls": sum(a.bank_calls for a in attempts),
        "discovery_calls": sum(a.discovery_calls for a in attempts),
        "duration_seconds": {
            "scored": duration_statistics(
                a.duration_seconds for a in attempts if a.duration_seconds is not None
            ),
            "solved": duration_statistics(
                a.duration_seconds for a in solved if a.duration_seconds is not None
            ),
        },
        "normalized_bank_calls": None,
        "normalized_time": None,
    }
    if baselines and solved:
        summary["normalized_bank_calls"] = median(
            a.bank_calls / max(1, baselines[a.task_id].bank_calls) for a in solved
        )
        if all(a.duration_seconds is not None for a in solved):
            summary["normalized_time"] = median(
                a.duration_seconds / baselines[a.task_id].duration_seconds
                for a in solved
            )
    return summary


def _order(summary):
    return (
        -summary["solved"],
        summary["critical_tasks"],
        -(summary["unresolved_progress"] or 0),
        summary["agent_tool_errors"],
        summary["normalized_bank_calls"] or 0,
        summary["normalized_time"] or 0,
    )


def rank(
    participants: dict[str, list[Attempt]], baselines: dict[str, TaskBaseline]
) -> list[dict]:
    tasks = set(baselines)
    if not tasks or not participants:
        raise ValueError(
            "The competition needs participants and a common calibrated task set"
        )
    rows = []
    for name, attempts in participants.items():
        if len(attempts) != len(tasks) or {a.task_id for a in attempts} != tasks:
            raise ValueError(f"{name}: expected one scored attempt per common task")
        if any(a.duration_seconds is None for a in attempts):
            raise ValueError(f"{name}: missing coordinator time for a scored attempt")
        rows.append({"participant": name, **summarize(attempts, baselines)})
    rows.sort(key=lambda row: (_order(row), row["participant"]))
    previous = None
    place = 0
    for index, row in enumerate(rows, start=1):
        order = _order(row)
        if order != previous:
            place = index
        row["rank"] = place
        previous = order
    return rows
