"""Resolve declared reference arguments from successful preceding tool results."""

import json
import re
from copy import deepcopy

from tau2.environment.environment import Environment


def reference_arguments(action, arguments, bindings, received):
    """Bind only explicitly declared fields; never infer resource IDs from state.

    ``received`` contains successful results keyed by reference action ID. A
    missing or ambiguous source fails closed instead of using a stale literal.
    """
    arguments = deepcopy(arguments)
    for binding in bindings:
        if binding.target_action_id != action.action_id:
            continue
        if binding.source_action_id not in received:
            raise ValueError(
                f"Reference binding source unavailable: {binding.binding_id}"
            )
        # Gold tools can return models nested in containers. Resolve the exact
        # representation received by the participant, using the shared serializer.
        text = Environment.to_json_str(received[binding.source_action_id])
        if binding.result_path is not None:
            result = json.loads(text)
            for key in binding.result_path:
                if not isinstance(result, dict) or key not in result:
                    raise ValueError(
                        f"Reference binding path unavailable: {binding.binding_id}"
                    )
                result = result[key]
            values = [result]
        else:
            flags = re.I if binding.case_insensitive else 0
            values = [
                m.group("value")
                for m in re.finditer(binding.result_pattern, text, flags)
            ]
        if (
            not values
            or any(not isinstance(value, str) for value in values)
            or len(set(values)) != 1
        ):
            raise ValueError(
                f"Reference binding is not a unique string: {binding.binding_id}"
            )
        arguments[binding.argument_name] = values[0]
    return arguments
