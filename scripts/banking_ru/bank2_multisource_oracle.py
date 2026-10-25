"""Independent author-only Bank2 v4 oracle, integer kopecks and fraction arithmetic."""

import argparse
import json
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

CUSTOMER = "gromov_a_1187"
PERIOD = "2026-07"
C, E, A, B, D = PURCHASES = (
    "txn_804201",
    "txn_804205",
    "txn_804219",
    "txn_804227",
    "txn_804231",
)
CASE = "case_610903"
CAPABILITY = "cap_620184"
PROMOTION = "promo_730415"
LATE = "txn_804287"
COMPENSATION = "txn_804265"


def money(value):
    return int(Decimal(str(value)) * 100)


def rounded(base, rate):
    fraction = Fraction(str(rate))
    return (2 * base * fraction.numerator + fraction.denominator) // (
        2 * fraction.denominator
    )


def variant(db, name):
    db = deepcopy(db)
    selected = B
    if name == "late_activation":
        db["promotion_activations"]["pa_730416"]["activated_at"] = (
            "2026-07-26T13:00:00+03:00"
        )
    elif name == "without_compensation":
        db["transactions"].pop(COMPENSATION)
    elif name == "without_authority":
        db["cashback_adjustment_permissions"][CAPABILITY]["transaction_id"] = D
    elif name == "select_other_receipt":
        selected = D
    elif name != "main":
        raise ValueError(name)
    return db, selected


def calculate(db, selected=B, include_pending=True):
    """Calculate independently from raw facts, never import production reward code."""
    contract = db["reward_contracts"]["rc_610482"]
    cycle = next(
        c for c in db["cashback_rules"][CUSTOMER]["cycles"] if c["period"] == PERIOD
    )
    txs = deepcopy(db["transactions"])
    if include_pending:
        for batch in db.get("cashback_settlements", {}).values():
            if batch["customer_id"] == CUSTOMER and batch["period"] == PERIOD:
                for credit in batch["credits"]:
                    txs.setdefault(credit["id"], credit)
    purchases = sorted(
        (
            t
            for t in txs.values()
            if t["customer_id"] == CUSTOMER
            and t.get("card_id") in contract["card_ids"]
            and t["kind"] == "purchase"
            and t["status"] == "posted"
            and cycle["posting_from"]
            <= t.get("posted_at", "")[:10]
            <= cycle["posting_to"]
        ),
        key=lambda t: (datetime.fromisoformat(t["posted_at"]), t["id"]),
    )
    remaining = money(cycle["limit"])
    rows = {}
    offer = db["promotions"][PROMOTION]
    for t in purchases:
        linked = [
            x
            for x in txs.values()
            if x.get("original_transaction_id") == t["id"]
            and x["status"] == "posted"
            and (x.get("posted_at") or x["date"])[:10] <= cycle["settlement_as_of"]
            and all(
                x.get(k) == t.get(k) for k in ("customer_id", "account_id", "card_id")
            )
        ]
        net = max(
            0,
            money(t["amount"])
            - sum(money(x["amount"]) for x in linked if x["kind"] == "refund"),
        )
        active = any(
            a["customer_id"] == CUSTOMER
            and a["promotion_id"] == PROMOTION
            and datetime.fromisoformat(a["activated_at"])
            <= datetime.fromisoformat(t["purchased_at"])
            and (
                not a.get("revoked_at")
                or datetime.fromisoformat(t["purchased_at"])
                < datetime.fromisoformat(a["revoked_at"])
            )
            for a in db["promotion_activations"].values()
        )
        partner = (
            offer["valid_from"] <= t["date"] <= offer["valid_until"]
            and t["merchant"] == offer["merchant"]
            and t.get("mcc") in offer["mcc_codes"]
            and active
        )
        if partner:
            program = "partner_cashback"
            entitlement = min(
                rounded(net, offer["rate"]), money(offer["per_purchase_cap"])
            )
        else:
            program = "standard_cashback"
            history = next(
                h
                for h in db["cashback_rules"][CUSTOMER]["categories_history"]
                if h["effective_from"] <= t["date"]
                and (not h["effective_to"] or t["date"] <= h["effective_to"])
            )
            rate = next(
                (
                    c["rate"]
                    for c in history["categories"]
                    if t.get("mcc") in c["mcc_codes"]
                ),
                0,
            )
            entitlement = min(rounded(net, rate), remaining)
            remaining -= entitlement
        paid = sum(
            money(x["amount"])
            for x in linked
            if x["kind"] == "cashback" or x.get("compensates_cashback")
        )
        rows[t["id"]] = dict(
            program=program,
            net=net,
            entitlement=entitlement,
            paid=paid,
            gap=max(0, entitlement - paid),
        )
    capability = db["cashback_adjustment_permissions"][CAPABILITY]
    prior = db["cases"][CASE]
    claimed = sum(
        money(x["amount"]) for x in prior["reward_items"] if x["transaction_id"] == A
    )
    valid = (
        capability["customer_id"] == CUSTOMER
        and capability["transaction_id"] == A
        and capability["program"] == rows[A]["program"]
        and capability["period"] == PERIOD
        and capability["status"] == "approved"
        and capability["valid_from"] <= db["today"] <= capability["valid_to"]
    )
    payment = (
        min(money(capability["remaining_amount"]), max(0, rows[A]["gap"] - claimed))
        if valid
        else 0
    )
    items = {
        x["transaction_id"]: money(x["amount"])
        for x in prior["reward_items"]
        if x["transaction_id"] not in (A, selected)
    }
    items[A] = rows[A]["gap"] - payment
    if rows[selected]["gap"]:
        items[selected] = rows[selected]["gap"]
    return dict(
        rows=rows,
        manual_payment=payment,
        late_payment=700,
        case_items=items,
        case_total=sum(items.values()),
        case_increment=sum(items.values()) - money(prior["amount"]),
        balance_delta=payment + 700,
        standard_limit_used=money(cycle["limit"]) - remaining,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    db = json.loads(args.db.read_text())
    print(
        json.dumps(
            {
                name: calculate(*[variant(db, name)[0]], selected=variant(db, name)[1])
                for name in (
                    "main",
                    "late_activation",
                    "without_compensation",
                    "without_authority",
                    "select_other_receipt",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
