"""Replay archived Bank47 calls unchanged on two independent clean sessions."""

import argparse
import json
from pathlib import Path

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    saved = json.loads(args.archive.read_text())
    original = [c for m in saved["messages"] for c in m.get("tool_calls") or []]
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {
        "historical_reward": saved["reward_info"]["reward"],
        "fresh_models": 0,
        "runs": [],
    }
    for route in ("raw", "raw_repeat"):
        s = PlatformSession(
            next(t for t in get_tasks() if t.id == "Bank 47"),
            get_environment,
            max_calls=250,
            max_errors=100,
        )
        translations = []
        for c in original:
            arguments = dict(c["arguments"])
            s.call(c["name"], arguments)
        s.call("done", {})
        half = s.grade()
        full = grade_full_session(s).reward_info
        result = {
            "route": route,
            "original_calls": len(original),
            "replayed_calls": len(s.log),
            "HALF": half.model_dump(mode="json"),
            "FULL": full.model_dump(mode="json"),
            "translations": translations,
            "calls": [
                {"call": c.model_dump(mode="json"), "result": r.model_dump(mode="json")}
                for c, r in s.log
            ],
        }
        (args.output / (route + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        summary["runs"].append(
            {
                "route": route,
                "HALF": half.final_reward,
                "FULL": full.final_reward,
                "translations": len(translations),
            }
        )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
