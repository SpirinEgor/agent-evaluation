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

from tau2.domains.banking_ru.component_eligibility import COMMON_READ_ACCESS
from tau2.domains.banking_ru.task_store import (
    is_task_index,
    read_task_text,
    render_task_patch,
)

ROOT = Path(__file__).resolve().parents[2]
TOOLS_FILE = ROOT / "src/tau2/domains/banking_ru/tools.py"
ARGUMENT_COLLECTIONS = {
    "customer_id": "customers",
    "submission_id": "reward_client_submissions",
    "statement_id": "reward_statement_registrations",
    "account_id": "accounts",
    "source_account_id": "accounts",
    "payout_account_id": "accounts",
    "from_account_id": "accounts",
    "to_account_id": "accounts",
    "card_id": "cards",
    "transaction_id": "transactions",
    "original_transaction_id": "transactions",
    "promotion_id": "promotions",
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
    "contact_id": "document_contacts",
    "permission_id": "cashback_adjustment_permissions",
    "request_id": "bank_operations",
    "operation_id": "cashback_adjustment_operations",
    "offer_id": "reward_resolution_offers",
}
PUBLIC_ID_ARGUMENTS = {
    "article_id",
    "new_tariff_id",
    "confirmation_id",
    "quote_id",
    "merchant_id",
}
# Component IDs are offer-local keys, never selectors of another customer's data.
# The enclosing offer_id is protected; execution verifies membership in that offer.
LOCAL_ID_ARGUMENTS = {
    "execute_reward_resolution": {"component_ids"},
    "ask_client": {"component_ids"},
}
PRE_AUTH_TOOLS = ["verify_identity", "ask_client", "reply_to_ticket"]
RESULT_COLLECTIONS = {
    "ask_client": ["reward_client_submissions"],
    "register_reward_statement": ["reward_statement_registrations"],
    "get_reward_statement_status": ["reward_statement_registrations"],
    "get_reward_resolution_offers": ["reward_resolution_offers"],
    "execute_reward_resolution": ["transactions"],
    "get_accounts": ["accounts"],
    "get_cards": ["cards"],
    "get_reward_contracts": ["reward_contracts"],
    "get_promotion_activations": ["promotion_activations"],
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
    "create_cashback_investigation": ["cases"],
    "request_account_closure": ["cases"],
    "open_card_loss_case": ["cases"],
    "request_credit_holidays": ["cases"],
    "escalate_to_human": ["cases"],
    "open_dispute": ["disputes"],
    "refund_fee": ["transactions", "bank_operations"],
    "refund_transaction": ["transactions"],
    "grant_cashback": ["transactions"],
    "get_cashback_adjustment_permissions": ["cashback_adjustment_permissions"],
    "execute_cashback_adjustment": ["transactions", "cashback_adjustment_operations"],
    "get_cashback_adjustment_status": [
        "transactions",
        "cashback_adjustment_operations",
    ],
    "refresh_cashback_settlement": ["transactions"],
    "get_case_documents": ["documents"],
    "get_document_status": ["documents"],
    "get_document_delivery_contacts": ["document_contacts"],
    "deliver_case_document": ["document_deliveries", "bank_operations"],
    "get_fee_refund_state": ["transactions"],
    "get_bank_operation_status": ["bank_operations"],
    "get_cashback_investigations": ["cases"],
    "amend_cashback_investigation": ["cases"],
    "submit_cashback_review": ["cases"],
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
            - LOCAL_ID_ARGUMENTS.get(method.name, set())
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
    result["ask_client"]["request.card_id"] = "cards"
    result["ask_client"]["request.refunds.*.transaction_id"] = "transactions"
    result["create_cashback_investigation"]["items.*.transaction_id"] = "transactions"
    result["amend_cashback_investigation"]["items.*.transaction_id"] = "transactions"
    result["create_cashback_investigation"]["reserved_adjustments.*.permission_id"] = (
        "cashback_adjustment_permissions"
    )
    result["create_cashback_investigation"]["reserved_adjustments.*.transaction_id"] = (
        "transactions"
    )
    result["amend_cashback_investigation"]["reserved_adjustments.*.permission_id"] = (
        "cashback_adjustment_permissions"
    )
    result["amend_cashback_investigation"]["reserved_adjustments.*.transaction_id"] = (
        "transactions"
    )
    result["ask_client"]["request.contact_id"] = "document_contacts"
    result["ask_client"]["request.offer_id"] = "reward_resolution_offers"
    result["ask_client"]["request.case_id"] = "cases"
    result["ask_client"]["request.payouts.*.transaction_id"] = "transactions"
    result["ask_client"]["request.resulting_claims.*.transaction_id"] = "transactions"
    result["execute_reward_resolution"]["payouts.*.transaction_id"] = "transactions"
    result["ask_client"]["request.arguments.case_id"] = "cases"
    result["ask_client"]["request.arguments.transaction_ids.*"] = "transactions"
    result["register_reward_statement"] = {
        "customer_id": "customers",
        "case_id": "cases",
        "submission_id": "reward_client_submissions",
    }
    result["get_reward_statement_status"] = {
        "customer_id": "customers",
        "statement_id": "reward_statement_registrations",
    }
    return result


def validate_protected_arguments(arguments):
    """Check effective inherited tool schemas against declared owner selectors."""
    from tau2.domains.banking_ru.client_requests import StructuredIdentityBankingTools
    from tau2.environment.tool import as_tool
    from tau2.environment.toolkit import TOOL_ATTR

    def selectors(node, schema, prefix=""):
        if "$ref" in node:
            target = schema
            for key in node["$ref"].removeprefix("#/").split("/"):
                target = target[key]
            yield from selectors(target, schema, prefix)
        for variant in [*node.get("anyOf", []), *node.get("oneOf", [])]:
            yield from selectors(variant, schema, prefix)
        for name, child in node.get("properties", {}).items():
            path = f"{prefix}.{name}" if prefix else name
            if name.endswith(("_id", "_ids")):
                yield path, name
            if child.get("type") == "array":
                yield from selectors(child.get("items", {}), schema, path + ".*")
            else:
                yield from selectors(child, schema, path)

    missing = []
    for name in dir(StructuredIdentityBankingTools):
        method = getattr(StructuredIdentityBankingTools, name)
        if not getattr(method, TOOL_ATTR, False):
            continue
        schema = as_tool(method).params.model_json_schema()
        for path, selector in selectors(schema, schema):
            if (
                selector in PUBLIC_ID_ARGUMENTS
                or selector == "answer_id"
                or selector in LOCAL_ID_ARGUMENTS.get(name, set())
            ):
                continue
            collection = ARGUMENT_COLLECTIONS.get(selector)
            if collection is None or arguments.get(name, {}).get(path) != collection:
                missing.append(f"{name}.{path}")
    if missing:
        raise ValueError(f"Missing protected tool mapping: {missing}")


def resource_catalog(db: dict) -> dict[str, dict[str, list[str]]]:
    """Group immutable initial resource owners; created records are learned later."""
    result = {}
    for collection in sorted(
        set(ARGUMENT_COLLECTIONS.values())
        | {c for collections in RESULT_COLLECTIONS.values() for c in collections}
    ):
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
    validate_protected_arguments(arguments)
    for name, fields in COMMON_READ_ACCESS.items():
        if arguments.get(name) != fields:
            raise ValueError(f"Shared read access/schema mismatch: {name}")
    # These customer-only selectors are applied by the common banking evaluator.
    # Do not duplicate their identical definition in all scenario files.
    arguments = {
        name: fields
        for name, fields in arguments.items()
        if name not in COMMON_READ_ACCESS
    }
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
            "pre_auth_argument_values": {"ask_client": {"request.type": "identity"}},
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
    original = read_task_text(TASK_FILE)
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
    if is_task_index(TASK_FILE):
        print(render_task_patch(TASK_FILE, json.loads(original), tasks), end="")
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
