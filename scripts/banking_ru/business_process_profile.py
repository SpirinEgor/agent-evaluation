"""Common business score: outcomes and authorization, not a prescribed research trace."""

from copy import deepcopy

KNOWLEDGE_TOOLS = {
    "file_read",
    "file_search",
    "corpus_command",
    "file_list",
    "file_find",
    "get_article",
    "search_knowledge",
}
BUSINESS_RECEIPTS = {
    "ask_client",
    "find_customer",
    "execute_reward_resolution",
    "get_reward_statement_status",
    "register_reward_statement",
    "get_bank_operation_status",
}


def apply_business_profile(tasks):
    tasks = deepcopy(tasks)
    for task in tasks:
        ec = task["evaluation_criteria"]
        ec["business_process_only"] = True
        knowledge_ids = {
            a["action_id"]
            for a in ec.get("actions", [])
            if a["name"] in KNOWLEDGE_TOOLS
        }
        ec["action_order"] = [
            r
            for r in ec.get("action_order", [])
            if r["before_action_id"] not in knowledge_ids
            and r["after_action_id"] not in knowledge_ids
        ]
        # Reference reads remain available for deriving expected state, never mandatory technique.
        for a in ec.get("actions", []):
            if a["name"] in KNOWLEDGE_TOOLS:
                a["required_for_reward"] = False
        ec["result_evidence"] = [
            r
            for r in ec.get("result_evidence", [])
            if set(r.get("tool_names", [])) & BUSINESS_RECEIPTS
        ]
        ec["env_assertions"] = [
            r
            for r in ec.get("env_assertions", [])
            if "attempt" not in r["func_name"]
            and r["func_name"] != "assert_reward_statements"
            and not r["func_name"].startswith("assert_file_")
        ]
        ec["forbidden_actions"] = [
            r
            for r in ec.get("forbidden_actions", [])
            if r["name"] not in KNOWLEDGE_TOOLS | {"reply_to_ticket"}
        ]
    return tasks
