"""Technical probing does not change business reward; safety remains on raw trace."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from tau2.data_model.tasks import EvaluationCriteria


def test_business_scope_is_explicit():
    assert EvaluationCriteria(business_process_only=True).business_process_only is True


def test_rejected_calls_filtered_only_for_business_constraints():
    from tau2.evaluator.evaluator_action import _business_observed

    ok = SimpleNamespace(result=SimpleNamespace(error=False))
    bad = SimpleNamespace(result=SimpleNamespace(error=True))
    raw = [ok, bad]
    assert _business_observed(SimpleNamespace(business_process_only=True), raw) == [ok]
    assert _business_observed(SimpleNamespace(business_process_only=False), raw) == raw
    assert raw == [ok, bad]


def test_no_separate_error_cap_keeps_call_budget():
    from tau2.domains.banking_ru.environment import get_environment, get_tasks
    from tau2.platform.session import PlatformSession, SessionStatus

    task = next(t for t in get_tasks() if t.id == "Bank 2")
    s = PlatformSession(task, get_environment, max_calls=7, max_errors=0)
    for _ in range(6):
        assert s.call("nonexistent_api", {}).is_error
    assert s.errors == 6 and s.status is SessionStatus.OPEN
    assert not s.call("get_bank_date", {}).is_error
    assert s.status is SessionStatus.BUDGET_EXHAUSTED


def test_prompt_states_only_call_cap_when_errors_disabled():
    p = Path(__file__).resolve().parents[1] / "scripts/banking_ru/run_codex_harness.py"
    spec = importlib.util.spec_from_file_location("business_harness", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    prompt = m.build_prompt(policy="ROLE", ticket="TICKET", max_calls=100, max_errors=0)
    assert "100" in prompt and "без отдельного лимита" in prompt
    assert "0 ошибок" not in prompt
