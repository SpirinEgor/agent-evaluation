"""Standalone API fixtures; no benchmark task or historical snapshot is loaded."""

from pathlib import Path

import pytest


@pytest.fixture
def operation_api_scenarios(monkeypatch):
    from tau2.domains.banking_ru import environment, file_pilot, utils

    path = Path(__file__).resolve().parents[2] / "fixtures/banking_ru/operation_api"
    for module in (environment, file_pilot, utils):
        for name, value in (
            ("BANKING_RU_DATA_DIR", path),
            ("BANKING_RU_DB_PATH", path / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", path / "tasks.json"),
        ):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)


@pytest.fixture
def investigation_api_scenarios(monkeypatch):
    from tau2.domains.banking_ru import environment, file_pilot, utils

    path = Path(__file__).resolve().parents[2] / "fixtures/banking_ru/investigation_api"
    for module in (environment, file_pilot, utils):
        for name, value in (
            ("BANKING_RU_DATA_DIR", path),
            ("BANKING_RU_DB_PATH", path / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", path / "tasks.json"),
        ):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
