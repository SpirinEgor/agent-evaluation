"""Declarative final-state obligations supplement semantic DB comparison."""

from typing import Any

from tau2.data_model.tasks import DBStateCheck


def state_check_failures(
    db_snapshot: dict[str, Any], checks: list[DBStateCheck]
) -> list[str]:
    """Check fields in every row selected by an explicit collection and scope.

    Missing data fails closed. Empty scoped collections pass universally;
    existence and the complete expected outcome remain part of DB comparison.
    This reads final state only, never tool history or environment assertions.
    """
    failures = []
    missing = object()
    for check in checks:
        collection = db_snapshot.get(check.collection)
        if not isinstance(collection, dict):
            failures.append(check.check_id)
            continue
        for row in collection.values():
            if not isinstance(row, dict):
                failures.append(check.check_id)
                break
            if not all(
                row.get(key, missing) == value for key, value in check.where.items()
            ):
                continue
            if row.get(check.field, missing) != check.equals:
                failures.append(check.check_id)
                break
    return failures
