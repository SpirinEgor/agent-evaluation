"""Independent event-time checks of actually registered bank case snapshots."""

from copy import deepcopy
from decimal import Decimal

from tau2.evaluator.cashback_state import payload


def _canonical(value, key=None):
    if isinstance(value, dict):
        return {k: _canonical(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    if (
        key
        in {"amount", "revision", "version", "source_version", "explanation_version"}
        and value is not None
    ):
        return Decimal(str(value))
    return value


def submission_failures(observed, initial_db):
    initial_db = _canonical(initial_db)
    cases = deepcopy(initial_db.get("cases", {}))
    allocations = deepcopy(initial_db.get("payment_allocations", {}))
    bases = deepcopy(initial_db.get("payment_bases", {}))
    requests = initial_db.get("case_exchange_requests", {})
    receipts = deepcopy(initial_db.get("case_exchange_receipts", {}))
    operations = {}
    activated = set()
    failures = []
    for target in sorted(observed, key=lambda call: call.execution_index):
        result = _canonical(payload(target))
        if not isinstance(result, dict) or not result:
            continue
        name, args = target.call.name, target.call.arguments
        case_id = args.get("case_id", result.get("case_id"))
        case = cases.get(case_id)
        if name in {
            "execute_reward_resolution",
            "get_reward_resolution_status",
            "retry_reward_resolution",
        }:
            ident = result.get(
                "id", result.get("operation_id", args.get("resolution_operation_id"))
            )
            if ident:
                operations[ident] = deepcopy(result)
            if result.get("ledger") and result.get("retry_required"):
                for key, review in initial_db.get(
                    "reward_duplicate_reviews", {}
                ).items():
                    if review.get("case_id") != case_id or key in activated:
                        continue
                    if review.get("trigger_offer_id") != result.get("offer_id"):
                        continue
                    activated.add(key)
                    basis = bases.get(review.get("renew_basis_id"))
                    if basis:
                        basis["revision"] += 1
                        if review.get("replacement_basis_lines"):
                            basis["lines"] = deepcopy(review["replacement_basis_lines"])
        if (
            name == "reconcile_payment_allocation"
            and result.get("status") == "reconciled"
        ):
            basis = bases.get(args.get("basis_id"))
            prior = allocations.get(args["allocation_id"], {})
            changed = result.get("revision") != prior.get("revision") or result.get(
                "status"
            ) != prior.get("status")
            if changed and (not basis or result.get("lines") != basis.get("lines")):
                failures.append(target.call.id + ":allocation_economic_lines")
            allocations[args["allocation_id"]] = deepcopy(result)
        if (
            name == "send_case_message"
            and result.get("exchange_result", {}).get("status") == "matched"
        ):
            receipts[args.get("exchange_id")] = {"status": "matched"}
        if not case:
            continue
        if name == "process_case_instruction" and result.get("status") == "processed":
            ident = args.get("instruction_id")
            case["incoming_instructions"][ident]["status"] = "processed"
        if name == "record_case_handover" and "source_version" in result:
            case["handover_receipt"] = deepcopy(result)
        if (
            name == "send_support_message"
            and result.get("status") == "sent"
            and "case_items" in args
        ):
            case["explanation_version"] = case["version"]
        if (
            name == "submit_cashback_review"
            and result.get("review_status") == "registered"
        ):
            reasons = []
            if any(
                r.get("case_id") == case_id
                and r.get("required_for_review")
                and receipts.get(key, {}).get("status") != "matched"
                for key, r in requests.items()
            ):
                reasons.append("exchange")
            if case.get("explanation_required") and case.get(
                "explanation_version"
            ) != case.get("version"):
                reasons.append("explanation")
            if any(
                i.get("action") == "withdraw_request"
                and i.get("signature_verified")
                and i.get("status") != "processed"
                and "withdraw_request"
                in case.get("parties", {})
                .get(i.get("party_id"), {})
                .get("permissions", [])
                for i in case.get("incoming_instructions", {}).values()
            ):
                reasons.append("withdrawal")
            handover = case.get("handover_receipt") or {}
            if case.get("handover_required") and (
                handover.get("source_version") != case.get("version")
                or sorted(handover.get("items", []), key=lambda x: x["transaction_id"])
                != sorted(
                    case.get("reward_items", []), key=lambda x: x["transaction_id"]
                )
            ):
                reasons.append("handover")
            if any(
                op.get("case_id") == case_id and op.get("status") == "execution_unknown"
                for op in operations.values()
            ):
                reasons.append("pending_execution")
            initial_items = (
                initial_db.get("cases", {}).get(case_id, {}).get("reward_items", [])
            )
            tids = {
                x["transaction_id"]
                for x in initial_items + case.get("reward_items", [])
            }
            for allocation in allocations.values():
                if allocation.get("customer_id") != case[
                    "customer_id"
                ] or allocation.get("period") != case.get("reward_period"):
                    continue
                related = [
                    b
                    for b in bases.values()
                    if b.get("payment_id") == allocation.get("payment_id")
                ]
                if not any(
                    line.get("purchase_id") in tids
                    for b in related
                    for line in b.get("lines", [])
                ):
                    continue
                basis = bases.get(allocation.get("basis_id"))
                if (
                    allocation.get("status") != "reconciled"
                    or not basis
                    or allocation.get("lines") != basis.get("lines")
                ):
                    reasons.append("payment_allocation")
            failures.extend(
                target.call.id + ":submission_" + reason
                for reason in sorted(set(reasons))
            )
        if (
            name in {"amend_cashback_investigation", "submit_cashback_review"}
            and result.get("id") == case_id
        ):
            case.update(deepcopy(result))
    return failures
