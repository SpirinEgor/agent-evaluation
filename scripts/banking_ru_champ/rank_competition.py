"""Rank adjudicated competition outcomes using frozen per-task baselines."""

import argparse
import json
from pathlib import Path

from tau2.platform.competition_ranking import Attempt, TaskBaseline, rank


def load_attempt(row, base):
    if "record" not in row:
        return Attempt.model_validate(row)
    record = json.loads((base / row["record"]).read_text(encoding="utf-8"))
    if record.get("technical_failure"):
        raise ValueError(f"{row['record']}: a technical outcome needs reconciliation")
    measurements = record["metrics"]["competition"]
    return Attempt.model_validate(
        {
            **measurements,
            **record["result"]["reward_breakdown"],
            "task_id": record["task_id"],
            "reward": record["final_reward"],
            "protocol_completion": record["protocol_status"] == "done",
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    participants = {
        label: [load_attempt(row, args.input.parent) for row in rows]
        for label, rows in source["participants"].items()
    }
    baselines = {
        task_id: TaskBaseline.model_validate(row)
        for task_id, row in source["baselines"].items()
    }
    print(json.dumps(rank(participants, baselines), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
