"""Emit an apply_patch patch for common banking ownership/authentication rules.

--check detects stale task/DB/tool contracts without writing any files.
"""

import argparse
import ast
import difflib
import json
from collections import defaultdict
from pathlib import Path

from build_confidentiality_contract import DATA, TASK_FILE, render_contract

ROOT = Path(__file__).resolve().parents[2]
TOOLS_FILE = ROOT / "src/tau2/domains/banking_ru/tools.py"
ARGUMENT_COLLECTIONS = {
    "customer_id": "customers",
    "account_id": "accounts",
    "source_account_id": "accounts",
    "payout_account_id": "accounts",
    "from_account_id": "accounts",
    "to_account_id": "accounts",
    "card_id": "cards",
    "transaction_id": "transactions",
    "transaction_ids": "transactions",
    "dispute_id": "disputes",
    "case_id": "cases",
    "subscription_id": "subscriptions",
    "autopayment_id": "autopayments",
    "deposit_id": "deposits",
    "loan_id": "loans",
    "penalty_id": "penalties",
    "device_id": "devices",
    "document_id": "documents",
}
PUBLIC_ID_ARGUMENTS = {"article_id", "new_tariff_id", "confirmation_id", "quote_id"}
PRE_AUTH_TOOLS = ["verify_identity", "ask_client", "reply_to_ticket"]
RESULT_COLLECTIONS = {
    "get_accounts": ["accounts"],
    "get_cards": ["cards"],
    "get_transactions": ["transactions"],
    "get_operations": ["transactions"],
    "get_transaction_details": ["transactions"],
    "get_disputes": ["disputes"],
    "get_deposit": ["deposits"],
    "get_loans": ["loans"],
    "get_loan_cases": ["cases"],
    "get_devices": ["devices"],
    "get_documents": ["documents"],
    "open_account": ["accounts"],
    "open_deposit": ["deposits"],
    "create_case": ["cases"],
    "request_account_closure": ["cases"],
    "open_card_loss_case": ["cases"],
    "request_credit_holidays": ["cases"],
    "escalate_to_human": ["cases"],
    "open_dispute": ["disputes"],
    "refund_fee": ["transactions"],
    "refund_transaction": ["transactions"],
    "grant_cashback": ["transactions"],
}


def protected_arguments() -> dict[str, dict[str, str]]:
    """Classify ID arguments; a new unclassified ID is a contract error."""
    tree = ast.parse(TOOLS_FILE.read_text(encoding="utf-8"))
    toolkit = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BankingTools"
    )
    result = {}
    for method in toolkit.body:
        if not isinstance(method, ast.FunctionDef):
            continue
        decorations = [
            d
            for d in method.decorator_list
            if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "is_tool"
        ]
        if not decorations:
            continue
        arguments = [a.arg for a in method.args.args if a.arg != "self"]
        unknown = (
            {arg for arg in arguments if arg.endswith(("_id", "_ids"))}
            - ARGUMENT_COLLECTIONS.keys()
            - PUBLIC_ID_ARGUMENTS
        )
        if unknown:
            raise ValueError(
                f"Unclassified ownership arguments in {method.name}: {unknown}"
            )
        fields = {
            arg: ARGUMENT_COLLECTIONS[arg]
            for arg in arguments
            if arg in ARGUMENT_COLLECTIONS
        }
        if fields:
            result[method.name] = fields
        elif ast.unparse(decorations[0].args[0]) == "ToolType.WRITE":
            raise ValueError(
                f"A new write needs an explicit access contract: {method.name}"
            )
    # The shared structured ask_client override exposes this optional owner selector.
    result["ask_client"]["transaction_id"] = "transactions"
    result["ask_client"]["loan_id"] = "loans"
    result["ask_client"]["account_id"] = "accounts"
    result["ask_client"]["payout_account_id"] = "accounts"
    return result


def resource_catalog(db: dict) -> dict[str, dict[str, list[str]]]:
    """Group immutable initial resource owners; created records are learned later."""
    result = {}
    for collection in sorted(set(ARGUMENT_COLLECTIONS.values())):
        groups = defaultdict(set)
        if collection == "customers":
            for customer_id in db["customers"]:
                groups[customer_id].add(customer_id)
        elif collection == "penalties":
            for loan in db["loans"].values():
                for penalty in loan["penalties"]:
                    groups[loan["customer_id"]].add(penalty["id"])
        else:
            for record in db.get(collection, {}).values():
                groups[record["customer_id"]].add(record["id"])
        result[collection] = {
            owner: sorted(ids) for owner, ids in sorted(groups.items())
        }
    return result


def discovery_catalog(db: dict) -> dict:
    """Declare the actual identity selectors and backend normalizations."""
    tree = ast.parse(TOOLS_FILE.read_text(encoding="utf-8"))
    method = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "find_customer"
    )
    arguments = {a.arg for a in method.args.args if a.arg != "self"}
    if arguments != {"phone", "last_name", "birth_date"}:
        raise ValueError("Changed discovery selectors need an explicit contract")
    return {
        "find_customer": {
            "records_by_id": {
                c["id"]: {
                    "phone": c["phone"],
                    "last_name": c["full_name"].split()[0],
                    "birth_date": c["birth_date"],
                }
                for c in db["customers"].values()
            },
            "arguments": {
                "phone": {"digits_only": True, "prefix_replacements": {"8": "7"}},
                "last_name": {"strip": True, "lowercase": True},
                "birth_date": {},
            },
            "required_any_arguments": ["phone", "last_name"],
        }
    }


def with_contract(tasks: list[dict], db: dict) -> list[dict]:
    """Attach access policy without changing reference actions or ticket content."""
    catalog, arguments = resource_catalog(db), protected_arguments()
    for task in tasks:
        criteria = task["evaluation_criteria"]
        subjects = {
            a["arguments"]["customer_id"]
            for a in criteria["actions"]
            if a["name"] == "verify_identity"
        }
        if len(subjects) != 1:
            raise ValueError(f"Task {task['id']} needs one explicitly defined customer")
        customer_id = next(iter(subjects))
        auth = [
            a["action_id"]
            for a in criteria["actions"]
            if a["name"] == "verify_identity"
            and a["arguments"].get("customer_id") == customer_id
        ]
        if not auth:
            raise ValueError(f"Missing authentication reference in {task['id']}")
        criteria["tool_access_policy"] = {
            "policy_id": "banking_private_access",
            "allowed_customer_ids": [customer_id],
            "authentication_action_ids": auth,
            "resources_by_customer": catalog,
            "tool_arguments": arguments,
            "pre_auth_tools": PRE_AUTH_TOOLS,
            "result_resource_collections": RESULT_COLLECTIONS,
            "result_scope_owner_arguments": {"get_operations": "customer_id"},
            "discovery_tools": discovery_catalog(db),
        }
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--task-id", help="Emit a bounded patch for one task")
    args = parser.parse_args()
    original = TASK_FILE.read_text(encoding="utf-8")
    tasks = json.loads(original)
    db = json.loads((DATA / "db.json").read_text(encoding="utf-8"))
    selected = (
        tasks
        if args.task_id is None
        else [task for task in tasks if task["id"] == args.task_id]
    )
    if not selected:
        raise ValueError("Unknown task ID")
    with_contract(selected, db)
    if args.check:
        if json.loads(original) != tasks:
            print("Access contract is stale; regenerate and apply its patch.")
            return 1
        print(
            f"Access contract is current for {len(selected)} tasks and {len(protected_arguments())} protected tools."
        )
        return 0
    rendered = render_contract(
        original,
        tasks,
        {task["id"] for task in selected},
        fields=("tool_access_policy",),
    )
    diff = list(difflib.unified_diff(original.splitlines(), rendered.splitlines(), n=3))
    if diff:
        print("*** Begin Patch")
        print(f"*** Update File: {TASK_FILE}")
        anchored = False
        for line in diff[2:]:
            if line.startswith("@@"):
                print(
                    f'@@     "id": "{args.task_id}",'
                    if args.task_id and not anchored
                    else "@@"
                )
                anchored = True
            else:
                print(line)
        print("*** End Patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
