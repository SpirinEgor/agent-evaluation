---
id: "reward_resolution_amounts"
section: "Акции и кешбэк"
title: "Суммы предложений урегулирования"
keywords: ["кешбэк", "урегулирование", "предложения", "resolution"]
revision_id: "resolution-v1"
effective_from: "2026-06-01"
effective_to: null
document_type: "bank-knowledge"
trust: "bank-policy"
---

# Суммы предложений урегулирования

Для предложения cumulative договорная amount — совокупная сумма погашения по transaction_ids. Из неё вычитаются связанные проведённые cashback и compensates_cashback=true, включая поздние начисления, по обеим покупкам. Покупки, товарные возвраты и выплаты других покупок не входят в зачёт. Для incremental amount — только новая дополнительная выплата. Незавершённую обработку периода нужно завершить до исполнения; сравнение вариантов проводится на актуальных фактах. При цели максимизации сравнивают новые гарантированные рубли всех разрешённых вариантов, включая ручное полномочие. Суммы в клиентском сообщении не заменяют банковские записи. Распределение нового платежа: на allocation_first_transaction_id направить allocation_first_amount; остаток — второй покупке scope. Не превышать непогашенное обязательство за вычетом уже заявленного. [Права](reward_resolution_rights.md), [исполнение](reward_resolution_execution.md), [сверка](reward_payment_reconciliation.md).
