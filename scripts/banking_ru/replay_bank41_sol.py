"""Exact archived Sol arguments replayed on current scoped Bank41 contract."""

import argparse
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
    task = next(t for t in get_tasks() if t.id == "Bank 41")
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    calls = [c for m in archived["messages"] for c in m.get("tool_calls") or []]
    for c in calls:
        session.call(c["name"], c["arguments"])
    session.call("done", {})
    half = session.grade()
    full = grade_full_session(session).reward_info
    payload = {
        "archive": str(args.archive),
        "raw_reward": archived["reward_info"],
        "translated_arguments": False,
        "archived_tool_calls": calls,
        "replayed_tool_calls": [
            {"name": c.name, "arguments": c.arguments}
            for c, r in session.log
            if c.name != "done"
        ],
        "half": half.model_dump(mode="json"),
        "full": full.model_dump(mode="json"),
        "errors": session.errors,
    }
    assert payload["replayed_tool_calls"] == [
        {"name": c["name"], "arguments": c["arguments"]} for c in calls
    ]
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


if __name__ == "__main__":
    main()
