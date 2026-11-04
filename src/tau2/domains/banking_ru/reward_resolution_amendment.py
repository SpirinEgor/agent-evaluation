"""Reconcile and amend the unexecuted portion of an accepted agreement."""

from copy import deepcopy

from tau2.domains.banking_ru.cashback_state import advance, state_guard
from tau2.domains.banking_ru.reward_workflows import (
    cents,
    money,
    reward_obligations,
    standard_gaps,
)
from tau2.environment.tool import ToolExecutionRejected


def public_bank_credit(tools, offer):
    """Expose the same conditional credit used by the first execution stage."""
    from tau2.domains.banking_ru.reward_resolution_models import AdditionalBankCredit

    profile = tools.db.reward_resolution_correction_profiles.get(offer.id)
    if (
        profile is None
        or tools.db.reward_resolution_partial_profiles.get(offer.id) is None
        or tools.db.reward_resolution_execution_profiles.get(offer.id) is not None
    ):
        return None
    tx = tools._get_transaction(profile.transaction_id)
    if tx.customer_id != offer.customer_id or tx.id not in offer.transaction_ids:
        raise ToolExecutionRejected(
            "ERR_CORRECTION_SCOPE: Неверная область корректировки"
        )
    posting = tools.db.transactions.get(profile.ledger_id)
    if posting is not None and (
        posting.customer_id != offer.customer_id
        or posting.original_transaction_id != tx.id
        or cents(posting.amount) != cents(profile.amount)
        or posting.kind != "cashback"
        or posting.status != "posted"
    ):
        raise ToolExecutionRejected(
            "ERR_CORRECTION_CONFLICT: Проводка не соответствует условиям корректировки"
        )
    result = {
        "transaction_id": tx.id,
        "amount": profile.amount,
        "currency": tools._get_account(tx.account_id).currency,
        "status": "posted" if posting is not None else "approved",
        "posting_condition": "first_accepted_execution",
        "guaranteed_if_executed": True,
        "included_in_agreement_amount": False,
    }
    if posting is not None:
        result["posting_id"] = posting.id
    return AdditionalBankCredit(**result)


def correction_credit(tools, offer, immediate_amounts):
    """Validate a scenario event before any part of the agreement is posted."""
    from tau2.domains.banking_ru.data_model import Transaction

    profile = tools.db.reward_resolution_correction_profiles.get(offer.id)
    if profile is None:
        return None
    tx = tools._get_transaction(profile.transaction_id)
    if tx.customer_id != offer.customer_id or tx.id not in offer.transaction_ids:
        raise ToolExecutionRejected(
            "ERR_CORRECTION_SCOPE: Неверная область корректировки"
        )
    if profile.ledger_id in tools.db.transactions:
        raise ToolExecutionRejected("ERR_CORRECTION_CONFLICT: Проводка уже существует")
    if cents(profile.amount) + immediate_amounts.get(tx.id, 0) > standard_gaps(
        tools.db, offer.customer_id, offer.period
    ).get(tx.id, 0):
        raise ToolExecutionRejected(
            "ERR_CORRECTION_AMOUNT: Корректировка превышает обязательство"
        )
    return Transaction(
        id=profile.ledger_id,
        customer_id=tx.customer_id,
        account_id=tx.account_id,
        card_id=tx.card_id,
        original_transaction_id=tx.id,
        date=tools.db.today,
        posted_at=tools.db.today + "T12:00:00+03:00",
        reward_program=reward_obligations(tools.db, offer.customer_id, offer.period)[
            tx.id
        ]["program"],
        amount=profile.amount,
        merchant="Корректировка ранее рассчитанного вознаграждения",
        kind="cashback",
        status="posted",
        channel="online",
    )


def operation_for_amendment(tools, operation_id):
    operation = tools.db.reward_resolution_executions.get(operation_id)
    if operation is None:
        raise ToolExecutionRejected("ERR_NOT_FOUND: Операция урегулирования не найдена")
    tools._require_verified(operation.customer_id)
    offer = tools.db.reward_resolution_offers[operation.packet["offer_id"]]
    profile = tools.db.reward_resolution_correction_profiles.get(offer.id)
    if profile is None or not profile.allow_reallocation:
        raise ToolExecutionRejected(
            "ERR_AMENDMENT_NOT_ALLOWED: Перераспределение не разрешено"
        )
    return operation, offer


def confirm_amendment(tools, customer_id, request):
    from tau2.domains.banking_ru.reward_resolution import (
        below_minimum,
        minimum_snapshot,
        normalized,
    )

    operation, offer = operation_for_amendment(tools, request.resolution_operation_id)
    if (
        customer_id != operation.customer_id
        or request.case_id != operation.case_id
        or request.offer_id != offer.id
    ):
        raise ToolExecutionRejected(
            "ERR_FOREIGN_OWNER: Согласие относится к другой операции"
        )
    if (
        operation.pending_settlement is None
        or not operation.pending_settlement.retry_required
    ):
        raise ToolExecutionRejected(
            "ERR_AMENDMENT_NOT_PENDING: Нет неисполненной части"
        )
    packet = request.model_dump(exclude={"type"})
    for key in ("payouts", "resulting_claims"):
        packet[key] = normalized(packet[key])
        for item in packet[key]:
            if (
                tools._get_transaction(item["transaction_id"]).customer_id
                != customer_id
            ):
                raise ToolExecutionRejected("ERR_FOREIGN_OWNER: Чужая операция")
    packet["component_ids"] = sorted(packet["component_ids"])
    snapshot = minimum_snapshot(
        tools, customer_id, operation.case_id, operation.transaction_ids
    )
    totals = dict.fromkeys(operation.transaction_ids, 0)
    for row in operation.result.get("ledger", []):
        totals[row["original_transaction_id"]] += cents(row["amount"])
    for row in packet["payouts"]:
        totals[row["transaction_id"]] = totals.get(row["transaction_id"], 0) + cents(
            row["amount"]
        )
    if below_minimum(
        [
            {"transaction_id": tid, "amount": money(amount)}
            for tid, amount in totals.items()
        ],
        snapshot,
    ):
        return dict(
            customer_id=customer_id,
            request_type=request.type,
            status="declined",
            data=packet,
            rights=offer.rights,
        )
    # Consent certifies presentation, not bank authorization or optimality.
    cid = next(
        (
            key
            for key, value in tools._resolution_consents.items()
            if value == packet
            and tools._resolution_consent_minimums.get(key, {}) == snapshot
        ),
        None,
    )
    if cid is None:
        cid = f"resolution-confirmation-{len(tools._resolution_consents) + 1}"
        tools._resolution_consents[cid] = deepcopy(packet)
    tools._resolution_consent_minimums[cid] = snapshot
    return dict(
        customer_id=customer_id,
        request_type=request.type,
        status="confirmed",
        confirmation_id=cid,
        data=packet,
        rights=offer.rights,
        question=f"Подтвердите изменение неисполненной части соглашения {operation.id}: дополнительные выплаты {request.total_amount:g} ₽. Уже исполненные выплаты сохраняются; новый остаток требований показан в data.",
    )


def funding_after_amendment(original, original_total, revised_total):
    if original is None:
        return None
    reduced = deepcopy(original)
    release = cents(original_total) - cents(revised_total)
    for source in ("bank", "partner"):
        amount = min(cents(reduced[source]), release)
        reduced[source] = money(cents(reduced[source]) - amount)
        release -= amount
    if release:
        raise ToolExecutionRejected(
            "ERR_AMENDMENT_FUNDING: Недостаточный исходный резерв"
        )
    return reduced


def amend(
    tools,
    resolution_operation_id,
    payouts,
    total_amount,
    confirmation_id,
    expected_revision,
):
    from tau2.domains.banking_ru.data_model import Transaction
    from tau2.domains.banking_ru.reward_resolution import (
        minimum_snapshot,
        normalized,
        public_execution_result,
    )

    operation, offer = operation_for_amendment(tools, resolution_operation_id)
    rows = normalized(payouts)
    requested = dict(
        payouts=rows,
        total_amount=total_amount,
        confirmation_id=confirmation_id,
        expected_revision=expected_revision,
    )
    previous = operation.result.get("amendment")
    if previous and previous["request"] == requested:
        return public_execution_result(tools, operation)
    plan = operation.pending_settlement
    if (
        plan is None
        or not plan.retry_required
        or operation.result.get("status") != "execution_unknown"
    ):
        raise ToolExecutionRejected(
            "ERR_AMENDMENT_NOT_PENDING: Нет неисполненной части"
        )
    blocked = state_guard(
        tools.db, operation.customer_id, operation.period, expected_revision
    )
    if blocked:
        return blocked
    consent = tools._resolution_consents.get(confirmation_id)
    snapshot = minimum_snapshot(
        tools, operation.customer_id, operation.case_id, operation.transaction_ids
    )
    if (
        consent
        and tools._resolution_consent_minimums.get(confirmation_id, {}) != snapshot
    ):
        raise ToolExecutionRejected(
            "ERR_AMENDMENT_CONSENT: Изменились ограничения клиента"
        )
    if not consent or any(
        consent.get(key) != value
        for key, value in {
            "resolution_operation_id": operation.id,
            "offer_id": offer.id,
            "offer_version": operation.packet["offer_version"],
            "case_id": operation.case_id,
            "payouts": rows,
            "total_amount": total_amount,
            "state_revision": expected_revision,
            "component_ids": sorted(operation.packet.get("component_ids", [])),
            "eligibility_evidence": operation.packet.get("eligibility_evidence", {}),
            "fallback_operation_id": operation.packet.get("fallback_operation_id"),
        }.items()
    ):
        raise ToolExecutionRejected(
            "ERR_AMENDMENT_CONSENT: Нет актуального согласия на точное изменение"
        )
    if (
        not rows
        or any(cents(row["amount"]) <= 0 for row in rows)
        or sum(cents(row["amount"]) for row in rows) != cents(total_amount)
    ):
        raise ToolExecutionRejected("ERR_AMENDMENT_AMOUNT: Некорректная сумма")
    gaps = standard_gaps(tools.db, operation.customer_id, operation.period)
    posted = sum(cents(row["amount"]) for row in operation.result.get("ledger", []))
    revised_total = posted + cents(total_amount)
    if revised_total > cents(operation.packet["total_amount"]):
        raise ToolExecutionRejected(
            "ERR_AMENDMENT_CAP: Превышена первоначально одобренная сумма"
        )
    case = tools.db.cases[operation.case_id]
    if case.status != "open" or case.review_status == "registered":
        raise ToolExecutionRejected(
            "ERR_REVIEW_REGISTERED: Дело уже передано или закрыто"
        )
    expected_claims = {
        item.transaction_id: cents(item.amount) for item in case.reward_items
    }
    expected_claims.update({tid: gaps.get(tid, 0) for tid in operation.transaction_ids})
    credits = []
    for row in rows:
        tid, amount = row["transaction_id"], cents(row["amount"])
        tx = tools._get_transaction(tid)
        reserved = sum(
            cents(item.amount)
            for other in tools.db.cases.values()
            if other.id != case.id
            and other.customer_id == operation.customer_id
            and other.status == "open"
            for item in other.reward_items
            if item.transaction_id == tid
        )
        if (
            tid not in operation.transaction_ids
            or tx.customer_id != operation.customer_id
            or amount + reserved > gaps.get(tid, 0)
        ):
            raise ToolExecutionRejected(
                "ERR_AMENDMENT_SCOPE: Чужая позиция или превышение остатка"
            )
        if tools._get_account(tx.account_id).status != "active":
            raise ToolExecutionRejected("ERR_AMENDMENT_ACCOUNT: Счёт закрыт")
        expected_claims[tid] -= amount
        credit_id = f"reward-resolution:{operation.case_id}:{tid}"
        if credit_id in tools.db.transactions:
            credit_id += ":amended"
        if credit_id in tools.db.transactions:
            raise ToolExecutionRejected(
                "ERR_AMENDMENT_DUPLICATE: Проводка уже исполнена"
            )
        credits.append(
            Transaction(
                id=credit_id,
                customer_id=tx.customer_id,
                account_id=tx.account_id,
                card_id=tx.card_id,
                original_transaction_id=tid,
                date=tools.db.today,
                posted_at=tools.db.today + "T12:00:00+03:00",
                reward_program=reward_obligations(
                    tools.db, offer.customer_id, offer.period
                )[tx.id]["program"],
                amount=money(amount),
                merchant="Выплата по соглашению",
                kind="cashback",
                status="posted",
                channel="online",
            ).model_dump()
        )
    expected_claims = {
        tid: amount for tid, amount in expected_claims.items() if amount > 0
    }
    if {
        row["transaction_id"]: cents(row["amount"])
        for row in consent["resulting_claims"]
    } != expected_claims:
        raise ToolExecutionRejected(
            "ERR_AMENDMENT_CLAIMS: Остаток требований не соответствует проводкам"
        )
    original_funding = (
        previous["original_funding"] if previous else deepcopy(plan.funding_charge)
    )
    revised_funding = funding_after_amendment(
        original_funding, operation.packet["total_amount"], money(revised_total)
    )
    if revised_funding is not None:
        from tau2.domains.banking_ru.reward_funding import reserve_snapshot

        reserve = reserve_snapshot(tools.db, offer)
        held = operation.result.get("funding_hold", {})
        if any(
            cents(revised_funding[source])
            > cents(reserve["remaining_" + source]) + cents(held.get(source, 0))
            for source in ("bank", "partner")
        ):
            raise ToolExecutionRejected(
                "ERR_SHARED_FUNDING_EXHAUSTED: Недостаточно общего резерва"
            )
    plan.ledger, plan.total_amount, plan.funding_charge = (
        credits,
        money(revised_total),
        revised_funding,
    )
    operation.result.update(
        amendment={
            "request": requested,
            "consent": deepcopy(consent),
            "original_funding": original_funding,
        },
        pending_total_amount=total_amount,
        agreement_total_amount=money(revised_total),
        state_revision=advance(tools.db, operation.customer_id, operation.period),
    )
    if revised_funding is not None:
        operation.result["funding_hold"] = deepcopy(revised_funding)
    return public_execution_result(tools, operation)


def validated_amendment(db, operation):
    """Return a monetary certificate only for internally consistent final records.

    DB comparison may ignore a legal package's financing choice, but must not
    ignore a corrupted funding charge, pending intent, or ledger record.
    """
    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    try:
        amendment = operation.result.get("amendment")
        if (
            not amendment
            or operation.result.get("status") != "succeeded"
            or operation.pending_settlement is not None
            or operation.result.get("funding_hold")
        ):
            return None
        offer = db.reward_resolution_offers[operation.packet["offer_id"]]
        profile = db.reward_resolution_correction_profiles.get(offer.id)
        if profile is None or not profile.allow_reallocation:
            return None
        original_total, original_allocations = selected_amounts(
            offer, operation.packet["component_ids"]
        )
        if original_total != cents(
            operation.packet["total_amount"]
        ) or original_allocations != {
            x["transaction_id"]: cents(x["amount"]) for x in operation.packet["payouts"]
        }:
            return None
        selected = [
            part
            for part in offer.components
            if part.id in operation.packet["component_ids"]
        ]
        original_funding = {
            "reserve_id": offer.selection_budget.id,
            **{
                source: money(
                    sum(cents(getattr(part, source + "_cost")) for part in selected)
                )
                for source in ("bank", "partner")
            },
        }
        if amendment["original_funding"] != original_funding:
            return None
        ledger = operation.result["ledger"]
        if len({row["id"] for row in ledger}) != len(ledger):
            return None
        if any(
            db.transactions.get(row["id"]) is None
            or db.transactions[row["id"]].model_dump() != row
            for row in ledger
        ):
            return None
        correction = db.transactions[profile.ledger_id]
        if (
            correction.customer_id != operation.customer_id
            or correction.original_transaction_id != profile.transaction_id
            or cents(correction.amount) != cents(profile.amount)
            or correction.status != "posted"
            or correction.kind != "cashback"
        ):
            return None
        paid = sum(cents(row["amount"]) for row in ledger)
        if (
            paid != cents(operation.result["total_amount"])
            or paid != cents(operation.result.get("agreement_total_amount", -1))
            or operation.result.get("pending_total_amount") is not None
            or operation.result.get("retry_required")
            or operation.result.get("failed_transaction_ids")
            or paid > original_total
        ):
            return None
        expected_funding = funding_after_amendment(
            original_funding, money(original_total), money(paid)
        )
        if operation.result.get("funding_charge") != expected_funding:
            return None
        consent = amendment["consent"]
        request = amendment["request"]
        if (
            any(consent[key] != request[key] for key in ("payouts", "total_amount"))
            or consent["state_revision"] != request["expected_revision"]
            or consent["resolution_operation_id"] != operation.id
        ):
            return None
        original_posted = {
            tid: amount
            for tid, amount in original_allocations.items()
            if tid != db.reward_resolution_partial_profiles[offer.id]
        }
        expected = dict(original_posted)
        for row in request["payouts"]:
            tid = row["transaction_id"]
            if tid not in operation.transaction_ids or cents(row["amount"]) <= 0:
                return None
            expected[tid] = expected.get(tid, 0) + cents(row["amount"])
        actual = {}
        for row in ledger:
            tid = row["original_transaction_id"]
            root_id = f"reward-resolution:{operation.case_id}:{tid}"
            if row["id"] not in {root_id, root_id + ":amended"}:
                return None
            if any(
                ident in db.transactions
                and ident not in {item["id"] for item in ledger}
                for ident in (root_id, root_id + ":amended")
            ):
                return None
            if (
                row["customer_id"] != operation.customer_id
                or row["status"] != "posted"
                or row["kind"] != "cashback"
            ):
                return None
            actual[tid] = actual.get(tid, 0) + cents(row["amount"])
        if actual != expected or sum(
            cents(row["amount"]) for row in request["payouts"]
        ) != cents(request["total_amount"]):
            return None
        return {"charge": expected_funding, "payouts": actual, "ledger": ledger}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def normalize_amended_state(db, snapshot, funding):
    """Normalize validated alternative financing and split postings for DB equality."""
    from tau2.domains.banking_ru.reward_funding import reserve_key

    for operation in db.reward_resolution_executions.values():
        certificate = validated_amendment(db, operation)
        if certificate is None:
            continue
        offer = db.reward_resolution_offers[operation.packet["offer_id"]]
        key = reserve_key(offer)
        if key is None:
            continue
        usage = funding["|".join(key)]
        for source in ("bank", "partner"):
            usage[source] -= cents(certificate["charge"][source])
        usage.setdefault("amended_payouts", []).append(
            sorted(certificate["payouts"].items())
        )
        groups = {}
        for row in certificate["ledger"]:
            groups.setdefault(row["original_transaction_id"], []).append(row)
        for tid, rows in groups.items():
            signatures = [
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"id", "amount"}
                }
                for row in rows
            ]
            if any(value != signatures[0] for value in signatures):
                continue
            root_id = f"reward-resolution:{operation.case_id}:{tid}"
            for row in rows:
                snapshot["transactions"].pop(row["id"])
            snapshot["transactions"][root_id] = {
                **rows[0],
                "id": root_id,
                "amount": money(sum(cents(row["amount"]) for row in rows)),
            }
    for usage in funding.values():
        if "amended_payouts" in usage:
            usage["amended_payouts"].sort()
