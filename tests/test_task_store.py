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
