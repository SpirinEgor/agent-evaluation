"""Helpers for real banking-tool calls; no model/API or evaluator shortcuts."""

import json
from copy import deepcopy

from tau2.data_model.message import ToolCall
from tau2.environment.environment import Environment


def snapshot(env: Environment) -> dict:
    """Include session and replies omitted by the graded DB hash, but not logs."""
    return deepcopy(
        {
            "db": env.tools.db.model_dump(),
            "session": env.tools.db.session.model_dump(),
            "replies": env.tools.db.ticket_replies,
        }
    )


def call(env: Environment, tool_name: str, /, **arguments):
    return env.get_response(
        ToolCall(id="contract", name=tool_name, arguments=arguments)
    )


def succeed(env: Environment, tool_name: str, /, **arguments):
    """Return the serialized value seen by the agent, requiring success."""
    response = call(env, tool_name, **arguments)
    assert not response.error, response.content
    if not response.content.startswith(("{", "[")):
        return response.content
    try:
        return json.loads(response.content)
    except json.JSONDecodeError:
        return response.content


def reject(env: Environment, tool_name: str, error_code: str, /, **arguments):
    """A rejected call must carry the expected error and preserve all state."""
    before = snapshot(env)
    response = call(env, tool_name, **arguments)
    assert response.error, response.content
    assert error_code in response.content, response.content
    assert snapshot(env) == before, f"{tool_name}: rejected call changed state"
    return response


def identify(env: Environment, customer_id: str):
    """Backend-unit-test setup, not proof of an agent-visible identity route."""
    succeed(
        env,
        "verify_identity",
        customer_id=customer_id,
        credential=env.tools.db.customers[customer_id].code_word,
    )


def authorize_otp(env: Environment, customer_id: str):
    identify(env, customer_id)
    succeed(env, "send_otp", customer_id=customer_id)
    succeed(
        env,
        "check_otp",
        customer_id=customer_id,
        code=env.tools.db.customers[customer_id].otp_code,
    )
