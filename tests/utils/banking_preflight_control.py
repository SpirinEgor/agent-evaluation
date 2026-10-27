"""Write actual HALF/FULL grades for a sealed task-validation control."""

import gzip
import hashlib
import json
from pathlib import Path

from tests.utils.banking_full_grade import grade_full_session


def write_control_report(session, path: Path) -> None:
    """Bind both complete evaluator outputs to the exact session task snapshot."""
    task = session.task.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(task, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    half = session.grade()
    full = grade_full_session(session).reward_info
    report = {
        "task_id": session.task.id,
        "task_sha256": fingerprint,
        "half": half.model_dump(mode="json"),
        "full": full.model_dump(mode="json"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False)
