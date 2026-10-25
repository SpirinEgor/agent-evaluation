"""Formal review captures a claim; it is different from a customer statement."""

import pytest

from tau2.domains.banking_ru.client_requests import StructuredIdentityBankingTools
from tau2.domains.banking_ru.data_model import BankingDB
from tau2.domains.banking_ru.file_corpus import FileCorpus


@pytest.fixture
def tools(tmp_path):
    from tests.utils.banking_parent_fixture import copy_historical_data

    d = copy_historical_data("reward_workflow_2026_10_21", tmp_path / "workflow")
    db = BankingDB.model_validate_json((d / "db.json").read_text())
    db.session.verified_customers.append("gromov_a_1187")
    return StructuredIdentityBankingTools(db, FileCorpus(d))


def test_formal_submission_captures_amount_and_is_idempotent(tools):
    c = tools.db.cases["case_610903"]
    assert c.review_status == "draft"
    tools.submit_cashback_review(c.id, c.version)
    assert c.review_status == "registered" and c.review_amount == 17
    assert c.review_items == c.reward_items and c.version == 2
    assert tools.submit_cashback_review(c.id, 1).version == 2
    assert tools.assert_cashback_review(c.id)


def test_stale_version_is_recoverable_without_registering(tools):
    c = tools.db.cases["case_610903"]
    result = tools.submit_cashback_review(c.id, 0)
    assert result.root["status"] == "version_conflict"
    assert c.review_status == "draft"


def test_registered_composition_cannot_be_amended(tools):
    c = tools.db.cases["case_610903"]
    tools.submit_cashback_review(c.id, 1)
    with pytest.raises(ValueError, match="REVIEW_REGISTERED"):
        tools.amend_cashback_investigation(
            c.id, [dict(transaction_id="txn_804219", amount=10.01)]
        )


def test_submit_requires_identification(tools):
    tools.db.session.verified_customers.clear()
    with pytest.raises(ValueError):
        tools.submit_cashback_review("case_610903", 1)
    assert tools.db.cases["case_610903"].review_status == "draft"


def test_completed_manual_payment_can_be_safely_replayed_after_registration(tools):
    c = "gromov_a_1187"
    case = "case_610903"
    b = "txn_804227"
    tools.give_discoverable_user_tool("submit_reward_statement")
    submitted = tools.perform_customer_action(
        c, "submit_reward_statement", dict(case_id=case, transaction_ids=[b])
    )
    reg = tools.register_reward_statement(c, submitted["id"], case).root
    tools.get_reward_statement_status(c, reg["id"])
    tools.refresh_cashback_settlement(c, "2026-07")
    args = dict(
        permission_id="cap_620184",
        transaction_id="txn_804219",
        amount=0.01,
        idempotency_key="safe-manual",
        expected_revision=2,
    )
    tools.execute_cashback_adjustment(**args)
    tools.submit_cashback_review(case, 1)
    before = tools.db.accounts["acc_1187"].balance
    replay = tools.execute_cashback_adjustment(**args)
    assert replay.root["status"] == "succeeded"
    assert tools.db.accounts["acc_1187"].balance == before
    with pytest.raises(ValueError, match="REVIEW_REGISTERED"):
        tools.execute_cashback_adjustment(
            **{**args, "idempotency_key": "another-manual"}
        )
