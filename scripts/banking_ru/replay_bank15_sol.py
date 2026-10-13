"""Regrade one saved Bank15 attempt unchanged in a fresh environment."""

import argparse
import json
from pathlib import Path

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    saved = json.loads(args.archive.read_text())
    assert saved["task_id"] == "Bank 15"
    task = next(t for t in get_tasks() if t.id == "Bank 15")
    s = PlatformSession(task, get_environment, max_errors=100)
    calls = [c for m in saved["messages"] for c in m.get("tool_calls") or []]
    for call in calls:
        s.call(call["name"], call["arguments"])
    s.call("done", {})
    half = s.grade()
    full = grade_full_session(s).reward_info
    result = {
        "task_id": "Bank 15",
        "HALF": half.final_reward,
        "FULL": full.final_reward,
        "components": {k.value: v for k, v in half.reward_breakdown.items()},
        "env_reasons": (half.info or {}).get("env"),
        "errors": s.errors,
        "calls_replayed": len(calls),
        "original_components": saved["reward_info"]["reward_breakdown"],
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    assert half.final_reward == full.final_reward == saved["reward_info"]["reward"]
    assert result["components"] == result["original_components"]


if __name__ == "__main__":
    main()
