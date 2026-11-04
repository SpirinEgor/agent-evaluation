"""Shared exact-money, operation-status and period-scoped CRM primitives."""

from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from tau2.environment.tool import ToolExecutionRejected


def cents(value):
    """Validate a finite monetary value with at most two decimal places."""
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.quantize(Decimal("0.01")):
            raise ValueError
        return int(number * 100)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(
            "ERR_INVALID_ARGUMENT: Сумма должна быть точной в копейках"
        ) from None


def money(value):
    return float(Decimal(value) / 100)


def standard_cycle(db, customer_id, period):
    """Resolve an explicit cycle; legacy transaction-ID cycles remain unchanged."""
    return next(
        (
            c
            for c in db.cashback_rules[customer_id].cycles
            if c["period"] == period
            and c.get("program", "standard_cashback") == "standard_cashback"
        ),
        None,
    )


def cycle_purchases(db, customer_id, cycle):
    """Build the full bank register from declared product scope, never a ticket."""
    if cycle.get("rules_revision") == "multi-program-v1":
        obligations = reward_obligations(db, customer_id, cycle["period"])
        return [db.transactions[x] for x in obligations]
    if cycle.get("rules_revision") != "posting-v2":
        return [db.transactions[x] for x in cycle["transaction_ids"]]
    return [
        t
        for t in db.transactions.values()
        if t.customer_id == customer_id
        and t.kind == "purchase"
        and t.status == "posted"
        and t.reward_program == cycle["program"]
        and t.card_id in cycle["card_ids"]
        and t.posted_at
        and cycle["posting_from"] <= t.posted_at[:10] <= cycle["posting_to"]
    ]


def cashback_period(db, tx, program, period):
    """The explicit rule revision determines the date used for period checks."""
    cycle = (
        standard_cycle(db, tx.customer_id, period)
        if program
        in {"standard_cashback", "cashback_reconciliation", "partner_cashback"}
        else None
    )
    if cycle and cycle.get("rules_revision") in {"posting-v2", "multi-program-v1"}:
        return tx in cycle_purchases(db, tx.customer_id, cycle)
    return tx.date.startswith(period)


def linked_posted(db, tx, cycle):
    """Complete settled relations as of the cycle's declared reconciliation date."""
    cutoff = cycle.get("settlement_as_of", db.today)
    return [
        t
        for t in db.transactions.values()
        if t.original_transaction_id == tx.id
        and t.status == "posted"
        and (t.customer_id, t.account_id, t.card_id)
        == (tx.customer_id, tx.account_id, tx.card_id)
        and (
            cycle.get("rules_revision") not in {"posting-v2", "multi-program-v1"}
            or (t.posted_at or t.date)[:10] <= cutoff
        )
    ]


def rules_with_register(db, customer_id):
    """Return complete cycle/settlement IDs and raw conditions, without entitlements."""
    rules = db.cashback_rules[customer_id].model_copy(deep=True)
    for cycle in rules.cycles:
        from tau2.domains.banking_ru.cashback_state import revision, settlements

        cycle["state_revision"] = revision(db, customer_id, cycle["period"])
        cycle["settlements"] = settlements(db, customer_id, cycle["period"])
        if cycle.get("rules_revision") == "multi-program-v1":
            for field in ["transaction_ids", "related_posted_ids", "registry_complete"]:
                cycle.pop(field, None)
            continue
        if cycle.get("rules_revision") != "posting-v2":
            continue
        purchases = sorted(
            cycle_purchases(db, customer_id, cycle), key=lambda t: (t.posted_at, t.id)
        )
        cycle["transaction_ids"] = [t.id for t in purchases]
        cycle["related_posted_ids"] = {
            t.id: sorted(
                x.id
                for x in linked_posted(db, t, cycle)
                if x.kind in {"refund", "cashback"}
            )
            for t in purchases
        }
        cycle["registry_complete"] = True
    return rules


def purchase_total(db, tx):
    """Aggregate explicit processing fragments, never infer a group by merchant."""
    fragments = [t for t in db.transactions.values() if t.purchase_group_id == tx.id]
    for part in fragments:
        if (
            any(
                getattr(part, key) != getattr(tx, key)
                for key in (
                    "customer_id",
                    "account_id",
                    "card_id",
                    "receipt_id",
                    "date",
                    "kind",
                    "status",
                )
            )
            or part.kind != "purchase"
            or not part.posted_at
            or part.posted_at[:7] != tx.posted_at[:7]
        ):
            raise ValueError(
                "ERR_INVALID_PURCHASE_GROUP: Несогласованные проводки покупки"
            )
    return cents(tx.amount) + sum(cents(t.amount) for t in fragments)


def reward_obligations(db, customer_id, period):
    """Calculate contract obligations in integer kopecks; never return via MCP."""
    from datetime import datetime

    cycle = standard_cycle(db, customer_id, period)
    if not cycle or cycle.get("rules_revision") != "multi-program-v1":
        return None
    contracts = [
        c
        for c in db.reward_contracts.values()
        if c.customer_id == customer_id
        and c.rules_revision == cycle["rules_revision"]
        and (not cycle.get("contract_id") or c.id == cycle["contract_id"])
        and c.effective_from <= cycle["posting_to"]
        and (not c.effective_to or c.effective_to >= cycle["posting_from"])
    ]
    if len(contracts) != 1:
        raise ValueError("ERR_INVALID_ARGUMENT: Неоднозначный договор вознаграждений")
    contract = contracts[0]
    purchases = sorted(
        [
            t
            for t in db.transactions.values()
            if t.customer_id == customer_id
            and t.account_id == contract.account_id
            and t.card_id in contract.card_ids
            and t.kind == "purchase"
            and t.purchase_group_id is None
            and t.status == "posted"
            and t.posted_at
            and cycle["posting_from"] <= t.posted_at[:10] <= cycle["posting_to"]
            and contract.effective_from <= t.date
            and (not contract.effective_to or t.date <= contract.effective_to)
        ],
        key=lambda t: (datetime.fromisoformat(t.posted_at), t.id),
    )
    available = cents(cycle["limit"])
    from tau2.domains.banking_ru.reward_payment_register import (
        allocated_reward_payments,
        basis_reward_payments,
    )

    allocated_paid = allocated_reward_payments(
        db, customer_id, cycle.get("settlement_as_of", db.today)
    )
    basis_paid = basis_reward_payments(
        db, customer_id, cycle.get("settlement_as_of", db.today)
    )
    rows = {}
    for tx in purchases:
        linked = linked_posted(db, tx, cycle)
        base = max(
            0,
            purchase_total(db, tx)
            - sum(cents(t.amount) for t in linked if t.kind == "refund"),
        )
        partners = []
        for offer in db.promotions.values():
            if not (
                offer.customer_id == customer_id
                and offer.product_code == contract.product_code
                and offer.program
                and offer.replaces_standard
                and offer.valid_from
                and offer.valid_from <= tx.date <= offer.valid_until
                and offer.merchant == tx.merchant
                and tx.mcc in offer.mcc_codes
            ):
                continue
            if offer.activation_required:
                if not tx.purchased_at:
                    raise ValueError(
                        "ERR_INVALID_ARGUMENT: Не получено точное время покупки"
                    )
                purchased = datetime.fromisoformat(tx.purchased_at)
                if not any(
                    a.customer_id == customer_id
                    and a.promotion_id == offer.id
                    and datetime.fromisoformat(a.activated_at) <= purchased
                    and (
                        not a.revoked_at
                        or purchased < datetime.fromisoformat(a.revoked_at)
                    )
                    for a in db.promotion_activations.values()
                ):
                    continue
            partners.append(offer)
        if len(partners) > 1:
            raise ValueError(
                "ERR_INVALID_ARGUMENT: Неоднозначная партнёрская программа"
            )
        if partners:
            offer = partners[0]
            accrued = int(
                (Decimal(base) * Decimal(str(offer.rate))).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            entitlement = min(accrued, cents(offer.per_purchase_cap))
            program = offer.program
        else:
            history = [
                h
                for h in db.cashback_rules[customer_id].categories_history
                if h["effective_from"] <= tx.date
                and (not h.get("effective_to") or tx.date <= h["effective_to"])
                and h.get("product_code", contract.product_code)
                == contract.product_code
            ]
            if len(history) != 1:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: Неоднозначная историческая ставка"
                )
            rate = next(
                (
                    c["rate"]
                    for c in history[0]["categories"]
                    if tx.mcc in c["mcc_codes"]
                ),
                0,
            )
            accrued = int(
                (Decimal(base) * Decimal(str(rate))).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            entitlement = min(accrued, available)
            available -= entitlement
            program = "standard_cashback"
        paid = sum(
            cents(t.amount)
            for t in linked
            if t.kind == "cashback" or t.compensates_cashback
        )
        paid += allocated_paid.get(tx.id, 0) + basis_paid.get((tx.id, program), 0)
        rows[tx.id] = {
            "program": program,
            "entitlement": entitlement,
            "paid": paid,
            "gap": max(0, entitlement - paid),
        }
    return rows


def standard_gaps(db, customer_id, period):
    """Internal double-compensation guard; never expose ready-made underpayments."""
    rules = db.cashback_rules[customer_id]
    cycle = standard_cycle(db, customer_id, period)
    if cycle is None:
        return None
    if cycle.get("rules_revision") == "multi-program-v1":
        return {
            key: row["gap"]
            for key, row in reward_obligations(db, customer_id, period).items()
        }
    purchases = cycle_purchases(db, customer_id, cycle)
    purchases.sort(key=lambda t: (t.posted_at or t.date, t.id))
    available = cents(cycle["limit"])
    gaps = {}
    for tx in purchases:
        if (
            tx.customer_id != customer_id
            or tx.kind != "purchase"
            or tx.status != "posted"
        ):
            raise ValueError("ERR_INVALID_ARGUMENT: Некорректный состав цикла")
        linked = linked_posted(db, tx, cycle)
        base = max(
            0,
            cents(tx.amount)
            - sum(cents(t.amount) for t in linked if t.kind == "refund"),
        )
        history = [
            h
            for h in rules.categories_history
            if h["effective_from"] <= tx.date
            and (not h.get("effective_to") or tx.date <= h["effective_to"])
        ]
        if len(history) != 1:
            raise ValueError("ERR_INVALID_ARGUMENT: Неоднозначная историческая ставка")
        if history[0].get("program", "standard_cashback") != "standard_cashback":
            raise ValueError("ERR_INVALID_ARGUMENT: Другая историческая программа")
        rate = next(
            (c["rate"] for c in history[0]["categories"] if tx.mcc in c["mcc_codes"]), 0
        )
        accrued = int(
            (Decimal(base) * Decimal(str(rate))).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        entitlement = min(accrued, available)
        available -= entitlement
        paid = sum(cents(t.amount) for t in linked if t.kind == "cashback")
        gaps[tx.id] = max(0, entitlement - paid)
    return gaps


def register_operation(tools, request_id, customer_id, result, unknown=False):
    """Persist stable business facts; replay keys are session state, not outcomes."""
    stable = deepcopy(result)
    stable.pop("account_balance", None)
    tools.db.bank_operations[request_id] = {
        **stable,
        "id": request_id,
        "customer_id": customer_id,
    }
    return (
        {
            "id": request_id,
            "request_id": request_id,
            "customer_id": customer_id,
            "status": "outcome_unknown",
        }
        if unknown
        else {"request_id": request_id, **result}
    )


def _resolution_paid_claim(db, case, transaction_id):
    """A claim can be reconciled only against a real payment for this case."""
    for operation in db.reward_resolution_executions.values():
        if (
            operation.customer_id != case.customer_id
            or operation.case_id != case.id
            or operation.period != case.reward_period
            or transaction_id not in operation.transaction_ids
            or operation.result.get("status") != "succeeded"
        ):
            continue
        for record in operation.result.get("ledger", []):
            credit = db.transactions.get(record.get("id"))
            if (
                credit is not None
                and credit.model_dump() == record
                and credit.customer_id == case.customer_id
                and credit.original_transaction_id == transaction_id
                and credit.kind == "cashback"
                and credit.status == "posted"
                and cents(credit.amount) > 0
            ):
                return True
    return False


class RewardWorkflowMixin:
    def _bank_operation_status(self, customer_id, request_id):
        self._require_verified(customer_id)
        result = self.db.bank_operations.get(request_id)
        if result is None:
            raise ValueError("ERR_NOT_FOUND: Запрос операции не найден")
        if result["customer_id"] != customer_id:
            raise ToolExecutionRejected(
                "ERR_FOREIGN_RESOURCE: Запрос принадлежит другому клиенту"
            )
        return deepcopy(result)

    def _get_reward_investigations(self, customer_id, program=None, period=None):
        self._get_customer(customer_id)
        self._require_verified(customer_id)
        from tau2.domains.banking_ru.cashback_state import revision

        return [
            c.model_copy(
                update={
                    "state_revision": revision(self.db, customer_id, c.reward_period)
                }
            )
            for c in self.db.cases.values()
            if c.customer_id == customer_id
            and c.category == "cashback_investigation"
            and (program is None or c.reward_program == program)
            and (period is None or c.reward_period == period)
        ]

    def _amend_reward_investigation(
        self, case_id, items, reserved_adjustments=None, expected_revision=None
    ):
        from tau2.domains.banking_ru.data_model import (
            CashbackInvestigationAmendmentItem,
            CashbackInvestigationItem,
        )

        case = self.db.cases.get(case_id)
        if case is None:
            raise ValueError("ERR_NOT_FOUND: Дело не найдено")
        self._require_verified(case.customer_id)
        if case.category != "cashback_investigation" or case.status != "open":
            raise ValueError("ERR_INVALID_ARGUMENT: Требуется открытое расследование")
        if case.review_status == "registered":
            raise ValueError(
                "ERR_REVIEW_REGISTERED: Состав переданного дела зафиксирован"
            )
        values = [
            CashbackInvestigationAmendmentItem.model_validate(
                x.model_dump() if hasattr(x, "model_dump") else x
            )
            for x in items
        ]
        if not values or len({x.transaction_id for x in values}) != len(values):
            raise ValueError("ERR_INVALID_ARGUMENT: Нужны разные покупки")
        if case.reward_program in {"standard_cashback", "cashback_reconciliation"}:
            return self._amend_standard_investigation(
                case, values, reserved_adjustments, expected_revision
            )
        # Other programs retain their positive-only append contract.
        values = [
            CashbackInvestigationItem.model_validate(x.model_dump()) for x in values
        ]
        existing = {x.transaction_id: x for x in case.reward_items}
        for item in values:
            tx = self._get_transaction(item.transaction_id)
            if (
                tx.customer_id != case.customer_id
                or tx.kind != "purchase"
                or tx.status != "posted"
                or (case.reward_period and not tx.date.startswith(case.reward_period))
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: Покупка другого владельца или периода"
                )
            cents(item.amount)
            if (
                item.transaction_id in existing
                and existing[item.transaction_id].amount != item.amount
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: Нельзя заменить уже учтённую сумму"
                )
        added = [x for x in values if x.transaction_id not in existing]
        if not added:
            return case
        case.reward_items = sorted(
            [*case.reward_items, *added], key=lambda x: x.transaction_id
        )
        case.transaction_ids = [x.transaction_id for x in case.reward_items]
        case.amount = money(sum(cents(x.amount) for x in case.reward_items))
        case.version += 1
        self._revise_case_document(case)
        return case

    def _amend_standard_investigation(
        self, case, values, reserved_adjustments, expected_revision=None
    ):
        """Set final per-purchase claims; preserve prior claims and reserve unpaid credits."""
        from tau2.domains.banking_ru.data_model import (
            CashbackAdjustmentReservation,
            CashbackInvestigationItem,
        )

        reservations = [
            CashbackAdjustmentReservation.model_validate(x)
            for x in (reserved_adjustments or [])
        ]
        existing = {x.transaction_id: x for x in case.reward_items}
        for item in values:
            tx = self._get_transaction(item.transaction_id)
            if (
                tx.customer_id != case.customer_id
                or tx.kind != "purchase"
                or tx.status != "posted"
                or not cashback_period(
                    self.db, tx, case.reward_program, case.reward_period
                )
            ):
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: Покупка другого владельца или периода"
                )
            if cents(item.amount) == 0 and (
                not _resolution_paid_claim(self.db, case, item.transaction_id)
                or (
                    standard_gaps(self.db, case.customer_id, case.reward_period) or {}
                ).get(item.transaction_id)
                != 0
            ):
                raise ToolExecutionRejected(
                    "ERR_CLAIM_NOT_SETTLED: Удаление возможно только после полной выплаты по делу"
                )
            if item.transaction_id in existing and cents(item.amount) < cents(
                existing[item.transaction_id].amount
            ):
                gaps = standard_gaps(self.db, case.customer_id, case.reward_period)
                reserved = sum(
                    cents(row.amount)
                    for row in reservations
                    if row.transaction_id == item.transaction_id
                )
                # A validated reservation preserves the claim while its payment
                # is pending; authority and exact totals are checked below before writes.
                reserved_remainder = reserved > 0 and cents(
                    item.amount
                ) + reserved == gaps.get(item.transaction_id)
                paid_remainder = _resolution_paid_claim(
                    self.db, case, item.transaction_id
                ) and cents(item.amount) == gaps.get(item.transaction_id)
                if not (reserved_remainder or paid_remainder):
                    raise ValueError(
                        "ERR_INVALID_ARGUMENT: Уменьшение возможно только до фактического остатка после выплаты по делу или с подтверждённым резервом"
                    )
        # An amendment of another position must not silently cancel its reserve.
        touched = {item.transaction_id for item in values}
        provided = {row.permission_id for row in reservations}
        reservations.extend(
            row.model_copy(deep=True)
            for row in case.adjustment_reservations
            if row.transaction_id not in touched and row.permission_id not in provided
        )
        # Validate authority independently of the version/pending conflict. Exact
        # current underpayment validation follows the optimistic-state guard.
        from tau2.domains.banking_ru.cashback_state import (
            advance,
            expected_state,
            revision,
            state_guard,
        )

        historical = expected_state(
            self.db, case.customer_id, case.reward_period, expected_revision
        )
        self._validate_reward_reservations(
            case.customer_id,
            case.reward_period,
            case.reward_program,
            values,
            reservations,
            state_snapshot=historical,
        )

        blocked = state_guard(
            self.db, case.customer_id, case.reward_period, expected_revision
        )
        if blocked:
            return blocked
        self._validate_reward_reservations(
            case.customer_id,
            case.reward_period,
            case.reward_program,
            values,
            reservations,
        )
        merged = {**existing, **{x.transaction_id: x for x in values}}
        merged = {
            tid: CashbackInvestigationItem.model_validate(item.model_dump())
            for tid, item in merged.items()
            if cents(item.amount) > 0
        }
        # Other open claims for a purchase cannot be silently overwritten/duplicated.
        for other in self.db.cases.values():
            if (
                other.id != case.id
                and other.customer_id == case.customer_id
                and other.status == "open"
                and other.reward_program
                in {
                    case.reward_program,
                    "cashback_reconciliation",
                    "standard_cashback",
                    "partner_cashback",
                }
                and set(merged) & set(other.transaction_ids)
            ):
                raise ValueError("ERR_DOUBLE_COMPENSATION: Покупка уже в другом деле")
        final = sorted(merged.values(), key=lambda x: x.transaction_id)
        reservations = [
            r
            for r in reservations
            if not self.db.cashback_adjustment_permissions[r.permission_id]["used"]
        ]
        if final == case.reward_items and reservations == case.adjustment_reservations:
            return case.model_copy(
                update={
                    "state_revision": revision(
                        self.db, case.customer_id, case.reward_period
                    )
                }
            )
        case.reward_items = final
        case.transaction_ids = [x.transaction_id for x in final]
        case.amount = money(sum(cents(x.amount) for x in final))
        case.adjustment_reservations = reservations
        case.version += 1
        self._revise_case_document(case)
        advance(self.db, case.customer_id, case.reward_period)
        return case.model_copy(
            update={
                "state_revision": revision(
                    self.db, case.customer_id, case.reward_period
                )
            }
        )

    def _revise_case_document(self, case):
        from tau2.domains.banking_ru.data_model import Document

        key = f"{case.id}:registration_confirmation"
        old = self.db.documents.get(key)
        if old:
            archived = old.model_copy(deep=True)
            archived.id = key + ":version:" + old.version
            archived.readiness = "superseded"
            self.db.documents[archived.id] = archived
        self.db.documents[key] = Document(
            id=key,
            customer_id=case.customer_id,
            case_id=case.id,
            title="Подтверждение регистрации",
            body=f"Дело {case.id}; программа {case.reward_program}; период {case.reward_period}; сумма {case.amount} ₽.",
            created_at=self.db.today,
            purpose="registration_confirmation",
            audience="customer",
            transmissible=True,
            version=str(case.version),
            readiness="ready",
            allowed_channels=["verified_sms", "verified_email"],
        )

    def _validate_reward_reservations(
        self, customer_id, period, program, values, reservations, state_snapshot=None
    ):
        gaps = (
            standard_gaps(self.db, customer_id, period)
            if program in {"standard_cashback", "cashback_reconciliation"} and period
            else None
        )
        if state_snapshot:
            gaps = state_snapshot["gaps"]
        reserved = {}
        for row in reservations:
            p = self.db.cashback_adjustment_permissions.get(row.permission_id)
            if (
                not p
                or p["customer_id"] != customer_id
                or p["transaction_id"] != row.transaction_id
                or p["program"]
                != (
                    reward_obligations(self.db, customer_id, period)
                    .get(row.transaction_id, {})
                    .get("program")
                    if program == "cashback_reconciliation"
                    else program
                )
                or p.get("period") not in [None, period]
                or p["status"] != "approved"
                or not p["valid_from"] <= self.db.today <= p["valid_to"]
                or cents(row.amount) > cents(p["max_amount"])
            ):
                raise ValueError(
                    "ERR_ADJUSTMENT_NOT_AUTHORIZED: Резерв не подтверждён полномочием"
                )
            if row.transaction_id in reserved:
                raise ValueError("ERR_INVALID_ARGUMENT: Повторный резерв покупки")
            cycle = standard_cycle(self.db, customer_id, period)
            if cycle and cycle.get("rules_revision") in {
                "posting-v2",
                "multi-program-v1",
            }:
                amounts = (
                    state_snapshot["permissions"].get(row.permission_id, p)
                    if state_snapshot
                    else p
                )
                remaining = cents(amounts.get("remaining_amount", 0))
                used = cents(amounts.get("used_amount", 0))
                if remaining:
                    if cents(row.amount) > remaining:
                        raise ValueError(
                            "ERR_ADJUSTMENT_NOT_AUTHORIZED: Недостаточный остаток полномочия"
                        )
                    reserved[row.transaction_id] = cents(row.amount)
                elif used >= cents(row.amount):
                    reserved[row.transaction_id] = 0
                else:
                    raise ValueError(
                        "ERR_ADJUSTMENT_NOT_AUTHORIZED: Резерв не исполнен и недоступен"
                    )
                continue
            used = cents(p.get("used_amount", p["max_amount"] if p["used"] else 0))
            if used not in [0, cents(row.amount)]:
                raise ValueError(
                    "ERR_INVALID_ARGUMENT: Несовместимое использование полномочия"
                )
            if not used and cents(row.amount) > cents(
                p.get("remaining_amount", p["max_amount"])
            ):
                raise ValueError(
                    "ERR_ADJUSTMENT_NOT_AUTHORIZED: Недостаточный остаток полномочия"
                )
            reserved[row.transaction_id] = 0 if used else cents(row.amount)
        if gaps is not None and any(
            cents(x.amount)
            != max(0, gaps.get(x.transaction_id, 0) - reserved.get(x.transaction_id, 0))
            for x in values
        ):
            if state_snapshot:
                # A stale request can be lawful against its observed snapshot or
                # the current state. Both routes merely receive a safe conflict;
                # an amount invalid in both states remains a forbidden attempt.
                return self._validate_reward_reservations(
                    customer_id, period, program, values, reservations
                )
            self._compensation_conflicts.add(customer_id)
            raise ValueError(
                "ERR_DOUBLE_COMPENSATION: Состав расследования пересекается с выплатой"
            )
