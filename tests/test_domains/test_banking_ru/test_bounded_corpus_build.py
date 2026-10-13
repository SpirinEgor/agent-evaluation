"""Build-level checks for fully readable shared corpus, without task execution."""

import importlib.util
import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[3] / "data/tau2/domains/banking_ru"
SCRIPT = DATA.parents[3] / "scripts/banking_ru/build_bounded_corpus.py"


def _builder():
    spec = importlib.util.spec_from_file_location("bounded_corpus_build", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_rejects_oversized_file_before_write(tmp_path):
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "oversized.md").write_text("line\n" * 201)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"documents": {"/oversized.md": {}}})
    )
    with pytest.raises(ValueError, match="200 lines"):
        _builder().validate_corpus(tmp_path)


def test_build_rejects_oversized_unchanged_source_before_output(tmp_path):
    import shutil

    target = tmp_path / "data"
    shutil.copytree(DATA, target)
    source = target / "corpus/knowledge/kb_101.md"
    source.write_text("line\n" * 201)
    tasks_before = (target / "tasks.json").read_bytes()
    with pytest.raises(ValueError, match="200 lines"):
        _builder().build(target)
    assert (target / "tasks.json").read_bytes() == tasks_before
