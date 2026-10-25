"""Author-only integer oracle. Never imports production resolution helpers."""

import hashlib
import json
from decimal import Decimal
from fractions import Fraction
from itertools import product
from pathlib import Path

from bank2_multisource_oracle import CASE, CUSTOMER, A, B, E
from bank2_multisource_oracle import calculate as obligations


def cents(x):
    return int(Decimal(str(x)) * 100)


def deduction_cents(row):
    fixed = cents(row.get("settlement_deduction", 0))
    rate = Fraction(str(row.get("settlement_deduction_rate", 0)))
    if fixed < 0 or not 0 <= rate < 1:
        raise ValueError("Invalid fixed or percentage deduction")
    exact = cents(row["amount"]) * rate
    whole, remainder = divmod(exact.numerator, exact.denominator)
    rounded = whole + int(
        2 * remainder > exact.denominator
        or (2 * remainder == exact.denominator and whole % 2 == 1)
    )
    return fixed + rounded


def net_cents(row):
    gross = cents(row["amount"])
    deduction = deduction_cents(row)
    if not 0 <= deduction < gross:
        raise ValueError("Deduction must leave a positive customer payment")
    return gross - deduction


def quarter_menu_choices(offer, free):
    """Exact two-budget bitset DP when every component has the same 1:3 split.

    Returns one optimal witness, not an enumeration of all optimal subsets.
    Every optimum has the same payout allocation, so all get the same DB grade.
    """
    groups = {}
    for row in offer["components"]:
        if row.get("required_companions"):
            raise ValueError("Dependencies require a separate feasibility certificate")
        if cents(row.get("settlement_deduction", 0)) or row.get(
            "settlement_deduction_rate", 0
        ):
            raise ValueError("Deductions require a separate net-value certificate")
        amount, bank, partner, first = (
            cents(row[k])
            for k in ["amount", "bank_cost", "partner_cost", "allocation_first_amount"]
        )
        if (
            amount <= 0
            or min(bank, partner) < 0
            or bank + partner != amount
            or first * 4 != amount
        ):
            raise ValueError(
                "The bitset proof requires funded positive quarter-split components"
            )
        groups.setdefault(row["exclusive_group"], []).append((row["id"], bank, partner))
    first = offer["allocation_first_transaction_id"]
    (second,) = set(offer["transaction_ids"]) - {first}
    cap = min(cents(offer["amount"]), free[first] * 4, free[second] * 4 // 3)
    bank_cap, partner_cap = (
        cents(offer["selection_budget"][k]) for k in ["bank", "partner"]
    )
    if cap <= 0:
        return []
    layers = [{0: 1}]
    for group in groups.values():
        prior = layers[-1]
        next_layer = dict(prior)
        for _, bank, partner in group:
            for old_b, bits in prior.items():
                new_b = old_b + bank
                max_p = min(partner_cap, cap - new_b)
                if new_b > bank_cap or max_p < partner:
                    continue
                shifted = (bits << partner) & ((1 << (max_p + 1)) - 1)
                if shifted:
                    next_layer[new_b] = next_layer.get(new_b, 0) | shifted
        layers.append(next_layer)
    total, b, p = max(
        (b + bits.bit_length() - 1, b, bits.bit_length() - 1)
        for b, bits in layers[-1].items()
    )
    if not total:
        return []
    selected = []
    for i, group in reversed(list(enumerate(groups.values()))):
        prior = layers[i]
        if (prior.get(b, 0) >> p) & 1:
            continue
        for ident, bank, partner in group:
            if (
                b >= bank
                and p >= partner
                and (prior.get(b - bank, 0) >> (p - partner)) & 1
            ):
                selected.append(ident)
                b -= bank
                p -= partner
                break
        else:
            raise AssertionError("No predecessor for reachable funding state")
    assert b == p == 0 and total % 4 == 0
    return [
        dict(
            component_ids=sorted(selected),
            additional=total,
            allocation={first: total // 4, second: total * 3 // 4},
            all_optima_enumerated=False,
        )
    ]


def menu_choices(offer, free):
    """Author-only exhaustive product; no runtime selectors or optimization helpers."""
    if any(
        p.get("incompatible_with")
        or p.get("required_companions")
        or cents(p.get("settlement_deduction", 0))
        or p.get("settlement_deduction_rate", 0)
        for p in offer["components"]
    ):
        return compatibility_choices(offer, free)
    groups = {}
    for p in offer["components"]:
        groups.setdefault(p["exclusive_group"], [None]).append(
            (
                p["id"],
                cents(p["amount"]),
                cents(p["bank_cost"]),
                cents(p["partner_cost"]),
                cents(p["allocation_first_amount"]),
            )
        )
    if len(groups) > 12:
        return quarter_menu_choices(offer, free)
    budget = offer["selection_budget"]
    first = offer["allocation_first_transaction_id"]
    (second,) = set(offer["transaction_ids"]) - {first}
    bank, partner = cents(budget["bank"]), cents(budget["partner"])
    maximum, winners = 0, []
    for choice in product(*groups.values()):
        selected = [x for x in choice if x]
        if not selected:
            continue
        amount = sum(x[1] for x in selected)
        a = sum(x[4] for x in selected)
        if (
            sum(x[2] for x in selected) > bank
            or sum(x[3] for x in selected) > partner
            or amount > cents(offer["amount"])
            or a > free[first]
            or amount - a > free[second]
        ):
            continue
        row = dict(
            component_ids=sorted(x[0] for x in selected),
            additional=amount,
            allocation={first: a, second: amount - a},
        )
        if amount > maximum:
            maximum, winners = amount, [row]
        elif amount == maximum:
            winners.append(row)
    return winners


def component_fingerprint(components):
    return hashlib.sha256(
        json.dumps(sorted(components, key=lambda r: r["id"]), sort_keys=True).encode()
    ).hexdigest()


def compatibility_choices(offer, free, certificate=None):
    """Prove a group/funding/contract upper bound via a fully checked feasible selection.

    Use either search or an explicit constructive certificate. Reject invalid
    certificates and unsupported/unsolved instances; a partial search result
    is never labelled as the true optimum.
    """
    from bank2_compatibility_oracle import solve, valid

    groups = {}
    for row in offer["components"]:
        groups.setdefault(row["exclusive_group"], []).append(row)
    rows = list(groups.values())
    assert all(len(group) == 3 for group in rows)
    assert all(
        0 < cents(row["allocation_first_amount"]) < net_cents(row)
        for group in rows
        for row in group
    )
    assert all(
        cents(row["bank_cost"]) >= 0
        and cents(row["partner_cost"]) >= 0
        and cents(row["bank_cost"]) + cents(row["partner_cost"]) == cents(row["amount"])
        for group in rows
        for row in group
    )
    index = {
        row["id"]: (i, a) for i, group in enumerate(rows) for a, row in enumerate(group)
    }
    assert len(index) == len(offer["components"])
    conflicts = []
    for group in rows:
        for row in group:
            i, a = index[row["id"]]
            for other in row.get("incompatible_with", []):
                j, b = index[other]
                assert i != j
                conflicts.append((i, a, j, b))
    problem = dict(groups=len(rows), conflicts=conflicts)
    if certificate is None:
        path = Path(__file__).with_name("compatibility_certificates") / (
            component_fingerprint(offer["components"]) + ".json"
        )
        if path.exists():
            certificate = json.loads(path.read_text())["component_ids"]
    if certificate is None:
        answer, _ = solve(problem)
        assert answer is not None and valid(problem, answer)
        certificate = [rows[i][a]["id"] for i, a in enumerate(answer)]
    # A certificate may omit groups. Runtime permits this; optimality depends
    # on attaining a sound bound, never on matching a reference's cardinality.
    assert certificate and len(set(certificate)) == len(certificate)
    selected = [rows[index[ident][0]][index[ident][1]] for ident in certificate]
    assert len({row["exclusive_group"] for row in selected}) == len(selected)
    selected_ids = set(certificate)
    assert all(
        clause
        and set(clause) <= set(index)
        and any(ident in selected_ids for ident in clause)
        for row in selected
        for clause in row.get("required_companions", [])
    )
    assert all(
        not selected_ids.intersection(row.get("incompatible_with", []))
        for row in selected
    )
    first = offer["allocation_first_transaction_id"]
    (second,) = set(offer["transaction_ids"]) - {first}
    selected_amount = sum(net_cents(row) for row in selected)
    selected_first = sum(cents(row["allocation_first_amount"]) for row in selected)
    # Independently valid bounds: one option per group, contractual cap,
    # bank+partner funding (each component is fully funded), and free claims.
    upper_bound = min(
        sum(max(net_cents(r) for r in group) for group in rows),
        cents(offer["amount"]),
        cents(offer["selection_budget"]["bank"])
        + cents(offer["selection_budget"]["partner"]),
        free[first] + free[second],
    )
    # A feasible witness below this bound does not prove optimality.
    assert selected_amount == upper_bound
    assert sum(cents(row["bank_cost"]) for row in selected) <= cents(
        offer["selection_budget"]["bank"]
    )
    assert sum(cents(row["partner_cost"]) for row in selected) <= cents(
        offer["selection_budget"]["partner"]
    )
    pools = offer["selection_budget"].get("pools", [])
    limits = {p["id"]: p for p in pools}
    assert len(limits) == len(pools)
    spent = {key: 0 for key in limits}
    for part in selected:
        costs = part.get("pool_costs", {})
        assert set(costs) <= set(limits) and all(cents(v) >= 0 for v in costs.values())
        if pools:
            for source in ["bank", "partner"]:
                assert sum(
                    cents(v) for k, v in costs.items() if limits[k]["source"] == source
                ) == cents(part[source + "_cost"])
        for key, value in costs.items():
            spent[key] += cents(value)
    assert all(spent[k] <= cents(p["amount"]) for k, p in limits.items())
    assert (
        selected_first <= free[first]
        and selected_amount - selected_first <= free[second]
    )
    return [
        dict(
            component_ids=sorted(certificate),
            additional=selected_amount,
            allocation={
                first: selected_first,
                second: selected_amount - selected_first,
            },
            all_optima_enumerated=False,
        )
    ]


def calculate(db):
    calc = obligations(db)
    posted = list(db["transactions"].values())
    for batch in db["cashback_settlements"].values():
        if (
            batch["customer_id"] == CUSTOMER
            and batch["period"] == "2026-07"
            and batch["status"] == "pending"
        ):
            posted += batch["credits"]
    paid = sum(
        cents(x["amount"])
        for x in posted
        if x.get("original_transaction_id") in (A, B)
        and x["status"] == "posted"
        and (x["kind"] == "cashback" or x.get("compensates_cashback"))
    )
    reserved = {
        tid: sum(
            cents(item["amount"])
            for case in db["cases"].values()
            if case["customer_id"] == CUSTOMER and case["status"] == "open"
            for item in case.get("reward_items", [])
            if item["transaction_id"] == tid
        )
        for tid in (A, B)
    }
    offers = {}
    for key, offer in db["reward_resolution_offers"].items():
        if offer["customer_id"] != CUSTOMER or offer["case_id"] != CASE:
            continue
        if offer.get("components"):
            options = menu_choices(
                offer, {tid: calc["rows"][tid]["gap"] - reserved[tid] for tid in (A, B)}
            )
            if not options:
                offers[key] = dict(additional=0, permitted=False, allocation={})
                continue
            best = options[0]
            offers[key] = dict(
                **best,
                permitted=(
                    offer["status"] == "approved"
                    and offer["rights"] == "preserve"
                    and offer["valid_from"] <= db["today"] <= offer["valid_to"]
                ),
                optimal_selections=options,
            )
            continue
        amount = cents(offer["amount"]) - (
            paid if offer["amount_basis"] == "cumulative" else 0
        )
        first_id = offer["allocation_first_transaction_id"]
        first_amount = cents(offer["allocation_first_amount"])
        allocation = {first_id: first_amount}
        other_ids = set(offer["transaction_ids"]) - {first_id}
        if len(other_ids) == 1:
            allocation[other_ids.pop()] = amount - first_amount
        feasible = (
            set(allocation) == {A, B}
            and min(allocation.values()) > 0
            and all(
                allocation[tid] <= calc["rows"][tid]["gap"] - reserved[tid]
                for tid in allocation
            )
        )
        permitted = (
            offer["status"] == "approved"
            and offer["rights"] == "preserve"
            and offer["valid_from"] <= db["today"] <= offer["valid_to"]
            and feasible
        )
        offers[key] = dict(
            additional=amount, permitted=permitted, allocation=allocation
        )
    optimum = max(x["additional"] for x in offers.values() if x["permitted"])
    assert optimum >= calc["manual_payment"], (
        "Counterfactual needs a manual-optimum reference"
    )
    winners = [
        k for k, v in offers.items() if v["permitted"] and v["additional"] == optimum
    ]
    payouts = offers[winners[0]]["allocation"]
    claims = {
        A: calc["rows"][A]["gap"] - payouts[A],
        B: calc["rows"][B]["gap"] - payouts[B],
        E: 700,
    }
    return dict(
        offers=offers,
        winners=winners,
        maximum=optimum,
        payouts=payouts,
        claims=claims,
        case_total=sum(claims.values()),
        component_ids=offers[winners[0]].get("component_ids", []),
    )
