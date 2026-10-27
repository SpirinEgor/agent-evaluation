"""Read banking tasks from an ordered index or a historical aggregate snapshot.

The index is storage metadata only. Evaluators and tools receive the same task
objects regardless of their on-disk representation.
"""

import difflib
import json
import os
import re
import tempfile
from pathlib import Path


def _serialize(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _index_paths(path: Path, value: dict) -> list[Path]:
    if set(value) != {"task_files"} or not isinstance(value["task_files"], list):
        raise ValueError(f"Invalid task index: {path}")
    paths = []
    for name in value["task_files"]:
        if not isinstance(name, str) or not re.fullmatch(
            r"tasks/bank_[0-9]{3}(?:\.json|/scenario\.json)", name
        ):
            raise ValueError(f"Invalid task reference: {name!r}")
        member = path.parent / name
        if not member.resolve().is_relative_to(path.parent.resolve()):
            raise ValueError(f"Task reference leaves its data directory: {name}")
        if member in paths:
            raise ValueError(f"Duplicate task reference: {name}")
        paths.append(member)
    return paths


def _validate(tasks) -> list[dict]:
    if not isinstance(tasks, list):
        raise ValueError("Tasks must be a list")
    ids = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            raise ValueError("Each task must be an object with a string ID")
        if task["id"] in ids:
            raise ValueError(f"Duplicate task ID: {task['id']}")
        ids.add(task["id"])
    return tasks


def _filename(task: dict) -> str:
    match = re.fullmatch(r"Bank ([1-9][0-9]{0,2})", task["id"])
    if not match:
        raise ValueError(f"Invalid banking task ID: {task['id']}")
    return f"tasks/bank_{int(match[1]):03d}.json"


def _member_inputs(member: Path, root: Path) -> list[Path]:
    paths = (
        [member, member.with_name("ticket.md"), member.with_name("evaluation.json")]
        if member.name == "scenario.json"
        else [member]
    )
    for target in paths:
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Task reference leaves its data directory: {target}")
    return paths


def _load_member(member: Path, root: Path) -> dict:
    inputs = _member_inputs(member, root)
    task = json.loads(member.read_bytes())
    if len(inputs) == 3:
        if (
            not isinstance(task, dict)
            or {"ticket", "evaluation_criteria"} & task.keys()
        ):
            raise ValueError(
                f"Scenario must not duplicate ticket or evaluation: {member}"
            )
        evaluation = json.loads(inputs[2].read_bytes())
        if not isinstance(evaluation, dict):
            raise ValueError(f"Evaluation must be an object: {inputs[2]}")
        task = {
            **task,
            "ticket": inputs[1].read_bytes().decode("utf-8"),
            "evaluation_criteria": evaluation,
        }
    return task


def load_tasks(path: Path) -> list[dict]:
    """Load all task objects, preserving the explicit index/aggregate order."""
    path = Path(path)
    value = json.loads(path.read_bytes())
    if isinstance(value, list):
        return _validate(value)
    paths = _index_paths(path, value) if isinstance(value, dict) else None
    if paths is None:
        raise ValueError(f"Invalid task store: {path}")
    tasks = []
    for member in paths:
        task = _load_member(member, path.parent)
        _validate([task])
        expected = path.parent / _filename(task)
        if member not in (expected, expected.with_suffix("") / "scenario.json"):
            raise ValueError(f"Task ID does not match filename: {member}")
        tasks.append(task)
    return _validate(tasks)


def task_inputs(path: Path) -> list[Path]:
    """List the index and all declared task files for concurrency checks."""
    path = Path(path)
    value = json.loads(path.read_bytes())
    load_tasks(path)  # Validate before handing paths to a writer.
    if isinstance(value, list):
        return [path]
    return [
        path,
        *(
            target
            for member in _index_paths(path, value)
            for target in _member_inputs(member, path.parent)
        ),
    ]


def task_outputs(path: Path, tasks: list[dict]) -> dict[Path, bytes]:
    """Expand an update into actual files; retain unchanged bytes verbatim.

    Missing destinations use the historical aggregate format, so temporary
    authoring snapshots do not silently change their representation.
    """
    path = Path(path)
    _validate(tasks)
    value = json.loads(path.read_bytes()) if path.exists() else []
    if isinstance(value, list):
        if path.exists() and value == tasks:
            return {path: path.read_bytes()}
        return {path: _serialize(tasks)}
    old_tasks = load_tasks(path)
    if {t["id"] for t in old_tasks} - {t["id"] for t in tasks}:
        raise ValueError("Removing indexed tasks requires an explicit migration")
    old_names = dict(zip((t["id"] for t in old_tasks), value["task_files"]))
    names = [old_names.get(task["id"], _filename(task)) for task in tasks]
    index = {"task_files": names}
    outputs = {path: path.read_bytes() if value == index else _serialize(index)}
    for name, task in zip(names, tasks):
        member = path.parent / name
        if not member.resolve().is_relative_to(path.parent.resolve()):
            raise ValueError(f"Task reference leaves its data directory: {name}")
        if member.name == "scenario.json":
            _member_inputs(member, path.parent)
            if not isinstance(task.get("ticket"), str) or not isinstance(
                task.get("evaluation_criteria"), dict
            ):
                raise ValueError(
                    "Split tasks require a text ticket and evaluation object"
                )
            pieces = {
                member: {
                    k: v
                    for k, v in task.items()
                    if k not in {"ticket", "evaluation_criteria"}
                },
                member.with_name("evaluation.json"): task["evaluation_criteria"],
            }
            outputs[member.with_name("ticket.md")] = task["ticket"].encode("utf-8")
        else:
            pieces = {member: task}
        for target, piece in pieces.items():
            outputs[target] = (
                target.read_bytes()
                if target.exists() and json.loads(target.read_bytes()) == piece
                else _serialize(piece)
            )
    return outputs


def write_tasks(path: Path, tasks: list[dict]) -> None:
    """Write task members atomically, publishing index changes last."""
    path = Path(path)
    outputs = task_outputs(path, tasks)
    for target in [p for p in outputs if p != path] + [path]:
        raw = outputs[target]
        if target.exists() and target.read_bytes() == raw:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)


def render_task_patch(path: Path, before: list[dict], after: list[dict]) -> str:
    """Render apply_patch sections against actual files, never the virtual list."""
    path = Path(path)
    if load_tasks(path) != before:
        raise ValueError("Task store changed while preparing its patch")
    sections = []
    for target, raw in task_outputs(path, after).items():
        original = target.read_bytes() if target.exists() else None
        if original == raw:
            continue
        if target.name == "ticket.md" and (
            original is None
            or not original.endswith(b"\n")
            or not raw.endswith(b"\n")
            or b"\r" in original
            or b"\r" in raw
        ):
            raise ValueError(
                "Text patch cannot preserve ticket line endings; use write_tasks"
            )
        if original is None:
            sections.append(
                f"*** Add File: {target}\n"
                + "".join("+" + line + "\n" for line in raw.decode().splitlines())
            )
        else:
            diff = list(
                difflib.unified_diff(
                    original.decode().splitlines(),
                    raw.decode().splitlines(),
                    lineterm="",
                )
            )
            body = "".join(
                "@@\n" if line.startswith("@@") else line + "\n" for line in diff[2:]
            )
            sections.append(f"*** Update File: {target}\n{body}")
    return (
        "*** Begin Patch\n" + "".join(sections) + "*** End Patch\n" if sections else ""
    )


def is_task_index(path: Path) -> bool:
    return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)


def read_task_text(path: Path) -> str:
    """Keep legacy formatting, expose indexed stores to aggregate renderers."""
    original = path.read_text(encoding="utf-8")
    if isinstance(json.loads(original), list):
        return original
    return json.dumps(load_tasks(path), ensure_ascii=False, indent=2) + "\n"


def read_json(path: Path):
    """Read ordinary JSON or derive the contents of a banking input store."""
    if path.name == "tasks.json":
        return load_tasks(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if path.name == "manifest.json" and isinstance(value, dict):
        from tau2.domains.banking_ru.corpus_manifest import COMPACT_KEYS, load_manifest

        if "documents" in value or set(value) == COMPACT_KEYS:
            return load_manifest(path.parent)
    return value


def write_json(path: Path, value) -> None:
    """Preserve input-store layouts without interpreting unrelated JSON reports."""
    if path.name == "tasks.json":
        write_tasks(path, value)
    elif (
        path.name == "manifest.json"
        and isinstance(value, dict)
        and "documents" in value
    ):
        from tau2.domains.banking_ru.corpus_manifest import manifest_outputs

        for target, raw in manifest_outputs(path.parent, value).items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_bytes() != raw:
                target.write_bytes(raw)
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
