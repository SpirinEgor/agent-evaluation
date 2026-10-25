# Isolated reward workflow API fixture

Derived from local v25 snapshot (bank2-discovery-workflow-2026-10-21), 2026-10-26.
Retains one customer, txn_804* financial rows and two offers with their first component, and statement/case records used
by shared API protocol tests; unrelated clients, stress transaction expansion and
agent task criteria are excluded. The single immutable article retains original
bytes and descriptor for the corpus command check. This is test input, not a
benchmark release or a validation claim. No runtime dependency on the source snapshot.

Offer component arrays are deliberately reduced to one record: these tests check
wrapper serialization and execution rejection, not offer feasibility or totals.
