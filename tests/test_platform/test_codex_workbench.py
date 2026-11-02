import asyncio
from copy import deepcopy

import pytest

from tau2.platform.agents import profile, prompt_for
from tau2.platform.agents.v1 import SCHEMA, Workspace, manifest
from tests.test_platform.test_codex_container import load_module, request_contract


def test_profile_contract_is_explicit_and_cannot_expand_baseline():
    contract, request = request_contract()
    contract["schemas"].append(deepcopy(SCHEMA))
    runtime = load_module()
    with pytest.raises(ValueError):
        runtime.dynamic_bank_tools(contract["schemas"])
    assert len(runtime.dynamic_bank_tools(contract["schemas"], "v1")) == 3
    contract["schemas"][-1]["description"] = "unapproved tool"
    with pytest.raises(ValueError):
        runtime.dynamic_bank_tools(contract["schemas"], "v1")
    with pytest.raises(ValueError):
        profile("v2")
    assert prompt_for("ticket") == "ticket"
    assert manifest()["version"] == "V1"
    assert len(manifest()["sha256"]) == 64


def test_notes_are_isolated_and_size_limited():
    async def check():
        a, b = Workspace(), Workspace()
        assert not (
            await a.call(
                "unused", {"operation": "set", "key": "facts", "value": {"x": 1}}
            )
        ).is_error
        assert (await b.call("unused", {"operation": "get", "key": "facts"})).content[
            0
        ].text == "null"
        assert (
            await a.call(
                "unused", {"operation": "set", "key": "large", "value": "x" * 70000}
            )
        ).is_error
        assert (
            await a.call(
                "unused", {"operation": "set", "key": "x", "value": 1, "invented": True}
            )
        ).is_error
        assert a.notes == {"facts": {"x": 1}}

    asyncio.run(check())
