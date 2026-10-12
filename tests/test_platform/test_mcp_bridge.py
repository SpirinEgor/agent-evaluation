import pytest

pytest.importorskip("mcp")

from tau2.data_model.message import ToolMessage
from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.mcp_bridge import (
    DONE_TOOL_NAME,
    done_tool,
    to_call_result,
    to_mcp_tool,
)


def test_schema_is_taken_verbatim_from_the_tau2_tool():
    """Схема MCP обязана быть той же, что видит встроенный агент: одна
    генерация из докстринга, не две."""
    env = get_environment(solo_mode=True)
    tool = next(t for t in env.get_tools() if t.name == "set_limit")
    mcp_tool = to_mcp_tool(tool)
    assert mcp_tool.name == "set_limit"
    assert mcp_tool.input_schema == tool.params.model_json_schema()
    assert mcp_tool.description == tool.openai_schema["function"]["description"]
    assert mcp_tool.input_schema["properties"]["limit_type"]["enum"] == [
        "daily_cash_withdrawal",
        "internet_operations",
        "sbp",
    ]


def test_done_tool_takes_no_arguments():
    tool = done_tool()
    assert tool.name == DONE_TOOL_NAME == "done"
    assert tool.input_schema["properties"] == {}
    assert tool.input_schema.get("additionalProperties") is False


def test_tool_message_maps_to_call_result_including_error_flag():
    ok = to_call_result(ToolMessage(id="1", role="tool", content='{"a": 1}'))
    assert ok.is_error is False
    assert ok.content[0].text == '{"a": 1}'
    err = to_call_result(
        ToolMessage(id="2", role="tool", content="Error: ERR_NOT_FOUND: x", error=True)
    )
    assert err.is_error is True
    assert err.content[0].text.startswith("Error: ERR_NOT_FOUND")
