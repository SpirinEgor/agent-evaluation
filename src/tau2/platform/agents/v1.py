"""Codex Workbench V1. Task-independent memory and container-local Python."""

import asyncio
import hashlib
import json
from pathlib import Path

INSTRUCTIONS = """Вы используете оболочку Codex Workbench V1.
Решайте поручение самостоятельно по доступным банковским источникам.
workbench — локальные заметки текущей попытки и изолированный Python.
Сохраняйте факты отдельно от предположений, с ID полученного ответа/источника.
Отмечайте неизвестные данные; полнота поиска не следует из первой найденной записи.
При необходимости считайте остатки, сверяйте прошлые исполнения и сравнивайте
допустимые альтернативы программно. Условия расчёта получите из документов:
код не подтверждает правильность исходных данных или интерпретации правил.
При изменении состояния перепроверяйте зависимые расчёты и версии.
До изменения банка проверьте основания, полномочия, согласия и ограничения.
После исполнения получите фактический результат, сверяйте его с целями.
Не считайте неизвестный исход успешным. Завершайте самостоятельно через done.
Не храните OTP и реквизиты карты в заметках. Локальные заметки не являются
банковскими записями и не заменяют действия через bank_call.
Python: данные передаются в переменной data, результат присвойте result.
Переменные Python между вызовами не сохраняются; переносите JSON через заметки.
"""
SCHEMA = dict(
    name="workbench",
    description="V1: заметки текущей попытки и Python-расчёты без банковских действий.",
    inputSchema={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["get", "set", "compute"]},
            "key": {"type": "string"},
            "value": {},
            "code": {"type": "string"},
            "data": {},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
)

# Runs only INSIDE the model container. Python is not treated as a security sandbox.
RUNNER = """
import json,sys,resource
resource.setrlimit(resource.RLIMIT_CPU,(2,2))
resource.setrlimit(resource.RLIMIT_AS,(134217728,134217728))
p=json.load(sys.stdin)
scope={'data':p.get('data')}
exec(p['code'],scope)
out=json.dumps(scope.get('result'),ensure_ascii=False,allow_nan=False)
if len(out.encode())>32768: raise ValueError('Result exceeds 32 KiB')
sys.stdout.write('\\nWORKBENCH_RESULT:'+out)
"""


def manifest():
    return {
        "id": "codex_workbench",
        "version": "V1",
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


class Workspace:
    def __init__(self):
        self.notes = {}
        self.events = []

    async def call(self, name, args):
        from mcp.types import CallToolResult, TextContent

        try:
            from jsonschema import validate

            validate(args, SCHEMA["inputSchema"])
            op = args["operation"]
            if op == "set":
                if set(args) != {"operation", "key", "value"}:
                    raise ValueError("set requires key and value")
                notes = {**self.notes, args["key"]: args["value"]}
                if len(json.dumps(notes).encode()) > 65536:
                    raise ValueError("Notes exceed 64 KiB")
                self.notes = notes
                output = {"saved": args["key"]}
            elif op == "get":
                if set(args) - {"operation", "key"}:
                    raise ValueError("get accepts only key")
                output = self.notes.get(args["key"]) if "key" in args else self.notes
            else:
                if set(args) - {"operation", "code", "data"} or not isinstance(
                    args.get("code"), str
                ):
                    raise ValueError("compute requires code and optional data")
                if len(json.dumps(args).encode()) > 65536:
                    raise ValueError("Input exceeds 64 KiB")
                process = await asyncio.create_subprocess_exec(
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "python3",
                    "-I",
                    "-c",
                    RUNNER,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:

                    async def exchange():
                        process.stdin.write(json.dumps(args).encode())
                        await process.stdin.drain()
                        process.stdin.close()
                        chunks = []
                        size = 0
                        while chunk := await process.stdout.read(4096):
                            size += len(chunk)
                            if size > 65536:
                                raise ValueError("Python output exceeds 64 KiB")
                            chunks.append(chunk)
                        await process.wait()
                        return b"".join(chunks)

                    stdout = await asyncio.wait_for(exchange(), 5)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                if process.returncode:
                    raise ValueError(
                        "Python failed; check code/types or resource limits"
                    )
                output = json.loads(stdout.decode().rsplit("WORKBENCH_RESULT:", 1)[1])
            text = json.dumps(output, ensure_ascii=False)
            error = False
        except Exception as exc:
            text = f"WORKBENCH_ERROR: {type(exc).__name__}: {str(exc)[:200]}"
            error = True
        self.events.append({"arguments": args, "output": text, "error": error})
        return CallToolResult(
            content=[TextContent(type="text", text=text)], isError=error
        )
