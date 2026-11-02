"""Completion-only banking keeps business checks without legacy prose graders."""

from tau2.domains.banking_ru.environment import get_environment, get_tasks


def test_completion_only_toolkit_has_no_legacy_reply_assertions():
    tools = get_environment().tools
    obsolete = [
        name
        for name in dir(tools)
        if name.startswith("assert_") and name.endswith("_reply")
    ]
    assert not obsolete
    for name in (
        "assert_answer_contains",
        "assert_bank_1_case_reference",
        "assert_answer_mentions_case_id",
        "assert_payout_account_roles",
        "assert_closed_payout_communication",
        "_customer_limit_values",
    ):
        assert not hasattr(tools, name)
    assert not hasattr(tools, "assert_bank_12_referral")
    for task in get_tasks():
        for assertion in task.evaluation_criteria.env_assertions or []:
            assert callable(getattr(tools, assertion.func_name))
