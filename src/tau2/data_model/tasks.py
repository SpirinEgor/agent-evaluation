# Copyright Sierra

import json
import re
import textwrap
import uuid
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator
from typing_extensions import Annotated

from tau2.data_model.message import Message, ToolCall, ToolRequestor


class StructuredUserInstructions(BaseModel):
    """
    User instructions. This information defines the specific situation the user is in and the tasks they are trying to complete.
    """

    domain: Annotated[str, Field(description="The domain of the task.")]
    reason_for_call: Annotated[
        str, Field(description="The reason for the user to call the agent.")
    ]
    known_info: Annotated[
        Optional[str],
        Field(description="Known information about the user.", default=None),
    ]
    unknown_info: Annotated[
        Optional[str],
        Field(description="Unknown information about the user.", default=None),
    ]
    task_instructions: Annotated[str, Field(description="Instructions for the User.")]

    def __str__(self) -> str:
        lines = []
        tab = "\t"
        lines.append(f"Domain: {self.domain}")
        lines.append(f"Reason for call:\n{textwrap.indent(self.reason_for_call, tab)}")
        if self.known_info is not None:
            lines.append(f"Known info:\n{textwrap.indent(self.known_info, tab)}")
        if self.unknown_info is not None:
            lines.append(f"Unknown info:\n{textwrap.indent(self.unknown_info, tab)}")
        lines.append(
            f"Task instructions:\n{textwrap.indent(self.task_instructions, tab)}"
        )
        return "\n".join(lines)


UserInstructions = StructuredUserInstructions | str


class UserScenario(BaseModel):
    """
    User scenario. All the information that will be sent to the user simulator.
    """

    persona: Annotated[
        Optional[str],
        Field(
            description="User's persona. This information defines the user in general, not the specific situation they are in.",
            default=None,
        ),
    ]
    instructions: Annotated[
        UserInstructions,
        Field(
            description="Instructions for the User. This information defines the specific situation the user is in and the tasks they are trying to complete."
        ),
    ]

    def __str__(self) -> str:
        lines = []
        if self.persona is not None:
            lines.append("Persona:")
            lines.append(textwrap.indent(self.persona, "\t"))
        lines.append("Instructions:")
        lines.append(textwrap.indent(str(self.instructions), "\t"))
        return "\n".join(lines)


class Description(BaseModel):
    """
    Description of a scenario. This can be sent to the evaluator.
    """

    purpose: Annotated[
        Optional[str],
        Field(description="Explains what the scenario is testing.", default=None),
    ]
    relevant_policies: Annotated[
        Optional[str],
        Field(
            description="The part of the policy that is relevant to the scenario.",
            default=None,
        ),
    ]
    notes: Annotated[
        Optional[str],
        Field(
            description="Any additional information about the scenario that is not covered by the other fields.",
            default=None,
        ),
    ]

    def __str__(self) -> str:
        lines = []
        if self.purpose is not None:
            lines.append(f"Purpose: {self.purpose}")
        if self.relevant_policies is not None:
            lines.append(f"Relevant Policies: {self.relevant_policies}")
        if self.notes is not None:
            lines.append(f"Notes: {self.notes}")
        return "\n".join(lines)


class Action(BaseModel):
    """
    Descriptor for a tool call by the agent or the user.

    Used in two contexts:

    - `EvaluationCriteria.actions`: one reference trajectory replayed to
      derive the target DB end state. A per-call requirement only when
      `RewardType.ACTION` is in `reward_basis` and this action has
      `required_for_reward=True`. See
      `docs/evaluation.md`.
    - `InitialState.initialization_actions`: replayed before the
      simulation to set up the initial state.

    `compare_with_tool_call` matches an action against a tool call using
    `compare_args` (or all arguments if `compare_args` is None). It is
    only consulted by `ActionEvaluator`.

    Example:
      {
        "action_id": "get_user_details_1",
        "requestor": "assistant",
        "name": "get_user_details",
        "arguments": {"user_id": "sophia_silva_7557"},
        "compare_args": ["user_id"]
      }
    """

    action_id: str = Field(
        description="The unique identifier for the action within a scenario."
    )
    requestor: ToolRequestor = Field(
        description="The requestor of the action.",
        default="assistant",
    )
    name: str = Field(description="The name of the action.")
    alternative_tool_names: list[str] = Field(
        default_factory=list,
        description="Declared equivalent tool names for the same logical step; semantic source evidence is checked separately.",
    )
    arguments: dict = Field(description="The arguments for the action.")
    info: Optional[str] = Field(
        description="Information about the action.", default=None
    )
    compare_args: Optional[list[str]] = Field(
        description="The arguments to check in tool call. If None, will check all the arguments.",
        default=None,
    )
    db_bind_arguments: list[str] = Field(
        default_factory=list,
        description="Explicit un-compared arguments copied from successful received matching calls for exact DB reference replay.",
    )

    @model_validator(mode="after")
    def validate_db_bind_arguments(self):
        names = self.db_bind_arguments
        if names and (
            self.compare_args is None
            or len(names) != len(set(names))
            or not set(names).issubset(self.arguments)
            or set(names).intersection(self.compare_args)
        ):
            raise ValueError(
                "DB bindings require unique declared, explicitly un-compared arguments"
            )
        return self

    required_for_reward: bool = Field(
        description=(
            "Whether this reference action gates ACTION reward. False keeps "
            "the action in the reference replay without imposing its route "
            "on other valid trajectories."
        ),
        default=True,
    )
    result_contains: list[str] = Field(
        default_factory=list,
        description=(
            "Deterministic substrings required in a successful tool result "
            "when this action gates ACTION reward."
        ),
    )
    result_patterns: list[str] = Field(
        default_factory=list,
        description="Regexes over a successful received tool result; separate from final prose.",
    )

    @model_validator(mode="after")
    def validate_result_patterns(self):
        for pattern in self.result_patterns:
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"Invalid result pattern: {error}") from error
        return self

    attempt_requires_result: bool = Field(
        default=False,
        description=(
            "Use the result to distinguish the topic of an otherwise ambiguous "
            "dialogue call when identifying attempts. Default False identifies "
            "attempts by arguments, including failed calls. Do not enable this "
            "for protected reads or writes: a later success must not erase an "
            "earlier premature attempt."
        ),
    )
    argument_patterns: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Case-insensitive regexes that must each match the named tool argument "
            "in addition to compare_args. Useful for free-form questions without "
            "requiring exact wording."
        ),
    )
    argument_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Exact values at dot-separated object paths, e.g. request.type; absent keys never match.",
    )

    argument_options: dict[str, list[Any]] = Field(
        default_factory=dict,
        description="Allowed values for named arguments; missing and null are both None. Restricts semantic witnesses without prohibiting other diagnostic reads.",
    )

    def __str__(self) -> str:
        lines = []
        lines.append(f"Action ID: {self.action_id}")
        lines.append(f"Requestor: {self.requestor}")
        lines.append(f"Name: {self.name}")
        lines.append(f"Arguments:\n{json.dumps(self.arguments, indent=2)}")
        if self.info is not None:
            lines.append(f"Info:\n{textwrap.indent(self.info, '    ')}")
        return "\n".join(lines)

    def get_func_format(self) -> str:
        """
        Get the function format of the action.
        """
        return (
            f"{self.name}({', '.join([f'{k}={v}' for k, v in self.arguments.items()])})"
        )

    def compare_with_tool_call(self, tool_call: ToolCall) -> bool:
        """
        Compare the action with a tool call.
        If the name is not the same, return False.
        If compare_args is None, will check all the arguments.
        Otherwise, will check only the arguments in compare_args.
        """
        if self.requestor != tool_call.requestor or tool_call.name not in {
            self.name,
            *self.alternative_tool_names,
        }:
            return False
        if self.compare_args is None:
            compare_args = tool_call.arguments.keys()
        else:
            compare_args = self.compare_args
        tool_args = {k: v for k, v in tool_call.arguments.items() if k in compare_args}
        action_args = {k: v for k, v in self.arguments.items() if k in compare_args}
        if tool_args != action_args:
            return False
        if any(
            tool_call.arguments.get(key) not in values
            for key, values in self.argument_options.items()
        ):
            return False
        for path, expected in self.argument_values.items():
            value = tool_call.arguments
            for key in path.split("."):
                if not isinstance(value, dict) or key not in value:
                    return False
                value = value[key]
            if value != expected:
                return False
        return all(
            isinstance(tool_call.arguments.get(key), str)
            and all(
                re.search(pattern, tool_call.arguments[key], flags=re.IGNORECASE)
                for pattern in patterns
            )
            for key, patterns in self.argument_patterns.items()
        )


class EnvFunctionCall(BaseModel):
    """
    A function call on the agent or user environment.
    """

    env_type: Annotated[
        ToolRequestor,
        Field(description="The type of environment to call the function on."),
    ]
    func_name: Annotated[str, Field(description="The name of the function to call.")]
    arguments: Annotated[
        dict, Field(description="The arguments to pass to the function.")
    ]

    def __str__(self) -> str:
        lines = []
        lines.append(f"Env Type: {self.env_type}")
        lines.append(f"Func Name: {self.func_name}")
        lines.append(f"Arguments:\n{json.dumps(self.arguments, indent=2)}")
        return "\n".join(lines)


class EnvAssertion(EnvFunctionCall):
    """
    An assertion on the agent or user environment.
    """

    assert_value: Annotated[
        bool, Field(default=True, description="The value to assert on.")
    ]
    message: Annotated[
        Optional[str],
        Field(
            description="A message to display to the user if the assertion fails.",
            default=None,
        ),
    ]


class RewardType(str, Enum):
    """
    Components that can gate a task's final reward.

    The final reward is the product of every component listed in
    `EvaluationCriteria.reward_basis`. Components not listed are not
    included (they may still run for diagnostics). Default basis is
    `[DB, COMMUNICATE]`, matching the original τ-bench.

    - DB: predicted DB end state matches the target. Target is the
      result of replaying `EvaluationCriteria.actions` on a fresh env;
      any agent path producing an equivalent end state passes.
    - ENV_ASSERTION: all `env_assertions` pass on the predicted env.
    - COMMUNICATE: every `communicate_info` string appears (substring)
      in the agent's messages.
    - NL_ASSERTION: every `nl_assertions` entry is judged true by an
      LLM (experimental / WIP).
    - ACTION: every action with `required_for_reward=True` is matched
      by a distinct agent tool call (per `Action.compare_with_tool_call`).
      Optional reference steps remain part of the DB replay. Used by
      `banking_ru` and a few `banking_knowledge` tasks; not used in
      airline/retail/telecom.

    See `docs/evaluation.md`.
    """

    DB = "DB"
    ENV_ASSERTION = "ENV_ASSERTION"
    NL_ASSERTION = "NL_ASSERTION"
    ACTION = "ACTION"
    COMMUNICATE = "COMMUNICATE"


class TaskIssueStatus(str, Enum):
    """Status of a task issue."""

    OPEN = "open"
    RESOLVED = "resolved"
    WONT_FIX = "wont_fix"


class TaskIssue(BaseModel):
    """
    An issue or discussion point about a task.
    Used to track potential problems, decisions made, and their resolutions.
    """

    id: str = Field(description="Unique identifier for the issue.")
    title: str = Field(description="Short summary of the issue.")
    description: Annotated[
        Optional[str],
        Field(
            description="Detailed description of the issue or discussion point.",
            default=None,
        ),
    ]
    status: Annotated[
        TaskIssueStatus,
        Field(
            description="Current status of the issue.",
            default=TaskIssueStatus.OPEN,
        ),
    ]
    resolution: Annotated[
        Optional[str],
        Field(
            description="Explanation of how/why the issue was resolved or won't be fixed.",
            default=None,
        ),
    ]
    created_at: Annotated[
        Optional[str],
        Field(
            description="ISO date (YYYY-MM-DD) when the issue was created.",
            default=None,
        ),
    ]
    resolved_at: Annotated[
        Optional[str],
        Field(
            description="ISO date (YYYY-MM-DD) when the issue was resolved.",
            default=None,
        ),
    ]
    author_email: Annotated[
        Optional[str],
        Field(
            description="Email of the person who raised this issue.",
            default=None,
        ),
    ]
    pr_link: Annotated[
        Optional[str],
        Field(
            description="Link to the PR that fixes this issue.",
            default=None,
        ),
    ]
    simulation_file: Annotated[
        Optional[str],
        Field(
            description="Relative path to a simulation result file demonstrating the issue (e.g., 'task_issues/task_0_issue_001.json').",
            default=None,
        ),
    ]

    def __str__(self) -> str:
        lines = []
        status_icon = {"open": "🔴", "resolved": "✅", "wont_fix": "⚪"}.get(
            self.status.value, ""
        )
        lines.append(f"{status_icon} [{self.id}] {self.title}")
        if self.description:
            lines.append(f"  Description: {self.description}")
        if self.resolution:
            lines.append(f"  Resolution: {self.resolution}")
        if self.created_at:
            lines.append(f"  Created: {self.created_at}")
        if self.resolved_at:
            lines.append(f"  Resolved: {self.resolved_at}")
        if self.author_email:
            lines.append(f"  Author: {self.author_email}")
        if self.pr_link:
            lines.append(f"  PR: {self.pr_link}")
        if self.simulation_file:
            lines.append(f"  Simulation: {self.simulation_file}")
        return "\n".join(lines)


class ActionOrder(BaseModel):
    """Require one successful action witness before the first target attempt."""

    before_action_id: str
    after_action_id: str
    require_result_before_target: bool = Field(
        default=False,
        description=(
            "Require the predecessor's successful result to be available to the "
            "agent before the first target call is formed. Use for source facts "
            "and client responses; False checks execution order only."
        ),
    )


class ResultEvidence(BaseModel):
    """A fact read from a successful tool result, independent of the read route."""

    evidence_id: str = Field(min_length=1)
    tool_names: list[str] = Field(min_length=1)
    record_id: str = Field(min_length=1)
    expected_fields: dict[str, object] = Field(min_length=1)
    before_action_id: Optional[str] = None
    after_action_id: Optional[str] = None
    only_if_target_attempted: bool = Field(
        default=False,
        description="Check this source before every first target attempt; absence of an optional target does not require the source.",
    )


class ArgumentEvidence(BaseModel):
    """Client-visible argument facts in a successful call, optionally before a write."""

    evidence_id: str = Field(min_length=1)
    tool_names: list[str] = Field(min_length=1)
    argument_name: str = Field(min_length=1)
    patterns: list[str] = Field(min_length=1)
    before_action_id: Optional[str] = None
    tool_arguments: dict[str, list[str]] = Field(default_factory=dict)
    include_native_text: bool = False
    before_action_argument_pattern: Optional[str] = Field(
        default=None,
        min_length=1,
        description=(
            "Optional boundary within the first target call's argument_name. "
            "A fact in that exact argument may precede this pattern instead of "
            "preceding the call; source results must still precede call formation."
        ),
    )
    after_action_id: Optional[str] = None
    after_result_evidence_id: Optional[str] = Field(default=None, min_length=1)
    witness_capture: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_patterns(self):
        if self.before_action_argument_pattern is not None:
            if not self.before_action_id:
                raise ValueError(
                    "before_action_argument_pattern requires before_action_id"
                )
            try:
                re.compile(self.before_action_argument_pattern)
            except re.error as error:
                raise ValueError(
                    f"Invalid before_action_argument_pattern: {error}"
                ) from error
        for pattern in self.patterns:
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(
                    f"Invalid argument evidence pattern: {error}"
                ) from error
        if any(
            not name or not fields or any(not field for field in fields)
            for name, fields in self.tool_arguments.items()
        ):
            raise ValueError("Argument evidence needs nonempty declared channels")
        if self.witness_capture and not any(
            self.witness_capture in re.compile(pattern).groupindex
            for pattern in self.patterns
        ):
            raise ValueError(
                "Argument evidence witness capture is missing from its patterns"
            )
        return self


class ResultArgumentBinding(BaseModel):
    """Bind every target attempt's string argument to a received source value."""

    binding_id: str = Field(min_length=1)
    source_action_id: str = Field(min_length=1)
    target_action_id: str = Field(min_length=1)
    argument_name: str = Field(min_length=1)
    result_pattern: Optional[str] = Field(default=None, min_length=1)
    result_path: Optional[list[str]] = Field(
        default=None,
        description="Exact object key path to a string in a structured JSON result; exclusive with result_pattern.",
    )
    case_insensitive: bool = False
    only_if_target_attempted: bool = False
    allow_unknown_resource_error: Optional[str] = None

    @model_validator(mode="after")
    def validate_result_pattern(self):
        if self.allow_unknown_resource_error is not None:
            if not self.argument_name.endswith("_id") or not re.fullmatch(
                r"ERR_(?:[A-Z_]+_)?NOT_FOUND", self.allow_unknown_resource_error
            ):
                raise ValueError(
                    "Unknown-resource retry requires an ID argument and a not-found error"
                )
        if (self.result_pattern is None) == (self.result_path is None):
            raise ValueError("Result binding needs exactly one pattern or JSON path")
        if self.result_path is not None:
            if not self.result_path or any(not key for key in self.result_path):
                raise ValueError("Result binding JSON path needs nonempty keys")
        else:
            try:
                expression = re.compile(self.result_pattern)
            except re.error as error:
                raise ValueError(f"Invalid result binding pattern: {error}") from error
            if "value" not in expression.groupindex:
                raise ValueError("Result binding pattern needs a named value capture")
        if self.source_action_id == self.target_action_id:
            raise ValueError("Result binding source and target must differ")
        return self


class ForbiddenMessagePattern(BaseModel):
    """A prohibited value in client-visible assistant text, including chunks."""

    rule_id: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    all_tool_arguments: bool = False
    only_after_action_id: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Activate this utterance prohibition only after receiving a successful matching agent action result.",
    )
    native_text_scope: Literal["disclosure", "utterance"] = Field(
        default="disclosure",
        description=(
            "Disclosure checks individual and combined outgoing texts. Utterance "
            "checks native chunks joined only by overlapping utterance IDs, "
            "and checks each declared tool argument independently."
        ),
    )
    digit_word_aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Optional whole-word digit names normalized only for this prohibition.",
    )
    tool_arguments: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Optional client-visible tool arguments joined with native text "
            "to detect a secret split across outgoing channels."
        ),
    )

    @model_validator(mode="after")
    def validate_pattern(self):
        if (
            self.only_after_action_id is not None
            and self.native_text_scope != "utterance"
        ):
            raise ValueError("Temporal message prohibitions require utterance scope")
        try:
            re.compile(self.pattern)
        except re.error as error:
            raise ValueError(f"Invalid forbidden argument pattern: {error}") from error
        aliases = self.digit_word_aliases
        if any(
            not word.isalpha() or len(digit) != 1 or digit not in "0123456789"
            for word, digit in aliases.items()
        ):
            raise ValueError(
                "Digit aliases must map a single alphabetic word to one ASCII digit"
            )
        if len({word.casefold() for word in aliases}) != len(aliases):
            raise ValueError("Digit aliases must be unambiguous ignoring case")
        return self


class ForbiddenArgumentPattern(ForbiddenMessagePattern):
    """A prohibited value in an outgoing assistant tool-call argument."""

    tool_name: str = Field(min_length=1)
    argument_name: str = Field(min_length=1)


class DiscoveryArgument(BaseModel):
    """Declared backend normalization of a discovery selector."""

    digits_only: bool = False
    strip: bool = False
    lowercase: bool = False
    prefix_replacements: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_prefixes(self):
        prefixes = list(self.prefix_replacements)
        if any(not prefix for prefix in prefixes) or any(
            left.startswith(right) or right.startswith(left)
            for i, left in enumerate(prefixes)
            for right in prefixes[i + 1 :]
        ):
            raise ValueError(
                "Discovery prefix replacements must be nonempty and unambiguous"
            )
        return self


class ToolDiscoveryPolicy(BaseModel):
    """Minimal identity discovery, independent of a reference search spelling."""

    records_by_id: dict[str, dict[str, str]] = Field(min_length=1)
    arguments: dict[str, DiscoveryArgument] = Field(min_length=1)
    required_any_arguments: list[str] = Field(min_length=1)
    result_id_field: str = "id"

    @model_validator(mode="after")
    def validate_discovery_catalog(self):
        if not set(self.required_any_arguments) <= self.arguments.keys():
            raise ValueError("Discovery primary selectors must be declared arguments")
        if any(
            not set(self.arguments) <= record.keys()
            for record in self.records_by_id.values()
        ):
            raise ValueError("Discovery records must define every selector")
        return self


class ToolAccessPolicy(BaseModel):
    """Ownership and identity prerequisites for all protected tool attempts.

    Resource IDs are grouped by customer to keep the deterministic catalog
    compact. Results from explicitly declared tools may establish new IDs;
    they never reassign an existing ID to another owner.
    """

    policy_id: str = Field(min_length=1)
    allowed_customer_ids: list[str] = Field(min_length=1)
    authentication_action_ids: list[str] = Field(min_length=1)
    resources_by_customer: dict[str, dict[str, list[str]]]
    tool_arguments: dict[str, dict[str, str]]
    pre_auth_tools: list[str] = Field(default_factory=list)
    result_resource_collections: dict[str, list[str]] = Field(default_factory=dict)
    result_scope_owner_arguments: dict[str, str] = Field(default_factory=dict)
    result_id_field: str = "id"
    result_owner_field: str = "customer_id"
    discovery_tools: dict[str, ToolDiscoveryPolicy] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_access_catalog(self):
        if set(self.discovery_tools) & self.tool_arguments.keys():
            raise ValueError("Discovery tools cannot also be private resource tools")
        for discovery in self.discovery_tools.values():
            if not set(self.allowed_customer_ids) <= discovery.records_by_id.keys():
                raise ValueError("Discovery must include every allowed customer")
        collections = self.resources_by_customer.keys()
        if not set(self.pre_auth_tools) <= self.tool_arguments.keys():
            raise ValueError("Pre-auth tools must be declared protected tools")
        for fields in self.tool_arguments.values():
            if not set(fields.values()) <= collections:
                raise ValueError(
                    "Tool access arguments reference an undefined collection"
                )
        for names in self.result_resource_collections.values():
            if not set(names) <= collections:
                raise ValueError("Resource results reference an undefined collection")
        if not set(self.result_scope_owner_arguments) <= self.tool_arguments.keys():
            raise ValueError("Scoped resource results must name a protected tool")
        for owners in self.resources_by_customer.values():
            seen = set()
            for ids in owners.values():
                if seen.intersection(ids) or len(set(ids)) != len(ids):
                    raise ValueError("A resource must have exactly one declared owner")
                seen.update(ids)
        return self


class NumericMessageClaim(BaseModel):
    """Check a declared numerical claim against received tool-state updates."""

    rule_id: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    witness_capture: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Optional named capture marking the completed-operation predicate before a later optional amount suffix.",
    )
    initial_values: dict[str, float] = Field(default_factory=dict)
    resource_aliases: dict[str, list[str]] = Field(default_factory=dict)
    resource_pattern: Optional[str] = None
    value_aliases: dict[str, float] = Field(default_factory=dict)
    update_tool: str = Field(min_length=1)
    update_arguments: dict[str, Any] = Field(default_factory=dict)
    resource_argument: str = Field(min_length=1)
    value_argument: str = Field(min_length=1)
    result_resource_field: str = Field(min_length=1)
    result_value_field: str = Field(min_length=1)
    require_update: bool = False
    tool_arguments: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_claim(self):
        import math

        try:
            expression = re.compile(self.pattern)
            resource = (
                re.compile(self.resource_pattern) if self.resource_pattern else None
            )
        except re.error as error:
            raise ValueError(f"Invalid numeric claim pattern: {error}") from error
        if "value" not in expression.groupindex or (
            resource is not None and "resource" not in resource.groupindex
        ):
            raise ValueError("Numeric claims need value/resource named captures")
        if (
            self.witness_capture is not None
            and self.witness_capture not in expression.groupindex
        ):
            raise ValueError("Numeric claim witness capture must exist in its pattern")
        if any(
            not math.isfinite(value) or value < 0
            for value in [*self.initial_values.values(), *self.value_aliases.values()]
        ):
            raise ValueError("Numeric claim values must be finite and nonnegative")
        if any(
            not ids or not set(ids) <= self.initial_values.keys()
            for ids in self.resource_aliases.values()
        ):
            raise ValueError(
                "Numeric claim aliases must name declared initial resources"
            )
        return self


class MessageClaimContract(BaseModel):
    """Versioned finite banking grammar and declared outgoing tool fields."""

    version: str
    families: list[str]
    tool_arguments: dict[str, list[str]]

    @model_validator(mode="after")
    def supported_catalog(self):
        supported = {"card_block", "dispute", "transfer", "deposit_close"}
        if self.version != "banking-claims-v1":
            raise ValueError("Unsupported banking claim contract version")
        if (
            not self.families
            or not set(self.families) <= supported
            or len(set(self.families)) != len(self.families)
        ):
            raise ValueError("Unsupported or duplicate banking claim families")
        if any(
            not name
            or not fields
            or any(not field for field in fields)
            or len(fields) != len(set(fields))
            for name, fields in self.tool_arguments.items()
        ):
            raise ValueError(
                "Outgoing channels must declare nonempty unique field names"
            )
        return self


class EvaluationCriteria(BaseModel):
    """
    Evaluation criteria for a task. Sent to the evaluator.

    `reward_basis` controls which fields gate the reward; other
    populated fields run as diagnostics only. In particular, `actions`
    is one reference trajectory used to derive the target DB end state
    — not a per-call requirement on the agent unless `RewardType.ACTION`
    is in `reward_basis` and the action has `required_for_reward=True`.
    See `docs/evaluation.md`.
    """

    completion_mode: Literal["legacy_reply", "completion_only"] = "legacy_reply"

    action_reward_scope: Literal["full_contract", "presence_and_order"] = Field(
        default="full_contract",
        description=(
            "presence_and_order limits ACTION to logical tool steps and through-order; "
            "ENV_ASSERTION retains successful semantic witnesses and trajectory guards."
        ),
    )

    @model_validator(mode="after")
    def validate_action_reward_scope(self):
        if (
            self.action_reward_scope == "presence_and_order"
            and RewardType.ENV_ASSERTION not in self.reward_basis
        ):
            raise ValueError(
                "presence_and_order requires ENV_ASSERTION in reward_basis"
            )
        return self

    message_claim_contract: MessageClaimContract | None = Field(
        default=None,
        description="Optional finite outgoing operation assertion contract.",
    )

    actions: Annotated[
        Optional[list[Action]],
        Field(
            description=(
                "One reference trajectory that solves the task. Replayed "
                "on a fresh env by `EnvironmentEvaluator` to derive the "
                "target DB hash. The agent may take any path producing "
                "an equivalent end state — these specific calls are only "
                "required when `RewardType.ACTION` is in `reward_basis`."
            ),
            default=None,
        ),
    ]

    process_source_targets: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Explicit grading dependencies by process-policy virtual path. "
            "Overrides the policy builder's default targets; does not change "
            "the agent-visible environment."
        ),
    )

    db_alternative_action_overrides: list[dict[str, dict[str, Any]]] = Field(
        default_factory=list,
        description=(
            "Alternative reference DB states. Each item maps reference action IDs "
            "to argument overrides; all other actions are replayed unchanged. "
            "An agent DB matches when it equals any declared reference state."
        ),
    )

    db_alternative_action_replacements: list[dict[str, Action]] = Field(
        default_factory=list,
        description=(
            "Equivalent reference routes using a declared alternative tool. Each map "
            "replaces reference steps by ID only for DB replay; all safety and "
            "received-source constraints still apply to the actual trajectory."
        ),
    )

    @model_validator(mode="after")
    def validate_db_alternative_action_replacements(self):
        actions = {action.action_id: action for action in self.actions or []}
        for replacements in self.db_alternative_action_replacements:
            if not replacements or not set(replacements) <= actions.keys():
                raise ValueError("DB replacements must name existing reference actions")
            for action_id, replacement in replacements.items():
                original = actions[action_id]
                if (
                    replacement.action_id != action_id
                    or replacement.requestor != original.requestor
                    or replacement.name
                    not in [original.name, *original.alternative_tool_names]
                ):
                    raise ValueError(
                        "DB replacement must use its declared alternative tool"
                    )
        return self

    db_alternative_action_additions: list[list[Action]] = Field(
        default_factory=list,
        description=(
            "Explicit optional DB-reference additions, combined with each declared "
            "argument alternative. They do not create ACTION obligations; trajectory "
            "guards must separately validate their authorization and arguments."
        ),
    )

    @model_validator(mode="after")
    def validate_db_alternative_action_additions(self):
        reference_ids = {action.action_id for action in self.actions or []}
        for additions in self.db_alternative_action_additions:
            ids = [action.action_id for action in additions]
            if not ids or len(ids) != len(set(ids)) or reference_ids.intersection(ids):
                raise ValueError("DB additions must have distinct non-reference IDs")
            if any(action.required_for_reward for action in additions):
                raise ValueError(
                    "Optional DB additions must not require ACTION matches"
                )
        return self

    @model_validator(mode="after")
    def validate_db_alternative_action_overrides(self):
        actions = {action.action_id: action for action in self.actions or []}
        for overrides in self.db_alternative_action_overrides:
            if not overrides or not set(overrides) <= actions.keys():
                raise ValueError(
                    "DB alternative overrides must name existing reference actions"
                )
            for action_id, arguments in overrides.items():
                if (
                    not arguments
                    or not set(arguments) <= actions[action_id].arguments.keys()
                ):
                    raise ValueError(
                        "DB alternative overrides must name existing action arguments"
                    )
        return self

    require_successful_actions: bool = Field(
        default=False,
        description=(
            "When ACTION gates reward, a required reference action is witnessed "
            "only by a call with a non-error tool result."
        ),
    )

    action_order: list[ActionOrder] = Field(
        default_factory=list,
        description="Ordered prerequisite pairs among required ACTION witnesses.",
    )

    forbidden_actions: list[Action] = Field(
        default_factory=list,
        description=(
            "Tool calls prohibited by policy, matched by name and compare_args. "
            "A matching attempt fails ACTION even when the tool returns an error."
        ),
    )

    forbidden_argument_patterns: list[ForbiddenArgumentPattern] = Field(
        default_factory=list,
        description=(
            "Case-insensitive regexes forbidden in assistant tool-call arguments. "
            "A matching attempt fails ACTION even if the tool returns an error."
        ),
    )

    forbidden_message_patterns: list[ForbiddenMessagePattern] = Field(
        default_factory=list,
        description=(
            "Case-insensitive regexes forbidden in assistant text messages. "
            "Check individual messages and concatenated streaming chunks, "
            "not client input, backend results, or binary audio."
        ),
    )

    tool_access_policy: Optional[ToolAccessPolicy] = Field(
        default=None,
        description=(
            "Ownership and authentication for every protected attempt, including "
            "additional reads and failed calls. Not limited to reference actions."
        ),
    )

    numeric_message_claims: list[NumericMessageClaim] = Field(
        default_factory=list,
        description="Declared numeric outgoing claims checked against successful agent update results received before the claim.",
    )

    @model_validator(mode="after")
    def validate_tool_access_authentication(self):
        actions = {action.action_id: action for action in self.actions or []}
        for rule in self.forbidden_message_patterns:
            if rule.only_after_action_id is not None:
                if RewardType.ACTION not in self.reward_basis:
                    raise ValueError(
                        "Temporal message prohibition requires ACTION in reward_basis"
                    )
                action = actions.get(rule.only_after_action_id)
                if action is None or action.requestor != "assistant":
                    raise ValueError(
                        "Temporal message prohibition must reference an existing agent action"
                    )
        if any(
            rule.only_after_action_id is not None
            for rule in self.forbidden_argument_patterns
        ):
            raise ValueError(
                "Use forbidden_message_patterns for temporally scoped outgoing arguments"
            )
        if self.tool_access_policy is not None:
            if (
                not set(self.tool_access_policy.authentication_action_ids)
                <= actions.keys()
            ):
                raise ValueError(
                    "Tool access authentication must reference existing actions"
                )
            if any(
                actions[action_id].requestor != "assistant"
                for action_id in self.tool_access_policy.authentication_action_ids
            ):
                raise ValueError(
                    "Tool access authentication must be performed by the agent"
                )
        return self

    result_evidence: list[ResultEvidence] = Field(
        default_factory=list,
        description=(
            "Required records and fields in successful tool results. Multiple "
            "evidence items may be witnessed by one read or separate reads."
        ),
    )

    argument_evidence: list[ArgumentEvidence] = Field(
        default_factory=list,
        description=(
            "Required patterns in one successful assistant tool-call argument. "
            "Separate evidence items may be witnessed by the same or different calls."
        ),
    )

    result_argument_bindings: list[ResultArgumentBinding] = Field(
        default_factory=list,
        description=(
            "Every matching target attempt must use a string value extracted "
            "by a full-result regex with a named value capture from a previously "
            "received successful agent source result. Compare trimmed strings; "
            "optional case normalization must match the backend contract."
        ),
    )

    env_assertions: Annotated[
        Optional[list[EnvAssertion]],
        Field(
            description=(
                "Assertions on the predicted environment. Gates the "
                "reward only when `RewardType.ENV_ASSERTION` is in "
                "`reward_basis`."
            ),
            default=None,
        ),
    ]

    communicate_info: Annotated[  # TODO: Deprecate this
        Optional[list[str]],
        Field(
            description=(
                "Strings the agent must say to the user (substring "
                "match). Gates the reward only when "
                "`RewardType.COMMUNICATE` is in `reward_basis`."
            ),
            default=None,
        ),
    ]

    nl_assertions: Annotated[
        Optional[list[str]],
        Field(
            description=(
                "Natural-language assertions judged by an LLM. Gates the "
                "reward only when `RewardType.NL_ASSERTION` is in "
                "`reward_basis` (experimental / WIP)."
            ),
            default=None,
        ),
    ]

    reward_basis: Annotated[
        list[RewardType],
        Field(
            description=(
                "Components that gate the final reward (their per-component "
                "rewards are multiplied). Default `[DB, COMMUNICATE]` "
                "matches the original τ-bench."
            ),
            default_factory=lambda: [RewardType.DB, RewardType.COMMUNICATE],
        ),
    ]

    @model_validator(mode="after")
    def validate_action_order(self) -> "EvaluationCriteria":
        if (
            not self.action_order
            and not self.result_evidence
            and not self.argument_evidence
            and not self.result_argument_bindings
        ):
            return self
        if RewardType.ACTION not in self.reward_basis:
            raise ValueError(
                "action_order/result_evidence/argument_evidence/result_argument_bindings requires ACTION in reward_basis"
            )
        actions = {action.action_id: action for action in self.actions or []}
        for pair in self.action_order:
            if pair.before_action_id == pair.after_action_id:
                raise ValueError("action_order cannot refer to the same action twice")
            for action_id in (pair.before_action_id, pair.after_action_id):
                if action_id not in actions:
                    raise ValueError(f"action_order has unknown action_id {action_id}")
                if not actions[action_id].required_for_reward:
                    raise ValueError(
                        f"action_order refers to non-required action_id {action_id}"
                    )
        evidence_ids = [item.evidence_id for item in self.result_evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Result evidence IDs must be unique")
        for item in self.result_evidence:
            if item.only_if_target_attempted and not item.before_action_id:
                raise ValueError("Conditional source evidence needs before_action_id")
            if (
                item.before_action_id == item.after_action_id
                and item.before_action_id is not None
            ):
                raise ValueError("result_evidence before and after actions must differ")
            for action_id in (item.before_action_id, item.after_action_id):
                if action_id is None:
                    continue
                action = actions.get(action_id)
                if action is None or (
                    not action.required_for_reward
                    and not (
                        item.only_if_target_attempted
                        and action_id == item.before_action_id
                    )
                ):
                    raise ValueError(
                        "result_evidence refers to missing or non-required action_id "
                        f"{action_id}"
                    )
        for item in self.argument_evidence:
            if (
                item.after_result_evidence_id is not None
                and item.after_result_evidence_id not in evidence_ids
            ):
                raise ValueError(
                    "argument_evidence refers to missing result_evidence ID"
                )
            for action_id in (item.before_action_id, item.after_action_id):
                if action_id is None:
                    continue
                action = actions.get(action_id)
                if (
                    action is None
                    or not action.required_for_reward
                    or (
                        action_id == item.after_action_id
                        and action.requestor != "assistant"
                    )
                ):
                    raise ValueError(
                        "argument_evidence refers to missing, non-required or non-assistant action_id "
                        f"{action_id}"
                    )
            if (
                item.before_action_argument_pattern is not None
                and actions[item.before_action_id].requestor != "assistant"
            ):
                raise ValueError(
                    "before_action_argument_pattern requires an assistant target"
                )
        for item in self.result_argument_bindings:
            for action_id in (item.source_action_id, item.target_action_id):
                action = actions.get(action_id)
                if action is None or action.requestor != "assistant":
                    raise ValueError(
                        "Result binding must reference existing assistant actions"
                    )
        return self

    def __str__(self) -> str:
        lines = []
        if self.actions is not None:
            lines.append("Actions:")
            lines.extend(
                [textwrap.indent(str(action), "\t") for action in self.actions]
            )
        if self.env_assertions is not None:
            lines.append("Env Assertions:")
            lines.extend(
                [
                    textwrap.indent(str(assertion), "\t")
                    for assertion in self.env_assertions
                ]
            )
        if self.communicate_info is not None:
            lines.append("Communicate Info:")
            lines.extend(
                [textwrap.indent(info, "\t") for info in self.communicate_info]
            )
        if self.nl_assertions is not None:
            lines.append("NL Assertions:")
            lines.extend(
                [textwrap.indent(assertion, "\t") for assertion in self.nl_assertions]
            )
        return "\n".join(lines)

    def info(self) -> dict:
        num_agent_actions = (
            len([action for action in self.actions if action.requestor == "assistant"])
            if self.actions is not None
            else 0
        )
        num_user_actions = (
            len([action for action in self.actions if action.requestor == "user"])
            if self.actions is not None
            else 0
        )
        num_env_assertions = (
            len(self.env_assertions) if self.env_assertions is not None else 0
        )
        num_nl_assertions = (
            len(self.nl_assertions) if self.nl_assertions is not None else 0
        )
        return {
            "num_agent_actions": num_agent_actions,
            "num_user_actions": num_user_actions,
            "num_env_assertions": num_env_assertions,
            "num_nl_assertions": num_nl_assertions,
        }


class InitializationData(BaseModel):
    """
    Updates default data for the agent and the user.
    """

    agent_data: Annotated[
        Optional[dict],
        Field(description="Agent env update data.", default=None),
    ]
    user_data: Annotated[
        Optional[dict],
        Field(description="User env update data.", default=None),
    ]


class InitialState(BaseModel):
    """
    Initial state of the task.
    This will be used to set the initial state of the environment and of the orchestrator.
    """

    initialization_data: Annotated[
        Optional[InitializationData],
        Field(description="Initial env update data.", default=None),
    ]
    initialization_actions: Annotated[
        Optional[list[EnvFunctionCall]],
        Field(
            description="Initial actions to be taken on the environment.", default=None
        ),
    ]
    message_history: Annotated[
        Optional[list[Message]],
        Field(
            default=None,
            description="Messages that have already been exchanged between the user, the agent and the environment. This will be used to set the initial state of the environment and of the orchestrator. Last messages must be from the user or the agent.",
        ),
    ]

    def __str__(self) -> str:
        lines = []
        if self.initialization_data is not None:
            lines.append("Initialization Data:")
            lines.extend(
                [
                    textwrap.indent(
                        self.initialization_data.model_dump_json(indent=2), "\t"
                    )
                ]
            )
        if self.initialization_actions is not None:
            lines.append("Initialization Actions:")
            lines.extend(
                [
                    textwrap.indent(str(action), "\t")
                    for action in self.initialization_actions
                ]
            )
        if self.message_history is not None:
            lines.append("Message History:")
            lines.extend(
                [
                    textwrap.indent(str(message), "\t")
                    for message in self.message_history
                ]
            )
        return "\n".join(lines)


class Task(BaseModel):
    """
    A task for a particular domain. This will be sent to the user simulator, the environment and the evaluator.
    """

    id: str = Field(description="The unique identifier for the task.")
    description: Annotated[
        Optional[Description],
        Field(
            description="Description of the task. This can be sent to the evaluator.",
            default=None,
        ),
    ]
    user_scenario: Annotated[
        UserScenario,
        Field(
            description="User scenario. This information will be sent to the user simulator."
        ),
    ]
    ticket: Annotated[
        Optional[str],
        Field(
            description="Task in ticket format for solo agent solving.",
            default=None,
        ),
    ]
    initial_state: Annotated[
        Optional[InitialState],
        Field(
            description="Initial state of the task. This will be used to set the initial state of the environment and of the orchestrator.",
            default=None,
        ),
    ]
    evaluation_criteria: Annotated[
        Optional[EvaluationCriteria],
        Field(
            description="Evaluation criteria for the task. This will be sent to the evaluator.",
            default=None,
        ),
    ]
    issues: Annotated[
        Optional[list[TaskIssue]],
        Field(
            description="List of issues, discussions, or notes about this task.",
            default=None,
        ),
    ]
    required_documents: Annotated[
        Optional[list[str]],
        Field(
            description="List of document titles required to solve the task (knowledge domain).",
            default=None,
        ),
    ]
    user_tools: Annotated[
        Optional[list[str]],
        Field(
            description="List of user tool names available to the user simulator for this task. "
            "If None, all domain user tools are available (backward compatible). "
            "If empty list, no user tools are available.",
            default=None,
        ),
    ]

    def __str__(self) -> str:
        lines = []
        lines.append(f"ID: {self.id}")
        if self.description is not None:
            lines.append("Description:")
            lines.append(textwrap.indent(str(self.description), "\t"))
        lines.append("User Scenario:")
        lines.append(textwrap.indent(str(self.user_scenario), "\t"))
        if self.initial_state is not None:
            lines.append("Initial State:")
            lines.append(textwrap.indent(str(self.initial_state), "\t"))
        if self.evaluation_criteria is not None:
            lines.append("Evaluation Criteria:")
            lines.append(textwrap.indent(str(self.evaluation_criteria), "\t"))
        if self.issues is not None and len(self.issues) > 0:
            lines.append("Issues:")
            lines.extend([textwrap.indent(str(issue), "\t") for issue in self.issues])
        return "\n".join(lines)


def make_task_id() -> str:
    """
    Make a task id.
    """
    return str(uuid.uuid4())


def make_task(
    user_instructions: str,
    eval_criteria: EvaluationCriteria,
    initialization_data: Optional[InitializationData] = None,
    initialization_actions: Optional[list[EnvFunctionCall]] = None,
    message_history: Optional[list[Message]] = None,
) -> Task:
    """
    Make a task from a user instruction, an evaluation criteria and a message history.
    """

    user_scenario = UserScenario(instructions=user_instructions)
    evaluation_criteria = eval_criteria
    initial_state = None
    if message_history is not None:
        # Patch to consider empty list of tool calls as None.
        for message in message_history:
            if (
                message.role == "assistant"
                and isinstance(message.tool_calls, list)
                and len(message.tool_calls) == 0
            ):
                message.tool_calls = None

        initial_state = InitialState(
            initialization_data=initialization_data,
            initialization_actions=initialization_actions,
            message_history=message_history,
        )
    return Task(
        id=make_task_id(),
        user_scenario=user_scenario,
        evaluation_criteria=evaluation_criteria,
        initial_state=initial_state,
    )


def validate_message_claim_contracts_for_publication(
    tasks: list[Task], domain: str
) -> None:
    """Reject publication of banking tasks without the required four-family contract.

    Loading/evaluating old tasks remains compatible. Publication callers must
    explicitly invoke this gate; it does not migrate data or reinterpret history.
    """
    if domain != "banking_ru":
        return
    required = {"card_block", "dispute", "transfer", "deposit_close"}
    for task in tasks:
        criteria = task.evaluation_criteria
        if criteria is not None and criteria.completion_mode == "completion_only":
            if (
                any("reply" in a.func_name for a in criteria.env_assertions or [])
                or criteria.communicate_info
                or criteria.nl_assertions
                or criteria.message_claim_contract is not None
                or criteria.numeric_message_claims
                or any(
                    a.required_for_reward and a.name == "reply_to_ticket"
                    for a in criteria.actions or []
                )
            ):
                raise ValueError(
                    f"completion_only publication contains legacy reply requirements for {task.id}"
                )
            if not {RewardType.DB, RewardType.ENV_ASSERTION, RewardType.ACTION} <= set(
                criteria.reward_basis
            ):
                raise ValueError(
                    f"completion_only publication requires three reward components for {task.id}"
                )
            continue
        contract = (
            task.evaluation_criteria.message_claim_contract
            if task.evaluation_criteria
            else None
        )
        if (
            contract is None
            or contract.version != "banking-claims-v1"
            or not required <= set(contract.families)
        ):
            raise ValueError(
                f"Banking publication requires message_claim_contract for {task.id}"
            )
        channels = {"ask_client": "question", "reply_to_ticket": "text"}
        if any(
            field not in contract.tool_arguments.get(tool, [])
            for tool, field in channels.items()
        ):
            raise ValueError(
                f"Banking publication requires outgoing channel declarations for {task.id}"
            )
        if not {RewardType.DB, RewardType.ENV_ASSERTION, RewardType.ACTION} <= set(
            task.evaluation_criteria.reward_basis
        ):
            raise ValueError(
                f"Banking publication requires DB/ENV_ASSERTION/ACTION reward_basis for {task.id}"
            )
