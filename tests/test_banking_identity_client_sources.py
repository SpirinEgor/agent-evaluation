"""Identity generator supports canonical intake and historical aggregate DBs."""

import importlib.util
import json
from pathlib import Path

import pytest

from tau2.domains.banking_ru.data_model import ClientAnswer


def test_client_answer_accepts_canonical_rows_and_historical_keywords():
    canonical = ClientAnswer(answer="Клиент согласен")
    assert canonical.keywords == []
    historical = ClientAnswer(answer="Клиент согласен", keywords=["согласие"])
    assert historical.keywords == ["согласие"]


@pytest.fixture
def identity_generator(monkeypatch):
    directory = Path(__file__).resolve().parents[1] / "scripts/banking_ru"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(
        "identity_client_sources", directory / "build_identity_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_db_without_legacy_answers_is_unchanged(identity_generator):
    original = '{\n  "customers": {"c1": {"birth_date": "1990-01-01"}}\n}\n'
    db = json.loads(original)
    assert identity_generator.with_client_sources(db) is db
    assert identity_generator.render_client_sources(original, db) == original


def test_missing_legacy_answers_does_not_hide_other_changes(identity_generator):
    with pytest.raises(ValueError):
        identity_generator.render_client_sources(
            '{"customers": {}}', {"customers": {"c1": {}}}
        )


def test_historical_answers_keep_birth_date_aliases_and_other_bytes(identity_generator):
    original = '{ "customers": {"c1": {"birth_date": "1990-01-01"}}, "client_answers": {"c1": [{"answer": "Дата рождения — 1990-01-01", "keywords": ["дата"]}]}, "balance": 42 }\n'
    db = identity_generator.with_client_sources(json.loads(original))
    keywords = db["client_answers"]["c1"][0]["keywords"]
    assert keywords == ["дата", *identity_generator.BIRTH_DATE_ALIASES]
    identity_generator.with_client_sources(db)
    assert keywords == ["дата", *identity_generator.BIRTH_DATE_ALIASES]
    rendered = identity_generator.render_client_sources(original, db)
    assert json.loads(rendered) == db
    assert rendered.startswith(
        '{ "customers": {"c1": {"birth_date": "1990-01-01"}}, "client_answers": '
    )
    assert rendered.endswith(', "balance": 42 }\n')
