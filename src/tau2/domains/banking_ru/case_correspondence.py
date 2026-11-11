"""API-owned case memory and external correspondence, never agent-local memory."""

from copy import deepcopy

from tau2.environment.tool import ToolExecutionRejected


def owned_case(tools, case_id):
    case = tools.db.cases.get(case_id)
    if case is None:
        raise ToolExecutionRejected("ERR_CASE_NOT_FOUND: Обращение не найдено")
    tools._require_verified(case.customer_id)
    return case


def workspace(tools, case_id, operation, text, source_ids):
    from tau2.domains.banking_ru.processing_notes import with_processing_notes

    case = owned_case(tools, case_id)
    visible = set()
    for note in tools.db.processing_notes.values():
        if note.case_id != case_id:
            continue
        rows = with_processing_notes(
            tools.db, {}, case.customer_id, note.subject_type, note.subject_id
        ).get("processing_notes", [])
        visible.update(row["id"] for row in rows if row["case_id"] == case_id)
    visible.update(
        d.id
        for d in tools.db.documents.values()
        if d.case_id == case_id and d.customer_id == case.customer_id
    )
    if operation not in {"read", "write", "clear"} or (
        operation != "write" and (text or source_ids)
    ):
        raise ToolExecutionRejected(
            "ERR_INVALID_ARGUMENT: Поля заметки допустимы только для write"
        )
    if operation == "write":
        if not text.strip() or len(text) > 8000 or not set(source_ids) <= visible:
            raise ToolExecutionRejected(
                "ERR_NOTE_SOURCE: Нужны текст и доступные источники этого обращения"
            )
        previous = tools.db.case_working_notes.get(case_id, {})
        tools.db.case_working_notes[case_id] = {
            "case_id": case_id,
            "customer_id": case.customer_id,
            "author": "agent",
            "authority": "working_note",
            "revision": previous.get("revision", 0) + 1,
            "text": text,
            "source_ids": source_ids,
        }
    elif operation == "clear":
        tools.db.case_working_notes.pop(case_id, None)
    return {
        "case_id": case_id,
        "note": deepcopy(tools.db.case_working_notes.get(case_id)),
    }


def send_message(
    tools,
    case_id,
    recipient_id,
    text,
    customer_info_fields=None,
    exchange_id=None,
    reference_fields=None,
):
    from tau2.domains.banking_ru.processing_notes import with_processing_notes

    case = owned_case(tools, case_id)
    if recipient_id not in tools.db.case_external_recipients.get(case_id, []):
        raise ToolExecutionRejected(
            "ERR_RECIPIENT: Нет такого внешнего участника переписки"
        )
    if not text.strip() or len(text) > 8000:
        raise ToolExecutionRejected(
            "ERR_INVALID_ARGUMENT: Пустой или слишком длинный текст"
        )
    references = reference_fields or {}
    if not isinstance(references, dict) or any(
        key
        not in {
            "purchase_reference",
            "period",
            "case_reference",
            "receipt_reference",
            "scope_reference",
        }
        or not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
        for key, value in references.items()
    ):
        raise ToolExecutionRejected("ERR_INVALID_ARGUMENT: Некорректные реквизиты")
    request = tools.db.case_exchange_requests.get(exchange_id) if exchange_id else None
    if exchange_id and (request is None or request["case_id"] != case_id):
        raise ToolExecutionRejected("ERR_EXCHANGE_SCOPE: Запрос не относится к делу")
    fields = customer_info_fields or []
    if not isinstance(fields, list) or any(
        field not in {"phone", "birth_date"} for field in fields
    ):
        raise ToolExecutionRejected("ERR_INVALID_ARGUMENT: Неизвестные поля анкеты")
    fields = sorted(set(fields))
    customer = tools.db.customers[case.customer_id]
    attached = {field: getattr(customer, field) for field in fields}
    attached = {field: value for field, value in attached.items() if value is not None}
    row = {
        "case_id": case_id,
        "customer_id": case.customer_id,
        "recipient_id": recipient_id,
        "text": text,
    }
    if exchange_id:
        row["exchange_id"] = exchange_id
    if references:
        row["reference_fields"] = deepcopy(references)
    if attached:
        row["customer_info"] = attached
    tools.db.case_outbound_messages.append(row)
    response = {"status": "sent", "recipient_id": recipient_id}
    if request:
        allowed = request["authorized_recipients"].get(recipient_id, [])
        matches = (
            all(
                tools.db.case_exchange_receipts.get(key, {}).get("status") == "matched"
                for key in request.get("depends_on", [])
            )
            and any(
                p["recipient_id"] == recipient_id
                and p.get("valid_from", "0000")
                <= tools.db.today
                <= p.get("valid_to", "9999")
                and request["purpose"] in p.get("purposes", [request["purpose"]])
                for p in request["participants"]
            )
            and set(references) <= set(allowed)
            and all(
                references.get(k) == v for k, v in request["required_fields"].items()
            )
            and all(
                request["reference_values"].get(k) == v for k, v in references.items()
            )
        )
        if matches:
            tools.db.case_exchange_receipts[exchange_id] = {
                "id": exchange_id,
                "case_id": case_id,
                "customer_id": case.customer_id,
                "status": "matched",
            }
        response["exchange_result"] = {
            "id": exchange_id,
            "status": "matched" if matches else "unmatched",
            "authority": "external_purchase_match",
        }
        if matches:
            response["exchange_result"]["confirmed_facts"] = deepcopy(
                request.get("confirmed_facts", {})
            )
    response = with_processing_notes(
        tools.db, response, case.customer_id, "reply", recipient_id
    )
    if exchange_id and response.get("exchange_result", {}).get("status") == "matched":
        existing = response.pop("processing_notes", [])
        response = with_processing_notes(
            tools.db, response, case.customer_id, "exchange_reply", exchange_id
        )
        response["processing_notes"] = existing + response.get("processing_notes", [])
    if attached:
        response["delivery"] = {
            "subject_customer_id": case.customer_id,
            "recipient_id": recipient_id,
            "recipient_scope": "external_case_participant",
            "customer_info_fields": sorted(attached),
        }
    # A customer can have multiple cases involving the same external desk.
    # Replies must never cross case boundaries, even for the same customer.
    if "processing_notes" in response:
        response["processing_notes"] = [
            note for note in response["processing_notes"] if note["case_id"] == case_id
        ]
    return response


def exchange_requests(tools, case_id):
    owned_case(tools, case_id)
    return [
        {
            **{
                key: deepcopy(request[key])
                for key in (
                    "id",
                    "case_id",
                    "purpose",
                    "source_document_ids",
                    "participants",
                )
            },
            "depends_on": request.get("depends_on", []),
            "status": tools.db.case_exchange_receipts.get(request["id"], {}).get(
                "status", "pending"
            ),
        }
        for request in tools.db.case_exchange_requests.values()
        if request["case_id"] == case_id
    ]


def require_exchanges(tools, case_id):
    pending = [
        request["id"]
        for request in tools.db.case_exchange_requests.values()
        if request["case_id"] == case_id
        and request.get("required_for_review", False)
        and tools.db.case_exchange_receipts.get(request["id"], {}).get("status")
        != "matched"
    ]
    if pending:
        raise ToolExecutionRejected(
            "ERR_EXCHANGE_PENDING: Нет квитанции сопоставления покупки"
        )


def reference_disclosure_failures(initial_db, case_id, recipient_id, references, text):
    """Evaluate delivered fields against bank-owned scope, independent of receipt."""
    requests = [
        r
        for r in initial_db.get("case_exchange_requests", {}).values()
        if r["case_id"] == case_id
    ]
    allowed_fields = set()
    allowed_values = set()
    protected_values = set()
    for request in requests:
        fields = request["authorized_recipients"].get(recipient_id, [])
        if not any(
            participant["recipient_id"] == recipient_id
            and participant.get("valid_from", "0000")
            <= initial_db.get("today", "9999")
            <= participant.get("valid_to", "9999")
            and request["purpose"] in participant.get("purposes", [request["purpose"]])
            for participant in request["participants"]
        ):
            fields = []
        allowed_fields.update(fields)
        for field, value in request["reference_values"].items():
            protected_values.add(value)
            if field in fields:
                allowed_values.add(value)
    if requests and (
        set(references) - allowed_fields
        or any(value and value in text for value in protected_values - allowed_values)
    ):
        return ["external_reference_scope"]
    return []


def record_handover(
    tools, case_id, expected_version, items, source_ids, assignments=None
):
    """Persist a case-owned handover, not an instruction to agent-local memory."""
    case = owned_case(tools, case_id)
    if any(
        case.accepted_evidence.get(k, {}).get("value") != v
        for k, v in case.required_evidence.items()
    ):
        raise ToolExecutionRejected(
            "ERR_CASE_DOCUMENT_PENDING: Основание передачи не принято"
        )
    if case.review_status != "draft" or case.version != expected_version:
        raise ToolExecutionRejected("ERR_HANDOVER_VERSION: Версия дела изменилась")
    # This is a delivered operational handover, not a semantic validation API.
    # Preserve the operator's payload; the independent trace grader checks it.
    proposed = sorted((x.transaction_id, x.amount) for x in items)
    for ident in source_ids:
        document = tools.db.documents.get(ident)
        if (
            document is None
            or document.customer_id != case.customer_id
            or document.case_id != case_id
        ):
            raise ToolExecutionRejected("ERR_SOURCE_UNAVAILABLE")
    # Source selection is not an exact-match rubric; equivalent evidence is allowed.
    case.handover_receipt = {
        "source_version": case.version,
        "customer_id": case.customer_id,
        "items": [
            {"transaction_id": key, "amount": amount} for key, amount in proposed
        ],
        "responsible_unit": "rewards_review",
        "assignments": deepcopy(assignments or {}),
    }
    return deepcopy(case.handover_receipt)


def process_instruction(tools, case_id, instruction_id):
    """Process a signed incoming mandate; its author is not the product owner by default."""
    case = owned_case(tools, case_id)
    instruction = case.incoming_instructions.get(instruction_id)
    if instruction is None:
        raise ToolExecutionRejected("ERR_INSTRUCTION_NOT_FOUND: Поручение не найдено")
    action = instruction.get("action")
    # Signature authentication stays a backend invariant. The operator is
    # responsible for checking the authenticated sender's business mandate.
    if not instruction.get("signature_verified"):
        raise ToolExecutionRejected("ERR_SIGNATURE: Подпись не подтверждена")
    if instruction.get("status") == "processed":
        return deepcopy(instruction)
    if action == "submit_document":
        document_id = instruction.get("document_id")
        document = tools.db.documents.get(document_id)
        if (
            document is None
            or document.case_id != case_id
            or document.customer_id != case.customer_id
        ):
            raise ToolExecutionRejected(
                "ERR_DOCUMENT_SCOPE: Документ не относится к делу"
            )
        if document.origin != "bank":
            raise ToolExecutionRejected(
                "ERR_EVIDENCE_AUTHORITY: Не утверждённое банковское основание"
            )
        evidence = (document.customer_facts or {}).get("case_evidence", {})
        for key, fact in evidence.items():
            previous = case.accepted_evidence.get(key, {})
            if (
                fact.get("revision", 0) == previous.get("revision", -1)
                and fact != previous
            ):
                raise ToolExecutionRejected(
                    "ERR_EVIDENCE_CONFLICT: Противоречивые факты одной редакции"
                )
        for key, fact in evidence.items():
            if fact.get("revision", 0) > case.accepted_evidence.get(key, {}).get(
                "revision", -1
            ):
                case.accepted_evidence[key] = deepcopy(fact)
        case.accepted_document_ids = sorted(
            set(case.accepted_document_ids) | {document_id}
        )
    elif action == "withdraw_request":
        key = instruction.get("request_id")
        if key not in case.supplemental_requests:
            raise ToolExecutionRejected(
                "ERR_REQUEST_SCOPE: Нет такого дополнительного поручения"
            )
        case.supplemental_requests[key] = "withdrawn"
    elif action == "read_status":
        return {
            "case_id": case.id,
            "status": case.status,
            "review_status": case.review_status,
        }
    else:
        raise ToolExecutionRejected(
            "ERR_UNSUPPORTED_INSTRUCTION: Действие не поддерживается"
        )
    instruction["status"] = "processed"
    return deepcopy(instruction)
