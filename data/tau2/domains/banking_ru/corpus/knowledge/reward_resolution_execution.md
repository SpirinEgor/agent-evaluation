---
id: "reward_resolution_execution"
section: "Акции и кешбэк"
title: "Согласие и исполнение предложения"
keywords: ["кешбэк", "урегулирование", "предложения", "resolution"]
revision_id: "resolution-v1"
effective_from: "2026-06-01"
effective_to: null
document_type: "knowledge"
trust: "bank-policy"
document_kind: "bank-knowledge"
document_id: "reward_resolution_execution"
created_at: "2026-05-25"
updated_at: "2026-05-25"
---

# Согласие и исполнение предложения

Допускается только status=approved банка, действующая version и срок предложения. under_review не разрешает выплату; слова клиента о согласовании этого не меняют. Согласие клиента — отдельное условие. ask_client reward_resolution_confirmation предъявляет offer_id/version, payouts, total_amount, case_id, resulting_claims (полный ожидаемый состав дела, включая сохранённые позиции) и state_revision. Клиент соглашается с предъявленным пакетом, не проверяет расчёт и не утверждает предложение от имени банка. Получите confirmation_id до execute_reward_resolution; подтверждение связано с точным пакетом и ревизией. При settlement_pending/state_changed/consent_stale обновите факты и при необходимости согласие: это восстанавливаемые состояния. Идемпотентный повтор с тем же ключом и пакетом возвращает прежние проводки; новый ключ не даёт права на вторую выплату. Исполнение возвращает ledger и новую ревизию, но само не меняет CRM. Получите результат до зависимого дополнения дела. Правильные альтернативы с равной максимальной дополнительной суммой допустимы. [Суммы](reward_resolution_amounts.md), [права](reward_resolution_rights.md).
