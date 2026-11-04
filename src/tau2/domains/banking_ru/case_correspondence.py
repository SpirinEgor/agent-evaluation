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


def send_message(tools, case_id, recipient_id, text):
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
    row = {
        "case_id": case_id,
        "customer_id": case.customer_id,
        "recipient_id": recipient_id,
        "text": text,
    }
    tools.db.case_outbound_messages.append(row)
    response = with_processing_notes(
        tools.db,
        {"status": "sent", "recipient_id": recipient_id},
        case.customer_id,
        "reply",
        recipient_id,
    )
    # A customer can have multiple cases involving the same external desk.
    # Replies must never cross case boundaries, even for the same customer.
    if "processing_notes" in response:
        response["processing_notes"] = [
            note for note in response["processing_notes"] if note["case_id"] == case_id
        ]
    return response
