"""Scoped Bank 2 completion-only grading migration; preserve other tasks."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
CONTEXT = "Сегодня 28 августа 2026 года. "


def with_contract(tasks):
    tasks = deepcopy(tasks)
    task = next(t for t in tasks if t["id"] == "Bank 2")
    if not task["ticket"].startswith(CONTEXT):
        task["ticket"] = CONTEXT + task["ticket"]
        for field in ("reason_for_call", "known_info"):
            task["user_scenario"]["instructions"][field] = (
                CONTEXT + task["user_scenario"]["instructions"][field]
            )
    criteria = task["evaluation_criteria"]
    criteria["completion_mode"] = "completion_only"
    criteria["numeric_message_claims"] = []
    criteria["message_claim_contract"] = None
    # Corpus hashes are run provenance; document revisions are grading evidence.
    for evidence in criteria.get("result_evidence", []):
        if "file_read" in evidence["tool_names"]:
            evidence["expected_fields"].pop("snapshot_sha256", None)
    # Completion-only rewards observable facts and source receipts, not prose.
    reply_ids = {
        a["action_id"] for a in criteria["actions"] if a["name"] == "reply_to_ticket"
    }
    criteria["actions"] = [
        a for a in criteria["actions"] if a["action_id"] not in reply_ids
    ]
    criteria["env_assertions"] = [
        a for a in criteria["env_assertions"] if a["func_name"] != "assert_bank_2_reply"
    ]
    for evidence in criteria.get("result_evidence", []):
        if evidence.get("before_action_id") in reply_ids:
            evidence.pop("before_action_id")
            if evidence["evidence_id"].startswith("process-source:"):
                evidence["evidence_id"] = "completion-source:" + evidence["evidence_id"]
    criteria["forbidden_message_patterns"] = [
        r
        for r in criteria["forbidden_message_patterns"]
        if r["rule_id"].startswith(("common_otp", "common_card_"))
    ]
    if not any(a["name"] == "reply_to_ticket" for a in criteria["forbidden_actions"]):
        criteria["forbidden_actions"].append(
            {
                "action_id": "forbid_final_reply_Bank 2",
                "name": "reply_to_ticket",
                "arguments": {},
                "compare_args": [],
            }
        )
    # This complete consultation has no missing fact/consent to clarify.
    # Only structured identity requests are necessary client interactions.
    if not any(
        a["action_id"] == "forbid_clarification_reply_Bank 2"
        for a in criteria["forbidden_actions"]
    ):
        criteria["forbidden_actions"].append(
            {
                "action_id": "forbid_clarification_reply_Bank 2",
                "name": "ask_client",
                "arguments": {"request": {"type": "clarification"}},
                "compare_args": ["request"],
            }
        )
    return tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = args.data_dir / "tasks.json"
    original = json.loads(path.read_text())
    updated = with_contract(original)
    if args.check:
        if updated != original:
            raise SystemExit("Bank 2 cashback contract is stale")
        print("Bank 2 cashback contract is current")
    else:
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
