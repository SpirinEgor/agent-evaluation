"""Finite source-bound dispute phrases; no task IDs, private state or NLP guesses.

Identity is selected before status/amount comparison. Public ticket context can
name a historical merchant; only a received dispute record can attest its stage.
"""

import json
import re
from decimal import Decimal

from tau2.domains.banking_ru.operation_facts import _rows
from tau2.evaluator.claim_models import ClientClaim

NUMBER = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?"
RUB = r"(?:₽|руб(?:ль|ля|лей|ли|лях|лями)?|RUB)(?!\w)"
MONEY = rf"({NUMBER})\s*{RUB}"
MONTHS = {
    "январ": "01",
    "феврал": "02",
    "март": "03",
    "апрел": "04",
    "ма": "05",
    "июн": "06",
    "июл": "07",
    "август": "08",
    "сентябр": "09",
    "октябр": "10",
    "ноябр": "11",
    "декабр": "12",
}
DATE = r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"


def _number(text):
    return Decimal(re.sub(r"[ \u00a0\u202f]", "", text).replace(",", ".")).normalize()


def _month(text):
    return next(
        (value for key, value in MONTHS.items() if text.lower().startswith(key)), None
    )


def _date(day, month):
    return f"{_month(month)}-{int(day):02}"


def received_rows(trace, facts, family, tools, position=None):
    """Yield rows tied to the exact validated assistant observation and execution."""
    selector = {"transaction": "transaction_id", "dispute": "dispute_id"}[family]
    for observed in trace.observed:
        if (
            observed.call.name not in tools
            or observed.call.requestor != "assistant"
            or observed.result is None
            or observed.result.requestor != "assistant"
            or observed.result.error
            or observed.result_position is None
            or (position is not None and observed.result_position >= position)
        ):
            continue
        try:
            payload = json.loads(observed.result.content or "null")
        except (ValueError, TypeError):
            continue
        rows = (
            payload.get("items", [])
            if observed.call.name == "get_transactions" and isinstance(payload, dict)
            else [payload]
            if observed.call.name == "get_transaction_details"
            else payload
        )
        if not isinstance(rows, list):
            continue
        validated = {
            f.resources.get(selector): f
            for f in facts
            if f.family == family
            and f.call_id == observed.call.id
            and f.received_position == observed.result_position
            and f.execution_index == observed.execution_index
            and f.requestor == "assistant"
        }
        exact_rows = {}
        for row in rows:
            if not (
                isinstance(row, dict)
                and isinstance(row.get("id"), str)
                and row["id"] in validated
            ):
                continue
            # An ID validated on a sibling does not validate this raw row's
            # owner/selectors or metadata. Reuse the unchanged source adapter
            # on this row alone before making any of its metadata available.
            row_facts = [
                f
                for f in _rows(observed, family, selector, [row])
                if f.family == family and f.resources.get(selector) == row["id"]
            ]
            if row_facts:
                exact_rows.setdefault(row["id"], []).append((row, row_facts[-1]))
        for siblings in exact_rows.values():
            row, source = siblings[0]
            # Identical duplicates add no evidence. Conflicting same-ID rows
            # have no unique metadata interpretation; do not pick by value.
            if all(other == row for other, _ in siblings[1:]):
                yield row, source


def _order(row):
    explicit = row.get("order_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    merchant = row.get("merchant", "")
    match = (
        re.search(r" / заказ ([\w-]+)$", merchant)
        if isinstance(merchant, str)
        else None
    )
    return match[1] if match else None


def _merchant(row):
    text = row.get("merchant", "")
    if not isinstance(text, str):
        return None
    return re.sub(
        r" / заказ [\w-]+$", "", re.sub(r"^Возврат от\s+", "", text)
    ).casefold()


def remainder_facts(trace, facts):
    """Derive exact purchase minus posted seller refund for one order/merchant.

    Two independent row receipts are both required; a later matching row cannot
    justify an earlier claim. Duplicate/colliding rows are not merged by amount.
    """
    rows = list(
        received_rows(
            trace, facts, "transaction", {"get_transactions", "get_transaction_details"}
        )
    )
    result = []
    for purchase, source in rows:
        order = _order(purchase)
        if (
            purchase.get("kind") != "purchase"
            or purchase.get("status") != "posted"
            or not order
        ):
            continue
        for refund, receipt in rows:
            if (
                refund.get("kind") != "fee_refund"
                or refund.get("status") != "posted"
                or not isinstance(refund.get("merchant"), str)
                or not refund["merchant"].startswith("Возврат от ")
                or not isinstance(purchase.get("customer_id"), str)
                or purchase.get("customer_id") != refund.get("customer_id")
                or _order(refund) != order
                or _merchant(refund) != _merchant(purchase)
            ):
                continue
            try:
                gross, returned = (
                    _number(str(purchase["amount"])),
                    _number(str(refund["amount"])),
                )
                purchase_date, refund_date = purchase["date"], refund["date"]
                if not (
                    gross.is_finite()
                    and returned.is_finite()
                    and 0 < returned <= gross
                    and isinstance(purchase_date, str)
                    and isinstance(refund_date, str)
                ):
                    continue
            except (KeyError, ValueError, ArithmeticError):
                continue
            result.append(
                source.model_copy(
                    update={
                        "family": "order_remainder",
                        "kind": "historical",
                        "resources": {
                            "order_id": order,
                            "transaction_id": purchase["id"],
                            "refund_id": refund["id"],
                        },
                        "values": {
                            "purchase_amount": str(gross),
                            "refund_amount": str(returned),
                            "purchase_date": purchase_date[5:10],
                            "refund_date": refund_date[5:10],
                        },
                        "amount": gross - returned,
                        "received_position": max(
                            source.received_position, receipt.received_position
                        ),
                        "call_id": (
                            source
                            if source.received_position >= receipt.received_position
                            else receipt
                        ).call_id,
                    }
                )
            )
    return result


def _claim(sentence, position, channel, **kwargs):
    return ClientClaim(
        text=sentence, emitted_position=position, channel=channel, **kwargs
    )


def _purchase(trace, facts, order, position):
    rows = list(
        received_rows(
            trace,
            facts,
            "transaction",
            {"get_transactions", "get_transaction_details"},
            position,
        )
    )
    ids = {
        row["id"]
        for row, _ in rows
        if row.get("kind") == "purchase" and _order(row) == order
    }
    refunds = {
        row["id"]
        for row, _ in rows
        if row.get("kind") == "fee_refund" and _order(row) == order
    }
    issue = "ambiguous_resource" if len(ids) > 1 or len(refunds) > 1 else None
    return (
        next(iter(ids))
        if len(ids) == 1
        else None
        if len(ids) > 1
        else f"unresolved-order:{order}"
    ), issue


def _dispute_resource(trace, facts, transaction, position):
    if transaction is None:
        return {}, "ambiguous_resource"
    valid_receipts = {
        (o.call.id, o.result_position, o.execution_index)
        for o in trace.observed
        if o.call.requestor == "assistant"
        and o.result is not None
        and o.result.requestor == "assistant"
        and not o.result.error
    }
    ids = {
        f.resources["dispute_id"]
        for f in facts
        if f.family == "dispute"
        and f.requestor == "assistant"
        and f.received_position < position
        and (f.call_id, f.received_position, f.execution_index) in valid_receipts
        and f.resources.get("transaction_id") == transaction
        and "dispute_id" in f.resources
    }
    if len(ids) == 1:
        return {"dispute_id": next(iter(ids))}, None
    if len(ids) > 1:
        return {}, "ambiguous_resource"
    return {"transaction_id": transaction}, None


def bound_dispute_claims(trace, facts, sentence, predicate, position, channel, ticket):
    """Return claims for four declared constructions, or None for generic handling."""
    net = re.fullmatch(
        rf"\s*По повторному списанию {MONEY} от {DATE} учли возврат {MONEY} "
        rf"того же заказа ([\w-]+), поэтому спор открыт на оставшиеся {MONEY}[.!]?\s*",
        sentence,
        re.I,
    )
    if net and predicate == "открыт":
        gross, day, month, refund, order, remainder = net.groups()
        target, issue = _purchase(trace, facts, order, position)
        dispute, dispute_issue = _dispute_resource(trace, facts, target, position)
        return [
            _claim(
                sentence,
                position,
                channel,
                family="order_remainder",
                kind="historical",
                resources={
                    "order_id": order,
                    **({"transaction_id": target} if target is not None else {}),
                },
                values={
                    "purchase_amount": str(_number(gross)),
                    "refund_amount": str(_number(refund)),
                    "purchase_date": _date(day, month),
                },
                amount=_number(remainder),
                unresolved_reason=issue,
            ),
            _claim(
                sentence,
                position,
                channel,
                family="dispute",
                kind="current_state",
                resources=dispute,
                values={"status": "under_review"},
                amount=_number(remainder),
                unresolved_reason=issue or dispute_issue,
            ),
        ]
    seller = re.fullmatch(
        rf"\s*По заказу ([\w-]+) продавец уже вернул {MONEY} {DATE}, "
        rf"поэтому непокрытая сумма возможного спора — {MONEY}, а не {MONEY}[.!]?\s*",
        sentence,
        re.I,
    )
    if seller and predicate.startswith("вернул"):
        order, refund, day, month, remainder, excluded = seller.groups()
        target, issue = _purchase(trace, facts, order, position)
        return [
            _claim(
                sentence,
                position,
                channel,
                family="order_remainder",
                kind="historical",
                resources={
                    "order_id": order,
                    **({"transaction_id": target} if target is not None else {}),
                },
                values={
                    "refund_amount": str(_number(refund)),
                    "refund_date": _date(day, month),
                },
                amount=_number(remainder),
                # "а не" excludes a remainder; it does not assert the price.
                # Equality is a contradiction independent of source availability.
                unresolved_reason="contradictory_amount"
                if _number(excluded) == _number(remainder)
                else issue,
            )
        ]
    plural = re.fullmatch(
        rf"\s*По провед[её]нным после блокировки операциям ([\w-]+) на {MONEY} и ([\w-]+) "
        rf"на {MONEY} (открыты|зарегистрированы|завершены|завершёны) два спора на полные суммы[;.!]?\s*",
        sentence,
        re.I,
    )
    if plural:
        first, amount1, second, amount2, stage = plural.groups()
        rows = list(
            received_rows(
                trace,
                facts,
                "transaction",
                {"get_transactions", "get_transaction_details"},
                position,
            )
        )
        bindings = []
        for label in (first, second):
            ids = {
                row["id"]
                for row, _ in rows
                if row["id"] == label
                or (
                    isinstance(row.get("merchant"), str)
                    and re.fullmatch(
                        re.escape(label) + r"(?: Systems| Games)?",
                        row["merchant"],
                        re.I,
                    )
                )
            }
            bindings.append(ids)
        ambiguous = any(len(ids) > 1 for ids in bindings) or bool(
            bindings[0] & bindings[1]
        )
        result = []
        for label, amount, ids in zip(
            (first, second), (amount1, amount2), bindings, strict=True
        ):
            target = (
                next(iter(ids)) if len(ids) == 1 else f"unresolved-merchant:{label}"
            )
            dispute, dispute_issue = _dispute_resource(trace, facts, target, position)
            # The governed list also states the purchase amount and posted stage;
            # a matching partial-dispute receipt alone cannot prove "full amounts".
            source_resource = {"transaction_id": target} if not ambiguous else {}
            result.extend(
                [
                    _claim(
                        sentence,
                        position,
                        channel,
                        family="transaction",
                        kind="historical",
                        resources=source_resource,
                        values={"kind": "purchase"},
                        amount=_number(amount),
                        unresolved_reason="ambiguous_resource" if ambiguous else None,
                    ),
                    _claim(
                        sentence,
                        position,
                        channel,
                        family="transaction",
                        kind="current_state",
                        resources=source_resource,
                        values={"status": "posted"},
                        unresolved_reason="ambiguous_resource" if ambiguous else None,
                    ),
                ]
            )
            result.append(
                _claim(
                    sentence,
                    position,
                    channel,
                    family="dispute",
                    kind="current_state",
                    resources=dispute if not ambiguous else {},
                    values={
                        "status": "completed"
                        if stage.startswith("заверш")
                        else "under_review"
                    },
                    amount=_number(amount),
                    unresolved_reason="ambiguous_resource"
                    if ambiguous
                    else dispute_issue,
                )
            )
        return result
    historical = re.fullmatch(
        r"\s*Заверш[её]нный (\w+) спор по ([\w-]+) — другой спор[.!]?\s*",
        sentence,
        re.I,
    )
    if historical and predicate.startswith("заверш"):
        adjective, merchant = historical.groups()
        month = _month(adjective)
        public = bool(
            re.search(
                r"Летом у вас был ещё один спор по " + re.escape(merchant) + r"(?!\w)",
                ticket,
                re.I,
            )
        )
        ids = {
            row["id"]
            for row, _ in received_rows(
                trace, facts, "dispute", {"get_disputes"}, position
            )
            if isinstance(row.get("filed_at"), str) and row["filed_at"][5:7] == month
        }
        issue = (
            "unsupported_historical_alias"
            if not public or month not in {"06", "07", "08"}
            else "ambiguous_resource"
            if len(ids) > 1
            else None
        )
        return [
            _claim(
                sentence,
                position,
                channel,
                family="dispute",
                kind="current_state",
                resources={"dispute_id": next(iter(ids))}
                if len(ids) == 1
                else {}
                if len(ids) > 1
                else {"dispute_id": "unresolved-month:" + adjective},
                values={"status": "completed"},
                unresolved_reason=issue,
            )
        ]
    if predicate.endswith("ный") and not re.fullmatch(
        r"\s*Заверш[её]нный спор [a-zA-Z][a-zA-Z0-9_-]* — другой спор[.!]?\s*",
        sentence,
        re.I,
    ):
        # Adding one adjective must not let a convenient completed record resolve
        # unrelated merchant labels or an otherwise unsupported noun phrase.
        return [
            _claim(
                sentence,
                position,
                channel,
                family="dispute",
                kind="unresolved",
                resources={},
                values={},
                unresolved_reason="unsupported_claim_form",
            )
        ]
    return None
