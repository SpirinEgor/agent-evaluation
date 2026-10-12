"""Task-ID scope and idempotence of the two repaired generators."""

import importlib.util
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.mark.parametrize("number", [2, 7])
def test_scoped_idempotent_generator(number, tmp_path):
    root = Path(
        os.environ.get("BANK_REPAIR_SNAPSHOT", Path(__file__).resolve().parents[3])
    )
    path = root / f"scripts/banking_ru/build_bank{number}_reply_contract.py"
    spec = importlib.util.spec_from_file_location(f"generator_{number}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tasks = json.loads((root / "data/tau2/domains/banking_ru/tasks.json").read_text())
    damaged = deepcopy(tasks)
    selected = next(t for t in damaged if t["id"] == f"Bank {number}")
    selected["evaluation_criteria"]["completion_mode"] = "legacy_reply"
    selected["evaluation_criteria"]["message_claim_contract"] = {
        "version": "old",
        "families": [],
        "tool_arguments": {},
    }
    result = module.with_contract(damaged)
    assert [a["id"] for a, b in zip(damaged, result) if a != b] == [f"Bank {number}"]
    assert module.with_contract(result) == result
    assert result == tasks
    temporary = tmp_path / "tasks.json"
    temporary.write_text(json.dumps(damaged, ensure_ascii=False))
    subprocess_data = tmp_path / "data"
    subprocess_data.mkdir()
    (subprocess_data / "tasks.json").write_bytes(temporary.read_bytes())
    # Real CLI application is restricted to disposable data.
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, str(path), "--data-dir", str(subprocess_data)],
        check=True,
        capture_output=True,
    )
    assert json.loads((subprocess_data / "tasks.json").read_text()) == tasks
