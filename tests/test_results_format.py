"""Tests for Results storage format: directory-based format, format detection,
round-trip compatibility, streaming APIs, and checkpoint operations."""

import json
import multiprocessing

import pytest

from tau2.data_model.simulation import (
    SIMULATIONS_DIR,
    Info,
    Results,
    SimulationRun,
    TerminationReason,
    UserInfo,
)
from tau2.data_model.tasks import EvaluationCriteria, Task, UserScenario
from tau2.environment.environment import EnvironmentInfo
from tau2.runner.checkpoint import create_checkpoint_fns


def _make_info() -> Info:
    return Info(
        git_commit="abc123",
        num_trials=1,
        max_steps=100,
        max_errors=10,
        user_info=UserInfo(implementation="user_simulator"),
        agent_info={"implementation": "llm_agent"},
        environment_info=EnvironmentInfo(domain_name="mock", policy="test policy"),
    )


def _make_task(task_id: str) -> Task:
    return Task(
        id=task_id,
        user_scenario=UserScenario(instructions="test instruction"),
        evaluation_criteria=EvaluationCriteria(),
    )


def _make_sim(
    task_id: str,
    trial: int = 0,
    seed: int = 42,
    termination_reason: TerminationReason = TerminationReason.USER_STOP,
) -> SimulationRun:
    return SimulationRun(
        id=f"sim-{task_id}-t{trial}-s{seed}",
        task_id=task_id,
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:01:00",
        duration=60.0,
        termination_reason=termination_reason,
        messages=[],
        trial=trial,
        seed=seed,
    )


@pytest.fixture
def sample_results():
    return Results(
        info=_make_info(),
        tasks=[_make_task("t0"), _make_task("t1")],
        simulations=[
            _make_sim("t0", trial=0, seed=42),
            _make_sim("t1", trial=0, seed=42),
        ],
    )


class TestJsonFormat:
    def test_save_and_load(self, tmp_path, sample_results):
        p = tmp_path / "results.json"
        sample_results.save(p, format="json")
        loaded = Results.load(p)
        assert len(loaded.simulations) == 2
        assert loaded.info.git_commit == "abc123"
        assert {s.id for s in loaded.simulations} == {
            s.id for s in sample_results.simulations
        }


class TestDirFormat:
    def test_load_from_directory_path(self, tmp_path, sample_results):
        p = tmp_path / "results.json"
        sample_results.save(p, format="dir")
        loaded = Results.load(tmp_path)
        assert len(loaded.simulations) == 2


class TestCheckpointDirFormat:
    def test_checkpoint_saver_dir_format(self, tmp_path):
        save_path = tmp_path / "results.json"
        results = Results(info=_make_info(), tasks=[_make_task("t0")], simulations=[])
        results.save(save_path, format="dir")
        lock = multiprocessing.Lock()
        (save_fn, _) = create_checkpoint_fns(save_path, lock)
        sim = _make_sim("t0", trial=0, seed=42)
        save_fn(sim)
        sims_dir = tmp_path / SIMULATIONS_DIR
        sim_files = list(sims_dir.glob("*.json"))
        assert len(sim_files) == 1
        assert sim_files[0].name == f"{sim.id}.json"
        loaded = Results.load(save_path)
        assert len(loaded.simulations) == 1
        assert loaded.simulations[0].task_id == "t0"


def test_review_status_survives_dir_save_and_checkpoint_without_final_reward(
    tmp_path, sample_results
):
    from tau2.data_model.simulation import RewardInfo

    path = tmp_path / "results.json"
    sim = sample_results.simulations[0]
    sim.reward_info = RewardInfo(reward=1, info={"grading_status": "needs_review"})
    sample_results.save(path, format="dir")
    metadata = json.loads(path.read_text())
    row = metadata["simulation_index"][0]
    assert row["reward"] is None and row["grading_status"] == "needs_review"
    loaded = Results.load(path)
    assert loaded.simulations[0].reward_info.reward == 1
    assert loaded.simulations[0].reward_info.final_reward is None
    save_fn, _ = create_checkpoint_fns(path, multiprocessing.Lock())
    extra = sim.model_copy(update={"id": "review-extra", "task_id": "t1", "seed": 55})
    save_fn(extra)
    row = next(
        r
        for r in json.loads(path.read_text())["simulation_index"]
        if r["id"] == "review-extra"
    )
    assert row["reward"] is None and row["grading_status"] == "needs_review"
