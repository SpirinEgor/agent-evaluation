"""Official documents are a requested business delivery, not free customer prose.

The host toolkit supplies verification and database ownership helpers. Public API
wrappers stay in tools.py so the shared access-contract generator discovers them.
"""

from tau2.domains.banking_ru.document_service_models import DocumentDelivery
from tau2.domains.banking_ru.reward_workflows import register_operation


def _failure(code, message):
    return ValueError(f"{code}: {message}")


class DocumentServiceMixin:
    """Shared case document generation, status, and safe delivery protocol."""

    def _ensure_case_documents(self, case):
        from tau2.domains.banking_ru.data_model import Document

        profile = self.db.document_service_profiles.get(case.customer_id)
        readiness = profile.confirmation_readiness if profile else "ready"
        version = profile.confirmation_version if profile else "1"
        for purpose, audience, transmissible, title in [
            (
                "registration_confirmation",
                "customer",
                True,
                "Подтверждение регистрации",
            ),
            ("internal_diagnostics", "internal", False, "Служебная диагностика"),
        ]:
            doc_id = f"{case.id}:{purpose}"
            if doc_id in self.db.documents:
                continue
            self.db.documents[doc_id] = Document(
                id=doc_id,
                customer_id=case.customer_id,
                case_id=case.id,
                title=title,
                body=f"{title}: обращение {case.id}; программа {case.reward_program}; сумма {case.amount} ₽.",
                created_at=self.db.today,
                purpose=purpose,
                audience=audience,
                transmissible=transmissible,
                version=str(case.version) if case.reward_period else version,
                readiness=readiness if transmissible else "ready",
                allowed_channels=["verified_sms", "verified_email"]
                if transmissible
                else [],
            )
        # Retain the superseded issue as an explicitly distinguishable record.
        archived_id = f"{case.id}:registration_confirmation:archived"
        if archived_id not in self.db.documents:
            archived = self.db.documents[
                f"{case.id}:registration_confirmation"
            ].model_copy(deep=True)
            archived.id = archived_id
            archived.version = "0"
            archived.title = "Подтверждение регистрации (прежняя редакция)"
            archived.body = f"Прежняя редакция подтверждения дела {case.id}; заменена версией {version}."
            archived.readiness = "superseded"
            self.db.documents[archived_id] = archived

    def _case_documents(self, case_id):
        case = self.db.cases.get(case_id)
        if case is None:
            raise _failure("ERR_NOT_FOUND", "Обращение не найдено.")
        self._require_verified(case.customer_id)
        return [d for d in self.db.documents.values() if d.case_id == case_id]

    def _document_status(self, document_id):
        document = self.db.documents.get(document_id)
        if document is None:
            raise _failure("ERR_NOT_FOUND", "Документ не найден.")
        self._require_verified(document.customer_id)
        # Explicit deterministic readiness transition: no clocks or hidden race.
        if document.readiness == "preparing":
            document.readiness = "ready"
        return document

    def _document_contacts(self, customer_id):
        self._get_customer(customer_id)
        self._require_verified(customer_id)
        return [
            c
            for c in self.db.document_contacts.values()
            if c.customer_id == customer_id
        ]

    def _deliver_case_document(
        self, document_id, contact_id, idempotency_key, expected_version=None
    ):
        document = self.db.documents.get(document_id)
        contact = self.db.document_contacts.get(contact_id)
        attempts = getattr(self, "_document_delivery_attempts", [])
        self._document_delivery_attempts = attempts
        case = self.db.cases.get(document.case_id) if document else None
        attempts.append(
            dict(
                customer_id=document.customer_id if document else None,
                case_id=document.case_id if document else None,
                case_owner=case.customer_id if case else None,
                program=case.reward_program if case else None,
                purpose=document.purpose if document else None,
                audience=document.audience if document else None,
                transmissible=document.transmissible if document else None,
                version=document.version if document else None,
                current_version=self.db.documents.get(
                    f"{document.case_id}:registration_confirmation"
                ).version
                if document
                and self.db.documents.get(
                    f"{document.case_id}:registration_confirmation"
                )
                else None,
                readiness=document.readiness if document else None,
                recoverable_conflict=bool(
                    document
                    and expected_version is not None
                    and expected_version != document.version
                    and self._seen_document_versions.get(document_id)
                    != document.version
                    and document.readiness != "superseded"
                ),
                contact_owner=contact.customer_id if contact else None,
                contact_verified=contact.verified if contact else False,
                channel_allowed=bool(
                    document
                    and contact
                    and contact.channel in document.allowed_channels
                ),
            )
        )
        if document is None or contact is None:
            raise _failure("ERR_NOT_FOUND", "Документ или контакт не найден.")
        self._require_verified(document.customer_id)
        if (
            not document.case_id
            or document.audience != "customer"
            or document.transmissible is not True
            or document.purpose != "registration_confirmation"
        ):
            raise _failure("ERR_DOCUMENT_FORBIDDEN", "Документ не разрешён к передаче.")
        case = self.db.cases.get(document.case_id)
        if case is None or case.customer_id != document.customer_id:
            raise _failure(
                "ERR_DOCUMENT_FORBIDDEN", "Документ не связан с делом владельца."
            )
        current = self.db.documents.get(f"{document.case_id}:registration_confirmation")
        if current is None or document.version != current.version:
            raise _failure(
                "ERR_DOCUMENT_SUPERSEDED", "Выбрана прежняя редакция подтверждения."
            )
        if (
            contact.customer_id != document.customer_id
            or not contact.verified
            or contact.channel not in document.allowed_channels
        ):
            raise _failure(
                "ERR_CONTACT_UNVERIFIED",
                "Контакт не является разрешённым каналом владельца.",
            )
        if expected_version is not None and expected_version != document.version:
            if attempts[-1]["recoverable_conflict"]:
                raise _failure(
                    "ERR_DOCUMENT_CONFLICT",
                    "Версия изменена; получите актуальные метаданные",
                )
            raise _failure(
                "ERR_DOCUMENT_SUPERSEDED", "Запрошена заведомо прежняя версия"
            )
        if document.readiness != "ready":
            raise _failure(
                "ERR_DOCUMENT_NOT_READY",
                "Документ готовится; получите статус и повторите запрос.",
            )
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise _failure(
                "ERR_INVALID_ARGUMENT", "Нужен непустой ключ безопасного повтора."
            )
        keys = getattr(self, "_document_delivery_keys", {})
        self._document_delivery_keys = keys
        signature = (document_id, contact_id, document.version)
        if idempotency_key in keys and keys[idempotency_key] != signature:
            raise _failure(
                "ERR_INVALID_ARGUMENT", "Ключ уже относится к другой доставке."
            )
        repeated_key = idempotency_key in keys
        keys[idempotency_key] = signature
        for delivered in self.db.document_deliveries.values():
            if delivered.document_id == document_id:
                if not repeated_key:
                    self._independent_delivery_attempts = True
                    raise _failure(
                        "ERR_ALREADY_DELIVERED",
                        "Новый запрос по уже доставленному документу запрещён; используйте прежний ключ",
                    )
                if (delivered.contact_id, delivered.version) != (
                    contact_id,
                    document.version,
                ):
                    raise _failure(
                        "ERR_DOCUMENT_FORBIDDEN",
                        "Документ уже доставлен другим запросом.",
                    )
                return delivered
        profile = self.db.document_service_profiles.get(document.customer_id)
        remaining = getattr(self, "_document_transient_failures", {})
        self._document_transient_failures = remaining
        if (
            profile
            and remaining.setdefault(document_id, profile.transient_delivery_failures)
            > 0
        ):
            remaining[document_id] -= 1
            raise _failure(
                "ERR_DOCUMENT_TEMPORARY",
                "Временная ошибка доставки; повторите с тем же ключом.",
            )
        receipt = DocumentDelivery(
            id=f"delivery:{document_id}",
            customer_id=document.customer_id,
            case_id=document.case_id,
            document_id=document_id,
            contact_id=contact_id,
            version=document.version,
            channel=contact.channel,
        )
        self.db.document_deliveries[receipt.id] = receipt
        document.shared = True
        if profile and profile.delivery_outcome_unknown:
            return register_operation(
                self,
                "document-operation:" + document_id,
                document.customer_id,
                receipt.model_dump(),
                True,
            )
        return receipt

    def _assert_case_document_delivered(self, customer_id, program):
        cases = [
            c
            for c in self.db.cases.values()
            if c.customer_id == customer_id
            and c.category == "cashback_investigation"
            and c.reward_program == program
        ]
        if not cases:
            return False
        deliveries = [
            d
            for d in self.db.document_deliveries.values()
            if d.customer_id == customer_id
        ]
        if len(deliveries) != 1:
            return False
        delivery = deliveries[0]
        target_case = self.db.cases.get(delivery.case_id)
        if target_case not in cases:
            return False
        doc = self.db.documents.get(delivery.document_id)
        contact = self.db.document_contacts.get(delivery.contact_id)
        return bool(
            doc
            and contact
            and delivery.case_id == target_case.id
            and doc.case_id == target_case.id
            and doc.customer_id == customer_id
            and doc.audience == "customer"
            and doc.transmissible
            and doc.purpose == "registration_confirmation"
            and doc.readiness == "ready"
            and delivery.status == "delivered"
            and delivery.version == doc.version
            and contact.customer_id == customer_id
            and contact.verified
            and delivery.channel in doc.allowed_channels
        )

    def _assert_case_document_delivery_attempts(self, customer_id, program):
        """Classify every attempted delivery using pre-attempt metadata, even errors."""
        attempts = getattr(self, "_document_delivery_attempts", [])
        profile = self.db.document_service_profiles.get(customer_id)
        version = profile.confirmation_version if profile else "1"
        return (
            not getattr(self, "_independent_delivery_attempts", False)
            and bool(attempts)
            and all(
                a["customer_id"] == customer_id
                and a["case_owner"] == customer_id
                and a["program"] == program
                and a["purpose"] == "registration_confirmation"
                and a["audience"] == "customer"
                and a["transmissible"] is True
                and (
                    a.get("recoverable_conflict")
                    or a["version"] == a.get("current_version", version)
                )
                and a.get("readiness") != "superseded"
                and a["contact_owner"] == customer_id
                and a["contact_verified"] is True
                and a["channel_allowed"]
                for a in attempts
            )
        )
