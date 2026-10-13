"""All registered banking tools must run a real success and failure contract."""

from importlib import import_module

import pytest

from tau2.domains.banking_ru.environment import get_environment

GROUPS = (
    "identity_message_tool_contracts",
    "read_tool_contracts",
    "file_tool_contracts",
    "finance_tool_contracts",
    "card_case_tool_contracts",
)


def collect_scenarios(attribute):
    scenarios = {}
    for group in GROUPS:
        module = import_module(f"{__package__}.{group}")
        for name, scenario in getattr(module, attribute).items():
            assert name not in scenarios, f"Duplicate contract for {name}"
            scenarios[name] = scenario
    return scenarios


SUCCESS = collect_scenarios("SUCCESS_SCENARIOS")

ERROR = collect_scenarios("ERROR_SCENARIOS")

NO_ERROR_INPUT = {"list_tariffs"}


def test_every_registered_tool_has_success_and_error_contracts():
    registered = {tool.name for tool in get_environment().get_tools()}
    assert set(SUCCESS) == registered, (
        f"Missing success contracts: {sorted(registered - set(SUCCESS))}; "
        f"unknown contracts: {sorted(set(SUCCESS) - registered)}"
    )
    assert set(ERROR) == registered - NO_ERROR_INPUT, (
        f"Missing error contracts: {sorted(registered - NO_ERROR_INPUT - set(ERROR))}"
    )


@pytest.mark.parametrize("tool_name", sorted(SUCCESS))
def test_tool_success_contract_actually_calls_tool(tool_name, monkeypatch):
    env = get_environment()
    results = []
    get_response = env.get_response

    def record(call):
        response = get_response(call)
        results.append((call.name, response.error))
        return response

    monkeypatch.setattr(env, "get_response", record)
    SUCCESS[tool_name](env)
    assert (tool_name, False) in results, f"{tool_name}: no successful real call"


@pytest.mark.parametrize("tool_name", sorted(ERROR))
def test_tool_error_contract_actually_calls_tool(tool_name, monkeypatch):
    env = get_environment()
    results = []
    get_response = env.get_response

    def record(call):
        response = get_response(call)
        results.append((call.name, response.error))
        return response

    monkeypatch.setattr(env, "get_response", record)
    ERROR[tool_name](env)
    assert (tool_name, True) in results, f"{tool_name}: no failed real call"
