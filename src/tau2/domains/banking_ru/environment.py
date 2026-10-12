import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Optional

from tau2.data_model.tasks import Task
from tau2.domains.banking_ru.data_model import BankingDB, ClientAnswer
from tau2.domains.banking_ru.utils import (
    BANKING_RU_DATA_DIR,
    BANKING_RU_DB_PATH,
    BANKING_RU_TASK_SET_PATH,
)
from tau2.environment.environment import Environment
from tau2.utils import load_file


def get_environment(
    db: Optional[BankingDB] = None,
    solo_mode: bool = False,
    task_id: Optional[str] = None,
    snapshot: Optional[tuple] = None,
) -> Environment:
    # Import lazily: the file toolkit reuses the ordinary banking tools.
    from tau2.domains.banking_ru.file_pilot import get_file_environment

    if snapshot is None:
        raw = (BANKING_RU_DATA_DIR / "intake_answers.json").read_bytes()
        manifest = json.loads((BANKING_RU_DATA_DIR / "manifest.json").read_text())
        if hashlib.sha256(raw).hexdigest() != manifest["intake_answers_sha256"]:
            raise ValueError(
                "Structured intake answers differ from the pinned snapshot"
            )
        db = BankingDB.load(BANKING_RU_DB_PATH) if db is None else deepcopy(db)
        for customer_id, answers in json.loads(raw).items():
            scoped = [
                a
                for a in answers
                if a.get("task_id") == task_id and task_id is not None
            ]
            selected = (
                [
                    a
                    for a in answers
                    if a.get("identity_method") and not a.get("task_id")
                ]
                + scoped
                if scoped
                else [a for a in answers if not a.get("task_id")]
            )
            db.client_answers[customer_id] = [
                ClientAnswer.model_validate(a) for a in selected
            ]
    env = get_file_environment(
        BANKING_RU_DATA_DIR,
        "banking_ru",
        db,
        solo_mode,
        snapshot,
        allow_non_solo=True,
        structured_identity=True,
    )
    env.evaluation_env_kwargs["task_id"] = task_id
    return env


def get_tasks(task_split_name: Optional[str] = "base") -> list[Task]:
    tasks = load_file(BANKING_RU_TASK_SET_PATH)
    tasks = [Task.model_validate(task) for task in tasks]
    if task_split_name is None:
        return tasks
    task_splits = get_tasks_split()
    if task_split_name not in task_splits:
        raise ValueError(
            f"Invalid task split name: {task_split_name}. "
            f"Valid splits are: {task_splits.keys()}"
        )
    return [task for task in tasks if task.id in task_splits[task_split_name]]


def get_tasks_split() -> dict[str, list[str]]:
    split_file = (
        Path(BANKING_RU_TASK_SET_PATH).parent
        / f"split_{Path(BANKING_RU_TASK_SET_PATH).stem}.json"
    )
    return load_file(split_file)
