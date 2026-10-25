---
id: "reward_refund_adjustments"
section: "Акции и кешбэк"
title: "Возвраты и база вознаграждения"
keywords: ["кешбэк", "вознаграждения", "multi-program-v1", "возвраты", "и", "база", "вознаграждения"]
revision_id: "multi-program-v1"
effective_from: "2026-06-01"
effective_to: null
document_type: "bank-knowledge"
trust: "bank-policy"
---

# Возвраты и база вознаграждения

Редакция multi-program-v1: проведённый возврат с original_transaction_id уменьшает базу исходной покупки до нуля, включая проведённые после периода возвраты не позднее settlement_as_of. Связь требует совпадения владельца, счёта и карты. Компенсация кешбэка с compensates_cashback=true не является возвратом товара, не уменьшает базу покупки и погашает соответствующее обязательство. Незавершённый возврат не уменьшает базу. См. [сверку выплат](reward_payment_reconciliation.md).
