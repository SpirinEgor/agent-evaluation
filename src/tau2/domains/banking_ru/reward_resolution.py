"""Bank offer execution: raw terms, exact client consent and atomic ledger effects."""

from copy import deepcopy
from decimal import ROUND_HALF_EVEN, Decimal

from tau2.domains.banking_ru.cashback_state import (
    advance,
    pending_funding,
    revision,
    state_guard,
)
from tau2.domains.banking_ru.reward_resolution_models import RewardResolutionExecution
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
    if any(
        cents(p.bank_cost) + cents(p.partner_cost) != cents(p.amount)
        or not 0 <= component_deduction_cents(p) < cents(p.amount)
        or not 0
        < cents(p.allocation_first_amount)
        < cents(p.amount) - component_deduction_cents(p)
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
    if len(rest) != 1 or total > cents(offer.amount):
        raise ValueError("Неверная область или превышен предел предложения")
    return total, {first: first_amount, rest.pop(): total - first_amount}


def list_offers(tools, customer_id, case_id):
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
        row.update(
            state_revision=revision(tools.db, customer_id, offer.period),
            funding_status="pending" if pending_funding(tools.db, offer) else "final",
            component_count=len(offer.components),
            components_complete=len(offer.components) <= 128,
        )
        if not row["components_complete"]:
            row["components"] = []
        results.append(row)
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


def confirm(tools, customer_id, request):
    offer, case = owned_offer(tools, request.offer_id, request.case_id)
    if customer_id != offer.customer_id:
        raise ToolExecutionRejected("ERR_FOREIGN_OWNER: Чужое предложение")
    packet = request.model_dump(exclude={"type"})
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
    declined = offer.rights == "full_settlement" and (
        preferences.preserve_unpaid_claims or preferences.preserve_other_case_items
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
        if old == packet:
            return {**base, "confirmation_id": cid}
    cid = f"resolution-confirmation-{len(tools._resolution_consents) + 1}"
    tools._resolution_consents[cid] = packet
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
):
    """Execute one chosen offer; never choose or advertise the optimal offer."""
    offer, case = owned_offer(tools, offer_id, case_id)

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
    if offer.components or component_ids:
        packet["component_ids"] = sorted(component_ids or [])
    # A replay is matched before revisions/consumption, but still after ownership.
    for op in tools.db.reward_resolution_executions.values():
        if op.customer_id != offer.customer_id:
            continue
        if op.idempotency_key == idempotency_key:
            if op.packet != {**packet, "confirmation_id": confirmation_id}:
                reject("Ключ использован с другим пакетом")
            return deepcopy(op.result)
        if (
            set(op.transaction_ids) & set(offer.transaction_ids)
            and op.period == offer.period
        ):
            reject("Уже выбран способ урегулирования этих покупок")
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
    for p in tools.db.cashback_adjustment_permissions.values():
        if (
            p["customer_id"] == offer.customer_id
            and p.get("period") == offer.period
            and p["transaction_id"] in offer.transaction_ids
            and cents(p.get("used_amount", 0))
        ):
            reject("Ручная корректировка уже выбрана")
    consent = tools._resolution_consents.get(confirmation_id)
    if not consent or any(consent[k] != v for k, v in packet.items()):
        reject("Нет согласия на точный пакет")
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
    expected_claims.update({tid: gaps[tid] - amt for tid, amt in expected.items()})
    if {
        x["transaction_id"]: cents(x["amount"]) for x in consent["resulting_claims"]
    } != expected_claims:
        reject("Согласованный остаток требований не соответствует проводкам")
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
    for credit in credits:
        tools.db.transactions[credit.id] = credit
        account = tools._get_account(credit.account_id)
        account.balance = money(cents(account.balance) + cents(credit.amount))
        tools.db.transactions[credit.original_transaction_id].cashback_granted = True
    current = advance(tools.db, offer.customer_id, offer.period)
    result = dict(
        id="resolution:" + case_id,
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
    tools.db.reward_resolution_executions[result["id"]] = RewardResolutionExecution(
        id=result["id"],
        customer_id=offer.customer_id,
        case_id=case_id,
        period=offer.period,
        transaction_ids=offer.transaction_ids,
        idempotency_key=idempotency_key,
        packet={**packet, "confirmation_id": confirmation_id},
        result=result,
    )
    return result
