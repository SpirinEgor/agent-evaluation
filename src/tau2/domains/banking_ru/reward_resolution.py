"""Bank offer execution: raw terms, exact client consent and atomic ledger effects."""

from copy import deepcopy
from decimal import ROUND_HALF_EVEN, Decimal

from tau2.domains.banking_ru.cashback_state import (
    advance,
    pending_funding,
    revision,
    state_guard,
)
from tau2.domains.banking_ru.component_eligibility import validate_eligibility
from tau2.domains.banking_ru.reward_duplicate_review import (
    activate,
    public_reviews,
    require_unrestricted,
    validate_trigger,
)
from tau2.domains.banking_ru.reward_funding import (
    reserve_key,
    reserve_members,
    reserve_snapshot,
)
from tau2.domains.banking_ru.reward_resolution_models import (
    RewardResolutionExecution,
    RewardResolutionSettlement,
)
from tau2.domains.banking_ru.reward_workflows import (
    cents,
    money,
    reward_obligations,
    standard_gaps,
)
from tau2.environment.tool import ToolExecutionRejected


def owned_offer(tools, offer_id, case_id):
    offer = tools.db.reward_resolution_offers.get(offer_id)
    if offer is None:
        raise ValueError("ERR_NOT_FOUND: Предложение не найдено")
    tools._require_verified(offer.customer_id)
    case = tools.db.cases.get(case_id)
    if case is None:
        raise ValueError("ERR_NOT_FOUND: Дело не найдено")
    if case.customer_id != offer.customer_id or offer.case_id != case_id:
        raise ToolExecutionRejected("ERR_FOREIGN_OWNER: Предложение и дело не связаны")
    return offer, case


def validate_fallback(tools, offer, operation_id):
    """A fallback is conditional on the exact released predecessor scope."""
    if not offer.fallback_for_offer_id:
        if operation_id is not None:
            raise ToolExecutionRejected(
                "ERR_FALLBACK_NOT_AVAILABLE: Предложение не резервное"
            )
        return
    op = tools.db.reward_resolution_executions.get(operation_id)
    if (
        op is None
        or op.customer_id != offer.customer_id
        or op.case_id != offer.case_id
        or op.period != offer.period
        or set(op.transaction_ids) != set(offer.transaction_ids)
        or op.packet.get("offer_id") != offer.fallback_for_offer_id
        or op.result.get("status") != "failed_final"
        or not op.result.get("released_funding")
        or op.result.get("funding_charge")
        or op.result.get("funding_hold")
    ):
        raise ToolExecutionRejected(
            "ERR_FALLBACK_NOT_AVAILABLE: Нет окончательного отказа и освобождения резерва"
        )


def _post_credits(tools, credits):
    # Every credit and destination was validated before accepting the intent.
    for credit in credits:
        tools.db.transactions[credit.id] = credit
        account = tools.db.accounts[credit.account_id]
        account.balance = money(cents(account.balance) + cents(credit.amount))
        tools.db.transactions[credit.original_transaction_id].cashback_granted = True


def _settle(tools, operation, *, retry_pending=False):
    if operation.result.get("status") != "execution_unknown":
        return deepcopy(operation.result)
    from tau2.domains.banking_ru.data_model import Transaction

    plan = operation.pending_settlement
    if plan.retry_required and not retry_pending:
        return deepcopy(operation.result)
    result = {
        k: deepcopy(v) for k, v in operation.result.items() if k != "funding_hold"
    }
    result.update(status=plan.final_status, ledger=[])
    if plan.final_status == "succeeded":
        require_unrestricted(
            tools.db,
            operation.customer_id,
            [row["original_transaction_id"] for row in plan.ledger],
        )
        if plan.retry_required:
            if public_reviews(
                tools.db, operation.customer_id, operation.case_id
            ) and operation.result.get("state_revision") != revision(
                tools.db, operation.customer_id, operation.period
            ):
                raise ToolExecutionRejected(
                    "ERR_RESOLUTION_CONSENT_STALE: После решения проверки требуется актуальное согласие и пересмотр намерения"
                )
            gaps = standard_gaps(tools.db, operation.customer_id, operation.period)
            if any(
                cents(row["amount"]) > gaps.get(row["original_transaction_id"], 0)
                for row in plan.ledger
            ):
                raise ToolExecutionRejected(
                    "ERR_RESOLUTION_RECONCILIATION_REQUIRED: Остаток обязательства изменился; требуется пересмотр неисполненной части"
                )
        credits = [Transaction.model_validate(row) for row in plan.ledger]
        _post_credits(tools, credits)
        result.update(
            total_amount=plan.total_amount,
            ledger=deepcopy(operation.result.get("ledger", []) + plan.ledger),
        )
        result.pop("pending_total_amount", None)
        result.pop("retry_required", None)
        result.pop("failed_transaction_ids", None)
        if plan.funding_charge is not None:
            result["funding_charge"] = deepcopy(plan.funding_charge)
    elif plan.funding_charge is not None:
        result["released_funding"] = deepcopy(plan.funding_charge)
    result["state_revision"] = advance(
        tools.db, operation.customer_id, operation.period
    )
    reviews = public_reviews(tools.db, operation.customer_id, operation.case_id)
    if reviews:
        result["duplicate_reviews"] = reviews
    operation.result = result
    operation.pending_settlement = None
    return deepcopy(result)


def get_status(tools, resolution_operation_id):
    from tau2.domains.banking_ru.processing_notes import with_processing_notes

    operation = tools.db.reward_resolution_executions.get(resolution_operation_id)
    if operation is None:
        raise ToolExecutionRejected("ERR_NOT_FOUND: Операция урегулирования не найдена")
    tools._require_verified(operation.customer_id)
    result = _settle(tools, operation)
    reviews = public_reviews(tools.db, operation.customer_id, operation.case_id)
    if reviews:
        result["duplicate_reviews"] = reviews
    return with_processing_notes(
        tools.db,
        result,
        operation.customer_id,
        "resolution",
        operation.packet["offer_id"],
    )


def retry(tools, resolution_operation_id):
    operation = tools.db.reward_resolution_executions.get(resolution_operation_id)
    if operation is None:
        raise ToolExecutionRejected("ERR_NOT_FOUND: Операция урегулирования не найдена")
    tools._require_verified(operation.customer_id)
    return _settle(tools, operation, retry_pending=True)


def normalized(items):
    rows = [x.model_dump() if hasattr(x, "model_dump") else deepcopy(x) for x in items]
    if len({x["transaction_id"] for x in rows}) != len(rows):
        raise ValueError("ERR_INVALID_ARGUMENT: Повтор позиции")
    return sorted(rows, key=lambda x: x["transaction_id"])


def component_deduction_cents(part):
    rate = Decimal(str(getattr(part, "settlement_deduction_rate", 0)))
    fixed = cents(getattr(part, "settlement_deduction", 0))
    if not rate.is_finite() or not 0 <= rate < 1 or fixed < 0:
        raise ValueError("Некорректные условия удержания")
    percentage = Decimal(str(part.amount)) * rate * 100
    return fixed + int(percentage.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def selected_amounts(offer, component_ids):
    """Check one presented menu selection; deliberately do not find an optimum."""
    if not component_ids or len(set(component_ids)) != len(component_ids):
        raise ValueError("Пустой или повторяющийся набор компонентов")
    choices = {p.id: p for p in offer.components}
    if (
        len(choices) != len(offer.components)
        or not set(component_ids) <= choices.keys()
    ):
        raise ValueError("Неизвестный компонент")
    selected = [choices[k] for k in component_ids]
    if len({p.exclusive_group for p in selected}) != len(selected):
        raise ValueError("Компоненты одной группы взаимоисключаются")
    selected_ids = set(component_ids)
    for part in selected:
        for clause in getattr(part, "required_companions", []):
            if (
                not clause
                or not set(clause) <= choices.keys()
                or not selected_ids.intersection(clause)
            ):
                raise ValueError("Не выполнены зависимости компонентов предложения")
    if any(
        selected_ids.intersection(getattr(p, "incompatible_with", [])) for p in selected
    ):
        raise ValueError("Несовместимые компоненты предложения")
    budget = offer.selection_budget
    if budget is None or (
        sum(cents(p.bank_cost) for p in selected) > cents(budget.bank)
        or sum(cents(p.partner_cost) for p in selected) > cents(budget.partner)
    ):
        raise ValueError("Превышен бюджет составного предложения")
    singleton = (
        len(offer.transaction_ids) == 1
        and offer.transaction_ids[0] == offer.allocation_first_transaction_id
    )
    if any(
        cents(p.bank_cost) + cents(p.partner_cost) != cents(p.amount)
        or not 0 <= component_deduction_cents(p) < cents(p.amount)
        or not 0 < cents(p.allocation_first_amount)
        or (
            cents(p.allocation_first_amount)
            != cents(p.amount) - component_deduction_cents(p)
            if singleton
            else cents(p.allocation_first_amount)
            >= cents(p.amount) - component_deduction_cents(p)
        )
        for p in selected
    ):
        raise ValueError("Несогласованные банковские условия компонента")
    pools = getattr(budget, "pools", [])
    by_pool = {p.id: p for p in pools}
    if len(by_pool) != len(pools):
        raise ValueError("Повтор бюджета программы")
    consumed = {ident: 0 for ident in by_pool}
    for part in selected:
        costs = getattr(part, "pool_costs", {})
        if not set(costs) <= by_pool.keys() or any(
            cents(x) < 0 for x in costs.values()
        ):
            raise ValueError("Неизвестный или отрицательный расход программы")
        if pools:
            for source in ["bank", "partner"]:
                allocated = sum(
                    cents(v) for k, v in costs.items() if by_pool[k].source == source
                )
                if allocated != cents(getattr(part, source + "_cost")):
                    raise ValueError(
                        "Расходы программ не совпадают с источником финансирования"
                    )
        for ident, value in costs.items():
            consumed[ident] += cents(value)
    if any(consumed[k] > cents(p.amount) for k, p in by_pool.items()):
        raise ValueError("Превышен бюджет отдельной программы")
    total = sum(cents(p.amount) - component_deduction_cents(p) for p in selected)
    first_amount = sum(cents(p.allocation_first_amount) for p in selected)
    first = offer.allocation_first_transaction_id
    rest = set(offer.transaction_ids) - {first}
    if (not singleton and len(rest) != 1) or total > cents(offer.amount):
        raise ValueError("Неверная область или превышен предел предложения")
    if singleton:
        return total, {first: first_amount}
    return total, {first: first_amount, rest.pop(): total - first_amount}


def list_offers(tools, customer_id, case_id):
    from tau2.domains.banking_ru.processing_notes import with_processing_notes

    tools._get_customer(customer_id)
    tools._require_verified(customer_id)
    case = tools.db.cases.get(case_id)
    if case is None:
        raise ValueError("ERR_NOT_FOUND: Дело не найдено")
    if case.customer_id != customer_id:
        raise ToolExecutionRejected("ERR_FOREIGN_OWNER: Чужое дело")
    results = []
    for offer in tools.db.reward_resolution_offers.values():
        if offer.customer_id != customer_id or offer.case_id != case_id:
            continue
        row = offer.model_dump()
        if row["fallback_for_offer_id"] is None:
            row.pop("fallback_for_offer_id")
        row.update(
            state_revision=revision(tools.db, customer_id, offer.period),
            funding_status="pending" if pending_funding(tools.db, offer) else "final",
            component_count=len(offer.components),
            components_complete=len(offer.components) <= 128,
        )
        if not row["components_complete"]:
            row["components"] = []
        correction_profile = tools.db.reward_resolution_correction_profiles.get(
            offer.id
        )
        row["amendment_allowed"] = bool(
            correction_profile and correction_profile.allow_reallocation
        )
        reserve = reserve_snapshot(tools.db, offer)
        if reserve is not None:
            row["funding_reserve"] = reserve
        results.append(
            with_processing_notes(tools.db, row, customer_id, "offer", offer.id)
        )
    return results


def list_components(tools, offer_id, offset=0, limit=50):
    offer = tools.db.reward_resolution_offers.get(offer_id)
    if offer is None:
        raise ValueError("ERR_NOT_FOUND: Предложение не найдено")
    tools._require_verified(offer.customer_id)
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("ERR_INVALID_ARGUMENT: offset >= 0; 1 <= limit <= 100")
    total = len(offer.components)
    return dict(
        offer_id=offer_id,
        offer_version=offer.version,
        total=total,
        offset=offset,
        next_offset=offset + limit if offset + limit < total else None,
        components=[p.model_dump() for p in offer.components[offset : offset + limit]],
    )


def minimum_snapshot(tools, customer_id, case_id, transaction_ids):
    """Bind consent to current instructions for this offer's purchases only."""
    preferences = tools.db.reward_resolution_preferences.get(customer_id)
    if preferences is None:
        return {}
    result = {}
    for tid in transaction_ids:
        if tid not in preferences.minimum_payouts:
            continue
        key = (customer_id, case_id, tid)
        result[tid] = (
            tools._resolution_minimum_overrides.get(
                key, cents(preferences.minimum_payouts[tid])
            ),
            tools._resolution_minimum_revisions.get(key, 0),
        )
    return result


def change_minimum(tools, customer_id, transaction_id, request):
    tools._require_verified(customer_id)
    transaction = tools._get_transaction(transaction_id)
    case = tools.db.cases.get(request.case_id)
    if transaction.customer_id != customer_id or (
        case is not None and case.customer_id != customer_id
    ):
        raise ToolExecutionRejected("ERR_FOREIGN_OWNER: Чужая операция или дело")
    if case is None:
        raise ValueError("ERR_NOT_FOUND: Дело не найдено")
    if not any(
        offer.customer_id == customer_id
        and offer.case_id == case.id
        and transaction_id in offer.transaction_ids
        for offer in tools.db.reward_resolution_offers.values()
    ):
        raise ToolExecutionRejected(
            "ERR_MINIMUM_SCOPE: Операция вне области урегулирования"
        )
    preferences = tools.db.reward_resolution_preferences.get(customer_id)
    initial = preferences.minimum_payouts.get(transaction_id) if preferences else None
    key = (customer_id, case.id, transaction_id)
    current = tools._resolution_minimum_overrides.get(key, cents(initial or 0))
    requested = cents(request.minimum_amount)
    # Repeating the latest answer is harmless; an older answer never restores an override.
    for answer in reversed(tools._resolution_minimum_answers.values()):
        data = answer["data"]
        if (data["customer_id"], data["case_id"], data["transaction_id"]) == key:
            if (
                cents(data["requested_minimum"]) == requested
                and cents(data["effective_minimum"]) == current
            ):
                return deepcopy(answer)
            break
    accepted = initial is not None and (
        cents(preferences.minimum_payout_reduction_limits.get(transaction_id, initial))
        <= requested
        <= cents(initial)
    )
    effective = requested if accepted else current
    answer = dict(
        customer_id=customer_id,
        request_type=request.type,
        status="accepted" if accepted else "declined",
        answer_id=f"resolution-minimum-answer-{len(tools._resolution_minimum_answers) + 1}",
        data=dict(
            customer_id=customer_id,
            case_id=case.id,
            transaction_id=transaction_id,
            previous_minimum=money(current),
            requested_minimum=money(requested),
            effective_minimum=money(effective),
        ),
    )
    if accepted and effective != current:
        tools._resolution_minimum_overrides[key] = effective
        tools._resolution_minimum_revisions[key] = (
            tools._resolution_minimum_revisions.get(key, 0) + 1
        )
    tools._resolution_minimum_answers[answer["answer_id"]] = deepcopy(answer)
    return answer


def below_minimum(rows, snapshot):
    amounts = {row["transaction_id"]: cents(row["amount"]) for row in rows}
    return any(amounts.get(tid, 0) < minimum for tid, (minimum, _) in snapshot.items())


def confirm(tools, customer_id, request):
    offer, case = owned_offer(tools, request.offer_id, request.case_id)
    if customer_id != offer.customer_id:
        raise ToolExecutionRejected("ERR_FOREIGN_OWNER: Чужое предложение")
    validate_fallback(tools, offer, request.fallback_operation_id)
    packet = request.model_dump(exclude={"type"})
    if packet["resolution_operation_id"] is not None:
        from tau2.domains.banking_ru.reward_resolution_amendment import (
            confirm_amendment,
        )

        return confirm_amendment(tools, customer_id, request)
    packet.pop("resolution_operation_id")
    if packet["fallback_operation_id"] is None:
        packet.pop("fallback_operation_id")
    if not packet["eligibility_evidence"]:
        packet.pop("eligibility_evidence")
    if offer.components or packet["component_ids"]:
        packet["component_ids"] = sorted(packet["component_ids"])
    else:
        packet.pop("component_ids")
    for key in ("payouts", "resulting_claims"):
        packet[key] = normalized(packet[key])
        for item in packet[key]:
            tx = tools._get_transaction(item["transaction_id"])
            if tx.customer_id != customer_id:
                raise ToolExecutionRejected("ERR_FOREIGN_OWNER: Чужая операция")
    preferences = tools.db.reward_resolution_preferences.get(customer_id)
    if preferences is None:
        return {"status": "unsupported_context", "data": None}
    # Client accepts the presented package, not its mathematical correctness or bank approval.
    snapshot = minimum_snapshot(tools, customer_id, case.id, offer.transaction_ids)
    declined = (
        below_minimum(packet["payouts"], snapshot)
        or offer.rights == "full_settlement"
        and (
            preferences.preserve_unpaid_claims or preferences.preserve_other_case_items
        )
    )
    base = dict(
        customer_id=customer_id,
        request_type=request.type,
        data=packet,
        question=f"Подтвердите предложение {offer.id}, версия {request.offer_version}: новая выплата {request.total_amount:g} ₽. Выбранные компоненты, проводки и полный остаток дела показаны в data; условия прав — {offer.rights}.",
        rights=offer.rights,
        status="declined" if declined else "confirmed",
    )
    if declined:
        return base
    for cid, old in tools._resolution_consents.items():
        if (
            old == packet
            and tools._resolution_consent_minimums.get(cid, {}) == snapshot
        ):
            return {**base, "confirmation_id": cid}
    cid = f"resolution-confirmation-{len(tools._resolution_consents) + 1}"
    tools._resolution_consents[cid] = packet
    tools._resolution_consent_minimums[cid] = snapshot
    return {**base, "confirmation_id": cid}


def execute(
    tools,
    offer_id,
    offer_version,
    payouts,
    total_amount,
    case_id,
    confirmation_id,
    expected_revision,
    idempotency_key,
    component_ids=None,
    eligibility_evidence=None,
    fallback_operation_id=None,
):
    """Execute one chosen offer; never choose or advertise the optimal offer."""
    offer, case = owned_offer(tools, offer_id, case_id)
    from tau2.domains.banking_ru.reward_payment_register import (
        require_reconciled_payments,
    )

    require_reconciled_payments(
        tools.db, offer.customer_id, offer.period, set(offer.transaction_ids)
    )

    def reject(message):
        tools._resolution_violations.add(offer.customer_id)
        raise ValueError("ERR_RESOLUTION_NOT_AUTHORIZED: " + message)

    from tau2.domains.banking_ru.discovery_workflow import require_bank_acceptance

    rows = normalized(payouts)
    packet = dict(
        offer_id=offer_id,
        offer_version=offer_version,
        payouts=rows,
        total_amount=total_amount,
        case_id=case_id,
    )
    if fallback_operation_id is not None:
        packet["fallback_operation_id"] = fallback_operation_id
    if offer.components or component_ids:
        packet["component_ids"] = sorted(component_ids or [])
    evidence = {
        k: (v.model_dump() if hasattr(v, "model_dump") else deepcopy(v))
        for k, v in (eligibility_evidence or {}).items()
    }
    if evidence:
        packet["eligibility_evidence"] = evidence
    # A replay is matched before revisions/consumption, but still after ownership.
    for op in tools.db.reward_resolution_executions.values():
        if op.customer_id != offer.customer_id:
            continue
        if op.idempotency_key == idempotency_key:
            if op.packet != {**packet, "confirmation_id": confirmation_id}:
                raise ToolExecutionRejected(
                    "ERR_IDEMPOTENCY_CONFLICT: Ключ использован с другим пакетом"
                )
            return _settle(tools, op)
        if op.packet.get("offer_id") == offer_id or (
            set(op.transaction_ids) & set(offer.transaction_ids)
            and op.period == offer.period
            and op.result.get("status") != "failed_final"
        ):
            raise ToolExecutionRejected(
                "ERR_RESOLUTION_ALREADY_SELECTED: Уже выбран способ урегулирования этих покупок"
            )
    validate_fallback(tools, offer, fallback_operation_id)
    if not idempotency_key.strip():
        reject("Пустой ключ")
    if pending_funding(tools.db, offer):
        return {
            "status": "funding_pending",
            "offer_id": offer.id,
            "current_offer_version": offer.version,
            "current_revision": revision(tools.db, offer.customer_id, offer.period),
        }
    if offer.version != offer_version:
        return {
            "status": "offer_changed",
            "offer_id": offer.id,
            "expected_offer_version": offer_version,
            "current_offer_version": offer.version,
            "current_revision": revision(tools.db, offer.customer_id, offer.period),
        }
    try:
        require_bank_acceptance(
            tools, offer.customer_id, offer.transaction_ids, case_id=offer.case_id
        )
    except ValueError as exc:
        reject(str(exc))
    if (
        offer.status != "approved"
        or not offer.valid_from <= tools.db.today <= offer.valid_to
    ):
        reject("Нет действующего банковского одобрения")
    if case.status != "open":
        reject("Дело закрыто")
    if any(
        c.customer_id == offer.customer_id
        and c.review_status == "registered"
        and set(offer.transaction_ids) & {x.transaction_id for x in c.review_items}
        for c in tools.db.cases.values()
    ):
        reject("ERR_REVIEW_REGISTERED: Требования по покупкам переданы на рассмотрение")
    preferences = tools.db.reward_resolution_preferences.get(offer.customer_id)
    if (
        offer.rights == "full_settlement"
        and preferences
        and (
            preferences.preserve_unpaid_claims or preferences.preserve_other_case_items
        )
    ):
        reject("Клиент не разрешал отказ от требований")
    snapshot = minimum_snapshot(
        tools, offer.customer_id, case.id, offer.transaction_ids
    )
    if below_minimum(rows, snapshot):
        raise ToolExecutionRejected(
            "ERR_CUSTOMER_MINIMUM: Выплата ниже минимума клиента"
        )
    for p in tools.db.cashback_adjustment_permissions.values():
        if (
            p["customer_id"] == offer.customer_id
            and p.get("period") == offer.period
            and p["transaction_id"] in offer.transaction_ids
            and cents(p.get("used_amount", 0))
        ):
            reject("Ручная корректировка уже выбрана")
    consent = tools._resolution_consents.get(confirmation_id)
    if offer.fallback_for_offer_id and (
        not consent or consent.get("fallback_operation_id") != fallback_operation_id
    ):
        raise ToolExecutionRejected(
            "ERR_FALLBACK_NOT_AVAILABLE: Нет согласия на точное основание резерва"
        )
    if consent and consent.get("eligibility_evidence", {}) != evidence:
        raise ToolExecutionRejected(
            "ERR_COMPONENT_NOT_APPLICABLE: Основания не соответствуют согласованному пакету"
        )
    if not consent or any(consent.get(k) != v for k, v in packet.items()):
        reject("Нет согласия на точный пакет")
    if tools._resolution_consent_minimums.get(confirmation_id, {}) != snapshot:
        return {
            "status": "consent_stale",
            "current_revision": revision(tools.db, offer.customer_id, offer.period),
        }
    # Staleness is a recoverable condition, not an unlawful payment.
    blocked = state_guard(tools.db, offer.customer_id, offer.period, expected_revision)
    if blocked:
        return blocked
    if consent["state_revision"] != expected_revision:
        return {
            "status": "consent_stale",
            "current_revision": revision(tools.db, offer.customer_id, offer.period),
        }
    if set(x["transaction_id"] for x in rows) != set(offer.transaction_ids):
        reject("Область выплаты отличается от предложения")
    try:
        validate_eligibility(
            offer,
            component_ids or [],
            evidence,
            transactions=tools.db.transactions,
            contracts=tools.db.reward_contracts,
            incidents=tools.db.service_incidents,
            confirmations=tools.db.partner_confirmations,
        )
    except ValueError as exc:
        raise ToolExecutionRejected(str(exc)) from exc
    obligations = reward_obligations(tools.db, offer.customer_id, offer.period)
    gaps = standard_gaps(tools.db, offer.customer_id, offer.period)
    paid = sum(
        cents(t.amount)
        for t in tools.db.transactions.values()
        if t.customer_id == offer.customer_id
        and t.original_transaction_id in offer.transaction_ids
        and t.status == "posted"
        and (t.kind == "cashback" or t.compensates_cashback)
    )
    if offer.components:
        try:
            total, expected = selected_amounts(offer, component_ids or [])
        except ValueError as exc:
            reject(str(exc))
        if offer.amount_basis != "incremental":
            reject("Составное предложение должно быть incremental")
    else:
        if component_ids:
            reject("У предложения нет компонентов")
        total = cents(offer.amount) - (
            paid if offer.amount_basis == "cumulative" else 0
        )
        first = offer.allocation_first_transaction_id
        expected = {first: cents(offer.allocation_first_amount)}
        rest = set(offer.transaction_ids) - {first}
        if len(rest) != 1:
            reject("Неподдерживаемое распределение")
        expected[rest.pop()] = total - expected[first]
    if (
        cents(total_amount) != total
        or {x["transaction_id"]: cents(x["amount"]) for x in rows} != expected
        or min(expected.values()) <= 0
    ):
        reject("Неверная сумма или распределение")
    for tid, amount in expected.items():
        tx = tools._get_transaction(tid)
        if tx.customer_id != offer.customer_id or amount > gaps.get(tid, 0):
            reject("Чужая покупка или превышение обязательства")
        if tools._get_account(tx.account_id).status != "active":
            reject("Счёт закрыт")
        reserved = sum(
            cents(item.amount)
            for existing in tools.db.cases.values()
            if existing.customer_id == offer.customer_id
            and existing.status == "open"
            and existing.id != case.id
            for item in existing.reward_items
            if item.transaction_id == tid
        )
        if reserved + amount > gaps.get(tid, 0):
            reject("Сумма уже заявлена в расследовании")
    expected_claims = {x.transaction_id: cents(x.amount) for x in case.reward_items}
    # A shared reserve settles one full claim scope even before CRM records all
    # purchases. Unrelated existing case positions retain their declared amounts.
    for member in reserve_members(tools.db, offer):
        for tid in member.transaction_ids:
            if tid in gaps:
                expected_claims[tid] = gaps[tid]
    # CRM amendment may follow several independent payments. Keep prior paid items
    # at their real remaining obligation while preserving unrelated case positions.
    for operation in tools.db.reward_resolution_executions.values():
        if (
            operation.customer_id == offer.customer_id
            and operation.case_id == case_id
            and operation.period == offer.period
            and operation.result.get("status") == "succeeded"
        ):
            for tid in operation.transaction_ids:
                if tid in gaps:
                    expected_claims[tid] = gaps[tid]
    expected_claims.update({tid: gaps[tid] - amt for tid, amt in expected.items()})
    # Fully repaid positions are absent from the positive-only claim list.
    expected_claims = {
        tid: amount for tid, amount in expected_claims.items() if amount > 0
    }
    if {
        x["transaction_id"]: cents(x["amount"]) for x in consent["resulting_claims"]
    } != expected_claims:
        reject("Согласованный остаток требований не соответствует проводкам")
    funding_charge = None
    if reserve_key(offer) is not None:
        reserve = reserve_snapshot(tools.db, offer)
        selected = [p for p in offer.components if p.id in (component_ids or [])]
        if not selected:
            reject("Общий резерв требует составного предложения")
        costs = {
            source: sum(cents(getattr(p, source + "_cost")) for p in selected)
            for source in ("bank", "partner")
        }
        if any(
            costs[source] > cents(reserve["remaining_" + source]) for source in costs
        ):
            raise ToolExecutionRejected(
                "ERR_SHARED_FUNDING_EXHAUSTED: Недостаточно общего резерва"
            )
        funding_charge = {
            "reserve_id": reserve["id"],
            **{source: money(value) for source, value in costs.items()},
        }
    execution_id = "resolution:" + case_id
    profile = tools.db.reward_resolution_execution_profiles.get(offer_id)
    if funding_charge is not None or profile is not None:
        execution_id += ":" + offer_id
    if execution_id in tools.db.reward_resolution_executions:
        reject("Повтор записи урегулирования")
    from tau2.domains.banking_ru.data_model import Transaction

    credits = []
    for tid, amount in sorted(expected.items()):
        tx = tools._get_transaction(tid)
        credit_id = f"reward-resolution:{case_id}:{tid}"
        if credit_id in tools.db.transactions:
            reject("Повторная проводка")
        credits.append(
            Transaction(
                id=credit_id,
                customer_id=tx.customer_id,
                account_id=tx.account_id,
                card_id=tx.card_id,
                original_transaction_id=tid,
                date=tools.db.today,
                posted_at=tools.db.today + "T12:00:00+03:00",
                reward_program=obligations[tid]["program"],
                amount=money(amount),
                merchant="Выплата по соглашению",
                kind="cashback",
                status="posted",
                channel="online",
            )
        )
    deferred_tid = tools.db.reward_resolution_partial_profiles.get(offer_id)
    if deferred_tid is not None and deferred_tid not in expected:
        reject("Неверный профиль частичного исполнения")
    partial = deferred_tid is not None and profile is None
    correction = None
    if partial:
        validate_trigger(tools.db, offer)
        from tau2.domains.banking_ru.reward_resolution_amendment import (
            correction_credit,
        )

        correction = correction_credit(
            tools,
            offer,
            {tid: amount for tid, amount in expected.items() if tid != deferred_tid},
        )
        if correction is not None and correction.id in {
            credit.id for credit in credits
        }:
            raise ToolExecutionRejected(
                "ERR_CORRECTION_CONFLICT: ID корректировки совпадает с проводкой соглашения"
            )
    require_unrestricted(
        tools.db, offer.customer_id, [c.original_transaction_id for c in credits]
    )
    if profile is None:
        _post_credits(
            tools,
            [
                c
                for c in credits
                if not partial or c.original_transaction_id != deferred_tid
            ],
        )
    if correction is not None:
        _post_credits(tools, [correction])
    if partial:
        activate(tools.db, offer)
    current = advance(tools.db, offer.customer_id, offer.period)
    result = dict(
        id=execution_id,
        customer_id=offer.customer_id,
        case_id=case_id,
        status="succeeded",
        period=offer.period,
        state_revision=current,
        total_amount=money(total),
        ledger=[x.model_dump() for x in credits],
        offer_id=offer_id,
        offer_version=offer_version,
    )
    settlement = None
    if profile is not None or partial:
        settlement = RewardResolutionSettlement(
            final_status=profile or "succeeded",
            retry_required=partial,
            ledger=[
                x.model_dump()
                for x in credits
                if not partial or x.original_transaction_id == deferred_tid
            ],
            total_amount=money(total),
            funding_charge=funding_charge,
        )
        result.update(operation_id=execution_id, status="execution_unknown")
        result.pop("total_amount")
        result.pop("ledger")
        if partial:
            posted = [x for x in credits if x.original_transaction_id != deferred_tid]
            result.update(
                ledger=[x.model_dump() for x in posted],
                total_amount=money(sum(cents(x.amount) for x in posted)),
                retry_required=True,
                failed_transaction_ids=[deferred_tid],
            )
        if funding_charge is not None:
            result["funding_hold"] = funding_charge
    elif funding_charge is not None:
        result["funding_charge"] = funding_charge
    reviews = public_reviews(tools.db, offer.customer_id, case_id)
    if reviews:
        result["duplicate_reviews"] = reviews
    tools.db.reward_resolution_executions[result["id"]] = RewardResolutionExecution(
        id=result["id"],
        customer_id=offer.customer_id,
        case_id=case_id,
        period=offer.period,
        transaction_ids=offer.transaction_ids,
        idempotency_key=idempotency_key,
        packet={**packet, "confirmation_id": confirmation_id},
        result=result,
        pending_settlement=settlement,
    )
    return result
