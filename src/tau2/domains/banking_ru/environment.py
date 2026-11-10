import hashlib
import json
from copy import deepcopy
from typing import Optional

from tau2.data_model.tasks import Task
from tau2.domains.banking_ru.component_eligibility import prepare_access_policy
from tau2.domains.banking_ru.data_model import BankingDB, ClientAnswer
from tau2.domains.banking_ru.task_store import load_tasks
from tau2.domains.banking_ru.utils import (
    BANKING_RU_DATA_DIR,
    BANKING_RU_DB_PATH,
    BANKING_RU_TASK_SET_PATH,
)
from tau2.environment.environment import Environment
from tau2.platform.runtime_payloads import materialize_bytes, materialize_value


def get_environment(
    db: Optional[BankingDB] = None,
    solo_mode: bool = False,
    task_id: Optional[str] = None,
    snapshot: Optional[tuple] = None,
) -> Environment:
    # Import lazily: the file toolkit reuses the ordinary banking tools.
    from tau2.domains.banking_ru.file_pilot import get_file_environment

    if snapshot is None:
        raw = materialize_bytes(
            (BANKING_RU_DATA_DIR / "intake_answers.json").read_bytes(),
            json_document=True,
        )
        manifest = json.loads((BANKING_RU_DATA_DIR / "manifest.json").read_text())
        if hashlib.sha256(raw).hexdigest() != manifest["intake_answers_sha256"]:
            raise ValueError(
                "Structured intake answers differ from the pinned snapshot"
            )
        db = (
            BankingDB.model_validate_json(
                materialize_bytes(BANKING_RU_DB_PATH.read_bytes(), json_document=True)
            )
            if db is None
            else deepcopy(db)
        )
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


def get_tasks(task_split_name: Optional[str] = None) -> list[Task]:
    """Load the complete banking task set.

    task_split_name is unused, retained for the shared task-loader interface.
    Banking task splits are not defined.
    """
    return [prepare_task(task) for task in load_tasks(BANKING_RU_TASK_SET_PATH)]


def prepare_task(value: dict) -> Task:
    """The canonical runtime task, including domain-owned shared access rules."""
    # Authoring reads retain references. Only an explicitly configured benchmark
    # runtime expands them, including the hidden reference trajectory/criteria.
    import os

    task = Task.model_validate(
        materialize_value(value) if os.environ.get("BANKING_PAYLOAD_STORE") else value
    )
    if task.evaluation_criteria is not None:
        task.evaluation_criteria.tool_access_policy = prepare_access_policy(
            task.evaluation_criteria.tool_access_policy
        )
    return task
