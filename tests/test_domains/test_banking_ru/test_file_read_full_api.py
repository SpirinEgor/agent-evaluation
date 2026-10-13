"""Whole-file knowledge API: ranges cannot hide unreceived source content."""

import pytest

from tau2.domains.banking_ru.environment import get_environment


def test_file_read_exposes_only_path_and_returns_complete_text():
    env = get_environment(solo_mode=True)
    tool = env.tools.get_tools()["file_read"]
    schema = tool.openai_schema["function"]["parameters"]
    assert set(schema["properties"]) == {"path"}
    body = env.tools.file_read(path="/policies/general.md").root
    assert body["complete"] is True
    assert body["content"] == env.tools.corpus.documents[body["path"]]["content"]
    assert body["total_lines"] == len(body["content"].splitlines())
    assert not (
        {"start_line", "end_line", "requested_start_line", "requested_end_line"}
        & body.keys()
    )


@pytest.mark.parametrize("argument", ["start_line", "end_line"])
def test_file_read_rejects_range_arguments(argument):
    env = get_environment(solo_mode=True)
    with pytest.raises(TypeError):
        env.tools.file_read(path="/policies/general.md", **{argument: 12})
