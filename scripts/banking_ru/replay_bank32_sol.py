"""Replay original Bank32 Sol arguments unchanged against the current grader."""

import argparse
import hashlib
import json
from pathlib import Path

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    archived = json.loads(args.archive.read_text())
    task = next(t for t in get_tasks() if t.id == "Bank 32")
    session = PlatformSession(task, get_environment, max_calls=100, max_errors=20)
    calls = [c for m in archived["messages"] for c in m.get("tool_calls") or []]
    for call in calls:
        session.call(call["name"], call["arguments"])
    session.call("done", {})
    replayed = [
        {"name": c.name, "arguments": c.arguments}
        for c, _ in session.log
        if c.name != "done"
    ]
    assert replayed == [{"name": c["name"], "arguments": c["arguments"]} for c in calls]
    half = session.grade()
    full = grade_full_session(session).reward_info
    payload = {
        "task_id": task.id,
        "archive": str(args.archive),
        "archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        "original_reward": archived["reward_info"],
        "arguments_unchanged": True,
        "calls": len(calls),
        "half": half.model_dump(mode="json"),
        "full": full.model_dump(mode="json"),
        "errors": session.errors,
        "full_timing": "Sequential synthetic ticks from fresh session; not native streaming replay",
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "half": half.final_reward,
                "full": full.final_reward,
                "errors": session.errors,
                "calls": len(calls),
            }
        )
    )
    assert half.final_reward == full.final_reward == 1


if __name__ == "__main__":
    main()
