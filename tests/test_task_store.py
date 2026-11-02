"""Storage boundaries for individual banking tasks and historical snapshots."""

import json

import pytest

from tau2.domains.banking_ru.task_store import (
    load_tasks,
    render_task_patch,
    task_outputs,
    write_tasks,
)


def indexed(tmp_path):
    folder = tmp_path / "tasks"
    folder.mkdir()
    tasks = [{"id": "Bank 2", "ticket": "Первое"}, {"id": "Bank 1", "ticket": "Второе"}]
    index = tmp_path / "tasks.json"
    paths = ["tasks/bank_002.json", "tasks/bank_001.json"]
    index.write_text(json.dumps({"task_files": paths}))
    for name, task in zip(paths, tasks):
        (tmp_path / name).write_text(json.dumps(task, ensure_ascii=False))
    return index, tasks


def test_index_preserves_order_and_updates_only_changed_task(tmp_path):
    index, tasks = indexed(tmp_path)
    assert load_tasks(index) == tasks
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    tasks[0]["ticket"] = "Изменено"
    outputs = task_outputs(index, tasks)
    assert outputs[index] == before[index]
    assert (
        outputs[tmp_path / "tasks/bank_001.json"]
        == before[tmp_path / "tasks/bank_001.json"]
    )
    patch = render_task_patch(index, load_tasks(index), tasks)
    assert "tasks/bank_002.json" in patch
    assert "tasks/bank_001.json" not in patch
    write_tasks(index, tasks)
    assert load_tasks(index) == tasks
    assert render_task_patch(index, tasks, tasks) == ""


def test_legacy_snapshot_remains_aggregate_and_keeps_unchanged_bytes(tmp_path):
    path = tmp_path / "tasks.json"
    tasks = [{"id": "Bank 2", "ticket": "Тест"}]
    raw = json.dumps(tasks, ensure_ascii=False).encode()
    path.write_bytes(raw)
    assert load_tasks(path) == tasks
    assert task_outputs(path, tasks) == {path: raw}
    tasks[0]["ticket"] = "Другой"
    write_tasks(path, tasks)
    assert json.loads(path.read_text()) == tasks
    assert not (tmp_path / "tasks").exists()


@pytest.mark.parametrize(
    "failure", ["escape", "duplicate", "missing", "wrong_id", "symlink"]
)
def test_invalid_index_fails_closed(tmp_path, failure):
    index, tasks = indexed(tmp_path)
    content = json.loads(index.read_text())
    if failure == "escape":
        content["task_files"][0] = "../outside.json"
    elif failure == "duplicate":
        content["task_files"].append(content["task_files"][0])
    elif failure == "missing":
        (tmp_path / content["task_files"][0]).unlink()
    elif failure == "wrong_id":
        (tmp_path / content["task_files"][0]).write_text(json.dumps(tasks[1]))
    elif failure == "symlink":
        source = tmp_path / content["task_files"][0]
        source.unlink()
        outside = tmp_path.parent / (tmp_path.name + "-outside.json")
        outside.write_text(json.dumps(tasks[0]))
        source.symlink_to(outside)
    index.write_text(json.dumps(content))
    with pytest.raises((ValueError, FileNotFoundError)):
        load_tasks(index)


def split_indexed(tmp_path):
    index, tasks = indexed(tmp_path)
    task = tasks[0]
    task.update(
        description={"purpose": "Author-only"},
        initial_state=None,
        evaluation_criteria={"reward_basis": ["DB", "ACTION"]},
        scenario_version="v1",
    )
    folder = tmp_path / "tasks/bank_002"
    folder.mkdir()
    scenario = {
        k: v for k, v in task.items() if k not in {"ticket", "evaluation_criteria"}
    }
    (folder / "scenario.json").write_text(json.dumps(scenario))
    (folder / "ticket.md").write_text(task["ticket"])
    (folder / "evaluation.json").write_text(json.dumps(task["evaluation_criteria"]))
    (tmp_path / "tasks/bank_002.json").unlink()
    index.write_text(
        json.dumps(
            {"task_files": ["tasks/bank_002/scenario.json", "tasks/bank_001.json"]}
        )
    )
    return index, tasks, folder


def test_three_files_reassemble_and_preserve_unchanged_bytes(tmp_path):
    from tau2.domains.banking_ru.task_store import task_inputs

    index, tasks, folder = split_indexed(tmp_path)
    assert load_tasks(index) == tasks
    inputs = task_inputs(index)
    assert set(inputs) == {
        index,
        folder / "scenario.json",
        folder / "ticket.md",
        folder / "evaluation.json",
        tmp_path / "tasks/bank_001.json",
    }
    before = {p: p.read_bytes() for p in inputs}
    assert task_outputs(index, tasks) == before
    write_tasks(index, tasks)
    assert {p: p.read_bytes() for p in inputs} == before


@pytest.mark.parametrize("part", ["ticket", "scenario", "evaluation"])
def test_three_file_writer_updates_only_changed_part(tmp_path, part):
    index, tasks, folder = split_indexed(tmp_path)
    if part == "ticket":
        tasks[0]["ticket"] += "\n"
        write_tasks(index, tasks)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    if part == "ticket":
        tasks[0]["ticket"] = "Изменённый тикет\n"
    elif part == "scenario":
        tasks[0]["scenario_version"] = "v2"
    else:
        tasks[0]["evaluation_criteria"]["reward_basis"] = ["DB"]
    target = folder / ("ticket.md" if part == "ticket" else part + ".json")
    patch = render_task_patch(index, load_tasks(index), tasks)
    assert str(target) in patch
    assert patch.count("*** Update File:") == 1
    write_tasks(index, tasks)
    assert load_tasks(index) == tasks
    assert not (tmp_path / "tasks/bank_002.json").exists()
    for path, raw in before.items():
        if path != target:
            assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "defect", ["missing", "escape", "duplicate_ticket", "wrong_id", "bad_evaluation"]
)
def test_three_files_reject_invalid_inputs(tmp_path, defect):
    index, _, folder = split_indexed(tmp_path)
    if defect == "missing":
        (folder / "ticket.md").unlink()
    elif defect == "escape":
        target = folder / "evaluation.json"
        target.unlink()
        outside = tmp_path.parent / (tmp_path.name + "-outside.json")
        outside.write_text("{}")
        target.symlink_to(outside)
    elif defect == "duplicate_ticket":
        path = folder / "scenario.json"
        value = json.loads(path.read_text())
        value["ticket"] = "Hidden duplicate"
        path.write_text(json.dumps(value))
    elif defect == "wrong_id":
        path = folder / "scenario.json"
        value = json.loads(path.read_text())
        value["id"] = "Bank 3"
        path.write_text(json.dumps(value))
    else:
        (folder / "evaluation.json").write_text("[]")
    with pytest.raises((ValueError, FileNotFoundError)):
        load_tasks(index)


@pytest.mark.parametrize("replacement", ["Первое\n", "Другой тикет"])
def test_ticket_patch_rejects_unrepresentable_eof(tmp_path, replacement):
    from copy import deepcopy

    index, before, _ = split_indexed(tmp_path)
    after = deepcopy(before)
    after[0]["ticket"] = replacement
    with pytest.raises(ValueError, match="write_tasks"):
        render_task_patch(index, before, after)


def test_ticket_compatibility_fields_are_derived_without_persisting_copies(tmp_path):
    index, tasks = indexed(tmp_path)
    folder = tmp_path / "tasks/bank_002"
    folder.mkdir()
    scenario = {
        "id": "Bank 2",
        "user_scenario": {"instructions": {"domain": "banking_ru"}},
    }
    (folder / "scenario.json").write_text(json.dumps(scenario))
    (folder / "ticket.md").write_text("Обращение\n")
    (folder / "evaluation.json").write_text("{}")
    index.write_text(json.dumps({"task_files": ["tasks/bank_002/scenario.json"]}))
    loaded = load_tasks(index)
    instructions = loaded[0]["user_scenario"]["instructions"]
    assert instructions["reason_for_call"] == instructions["known_info"] == "Обращение"
    write_tasks(index, loaded)
    assert json.loads((folder / "scenario.json").read_text()) == scenario
    assert loaded[0]["user_scenario"]["instructions"] == instructions
    (folder / "ticket.md").write_text("Новое обращение\n")
    assert (
        load_tasks(index)[0]["user_scenario"]["instructions"]["reason_for_call"]
        == "Новое обращение"
    )
