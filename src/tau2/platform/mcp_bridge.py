"""Мост между инструментами tau2 и типами MCP.

Схема инструмента берётся из `Tool.params` без пересборки: участник обязан
видеть байт в байт то же описание, что и встроенный агент.
"""

import mcp.types as t

from tau2.data_model.message import ToolMessage
from tau2.environment.tool import Tool

#: Инструмент завершения сессии. Имя совпадает с LLMSoloAgent.STOP_FUNCTION_NAME.
DONE_TOOL_NAME = "done"
#: Ответ на done. Совпадает с LLMSoloAgent.STOP_TOKEN.
STOP_TOKEN = "###STOP###"


def to_mcp_tool(tool: Tool) -> t.Tool:
    """Описание инструмента tau2 в виде MCP-инструмента."""
    return t.Tool(
        name=tool.name,
        description=tool.openai_schema["function"]["description"],
        inputSchema=tool.params.model_json_schema(),
    )


def done_tool() -> t.Tool:
    """Инструмент завершения: без аргументов, вызывается один раз в конце."""
    return t.Tool(
        name=DONE_TOOL_NAME,
        description=(
            "Завершить работу над обращением. Вызывается один раз, когда все "
            "операции выполнены и их результаты получены. Соблюдайте контракт "
            "общения домена; итоговый ответ требуется только если он предусмотрен."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    )


def text_result(text: str, *, is_error: bool = False) -> t.CallToolResult:
    """Текстовый результат вызова."""
    return t.CallToolResult(
        content=[t.TextContent(type="text", text=text)], is_error=is_error
    )


def to_call_result(message: ToolMessage) -> t.CallToolResult:
    """Ответ среды tau2 как результат MCP-вызова. Ошибка среды — is_error."""
    return text_result(message.content or "", is_error=message.error)
