"""Session-scoped, paged bank discovery; no domain state or grading decisions."""

import hashlib
import json
from copy import deepcopy

import mcp.types as t
from jsonschema import Draft202012Validator

from tau2.domains.banking_ru.mcp_systems import SYSTEMS
from tau2.platform.catalog_pages import CatalogPages, CursorError

SYSTEM_NAMES = {
    "crm": "CRM",
    "identity": "Идентификация",
    "support": "Поддержка",
    "knowledge": "Знания и политики",
    "cards": "Карты",
    "payments": "Платежи",
    "disputes": "Споры",
    "core_banking": "АБС",
    "servicing": "Обслуживание",
    "loyalty": "Лояльность",
    "digital_access": "Цифровой доступ",
    "documents": "Документы",
    "risk": "Риски",
}


class DirectoryError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")


def _normal(value):
    return " ".join(value.casefold().replace("ё", "е").split())


def _schema(value):
    if isinstance(value, list):
        return [_schema(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {
        k: deepcopy(v) if k in {"default", "const", "enum", "examples"} else _schema(v)
        for k, v in value.items()
    }
    if result.get("type") == "object" and "properties" in result:
        result.setdefault("additionalProperties", False)
    return result


def directory_tools():
    """Only the routing mechanism is public; no bank catalog or operation schemas."""
    return [
        t.Tool(
            name="bank_directory",
            description="Найти банковские системы и инструменты, открыть входную схему операции.",
            inputSchema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "list_systems",
                            "search_systems",
                            "list_tools",
                            "search_tools",
                            "open_tool",
                        ],
                    },
                    **{
                        key: {"type": "string"}
                        for key in ("system_id", "tool_name", "query", "cursor")
                    },
                },
                "required": ["operation"],
                "additionalProperties": True,
            },
        ),
        t.Tool(
            name="bank_call",
            description="Вызвать операцию банковской системы после открытия её схемы через bank_directory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "system_id": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "arguments": {"type": "object", "additionalProperties": True},
                },
                "required": ["system_id", "tool_name", "arguments"],
                "additionalProperties": True,
            },
        ),
    ]


class BankDirectory:
    PAGE_SIZE = 5

    def __init__(self, systems: dict[str, list[t.Tool]], *, token: str):
        if set(systems) - SYSTEMS.keys():
            raise ValueError("Unknown banking systems")
        self._systems = {
            sid: {
                "system_id": sid,
                "name": SYSTEM_NAMES[sid],
                "description": SYSTEMS[sid],
            }
            for sid in sorted(SYSTEMS)
        }
        self._tools = {}
        for system, tools in systems.items():
            for tool in tools:
                key = (system, tool.name)
                if key in self._tools:
                    raise ValueError(f"Duplicate banking tool: {key}")
                description = (tool.description or tool.name).strip().splitlines()[0]
                schema = _schema(deepcopy(tool.input_schema))
                schema["additionalProperties"] = False
                schema.pop("title", None)
                schema.pop("description", None)
                Draft202012Validator.check_schema(schema)
                self._tools[key] = {
                    "system_id": system,
                    "tool_name": tool.name,
                    "description": description,
                    "input_schema": schema,
                }
        self._opened = set()
        self._fingerprint = hashlib.sha256(
            json.dumps(
                [token, self._systems, [self._tools[k] for k in sorted(self._tools)]],
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        self._pages = CatalogPages(self._fingerprint)

    def _tool(self, system, name):
        if not isinstance(system, str) or system not in self._systems:
            raise DirectoryError("UNKNOWN_SYSTEM", "Неизвестная банковская система")
        if not isinstance(name, str) or (system, name) not in self._tools:
            raise DirectoryError(
                "UNKNOWN_TOOL", "Система не предоставляет указанный инструмент"
            )
        return self._tools[system, name]

    def handle(self, request):
        if not isinstance(request, dict):
            raise DirectoryError("INVALID_ARGUMENT", "Запрос должен быть объектом")
        operation = request.get("operation")
        fields = {
            "list_systems": ({"operation"}, {"cursor"}),
            "search_systems": ({"operation", "query"}, {"cursor"}),
            "list_tools": ({"operation", "system_id"}, {"cursor"}),
            "search_tools": ({"operation", "query"}, {"cursor", "system_id"}),
            "open_tool": ({"operation", "system_id", "tool_name"}, set()),
        }
        if not isinstance(operation, str) or operation not in fields:
            raise DirectoryError("INVALID_ARGUMENT", "Неизвестная операция каталога")
        required, optional = fields[operation]
        if not required <= request.keys() or request.keys() - required - optional:
            raise DirectoryError(
                "INVALID_ARGUMENT", "Проверьте обязательные и допустимые поля операции"
            )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in request.values()
        ):
            raise DirectoryError(
                "INVALID_ARGUMENT", "Поля каталога должны быть непустыми строками"
            )
        system = request.get("system_id")
        if system is not None and system not in self._systems:
            raise DirectoryError("UNKNOWN_SYSTEM", "Неизвестная банковская система")
        if operation == "open_tool":
            tool = self._tool(system, request["tool_name"])
            self._opened.add((system, request["tool_name"]))
            return deepcopy(tool)
        if operation.endswith("systems"):
            items = list(self._systems.values())
        else:
            items = [
                {k: v for k, v in self._tools[key].items() if k != "input_schema"}
                for key in sorted(self._tools)
                if system is None or key[0] == system
            ]
        query = _normal(request.get("query", ""))
        if operation.startswith("search_"):
            items = [
                item
                for item in items
                if all(
                    word in _normal(" ".join(item.values())) for word in query.split()
                )
            ]
        try:
            return self._pages.page(
                items, [operation, system, query], request.get("cursor")
            )
        except CursorError as error:
            raise DirectoryError("INVALID_CURSOR", str(error)) from None

    def validate_call(self, payload):
        if not isinstance(payload, dict) or set(payload) != {
            "system_id",
            "tool_name",
            "arguments",
        }:
            raise DirectoryError(
                "INVALID_ARGUMENT", "Нужны system_id, tool_name и arguments"
            )
        system, name, arguments = (
            payload[k] for k in ("system_id", "tool_name", "arguments")
        )
        tool = self._tool(system, name)
        if (system, name) not in self._opened:
            raise DirectoryError(
                "TOOL_SCHEMA_NOT_OPENED",
                "Откройте входную схему через bank_directory.open_tool",
            )
        if not isinstance(arguments, dict):
            raise DirectoryError("INVALID_ARGUMENT", "arguments должен быть объектом")
        error = next(
            Draft202012Validator(tool["input_schema"]).iter_errors(arguments), None
        )
        if error:
            path = ".".join(str(p) for p in error.absolute_path)
            detail = f"нарушено правило {error.validator}"
            if error.validator == "type":
                detail = f"ожидается тип {error.validator_value}"
            elif error.validator == "required" and isinstance(error.instance, dict):
                detail = "отсутствуют поля: " + ", ".join(
                    sorted(set(error.validator_value) - error.instance.keys())
                )
            elif error.validator == "additionalProperties" and isinstance(
                error.instance, dict
            ):
                detail = "лишние поля: " + ", ".join(
                    sorted(
                        error.instance.keys()
                        - error.schema.get("properties", {}).keys()
                    )
                )
            raise DirectoryError(
                "INVALID_ARGUMENT", f"arguments{'.' + path if path else ''}: {detail}"
            )
        return system, name, deepcopy(arguments)
