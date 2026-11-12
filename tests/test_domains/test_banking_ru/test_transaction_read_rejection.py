"""Trusted missing-record rejection is narrow and does not redact real access."""

import pytest

from tau2.data_model.message import ToolCall
from tau2.domains.banking_ru.environment import get_environment
from tau2.domains.banking_ru.tools import BankingTools
from tau2.environment.toolkit import ToolType, is_tool


@pytest.mark.parametrize(
    "transaction_id,rejected", [("absent-operation", True), ("txn_774411", False)]
)
def test_read_metadata_and_no_mutation(transaction_id, rejected):
    env = get_environment()
    before = env.tools.db.model_dump(mode="json")
    result = env.get_response(
        ToolCall(
            id="read-test",
            name="get_transaction_details",
            arguments={"transaction_id": transaction_id},
            requestor="assistant",
        )
    )
    assert result.execution_rejected is rejected
    assert result.error is rejected
    assert env.tools.db.model_dump(mode="json") == before


def test_text_only_not_found_is_not_a_trusted_rejection(monkeypatch):
    @is_tool(ToolType.READ)
    def fail(self, transaction_id: str) -> str:
        """Simulate an unclassified failure."""
        raise ValueError("ERR_NOT_FOUND: simulated unexpected failure")

    monkeypatch.setattr(BankingTools, "get_transaction_details", fail)
    env = get_environment()
    result = env.get_response(
        ToolCall(
            id="read-test",
            name="get_transaction_details",
            arguments={"transaction_id": "absent-operation"},
            requestor="assistant",
        )
    )
    assert result.error
    assert "simulated unexpected failure" in result.content
    assert not result.execution_rejected
