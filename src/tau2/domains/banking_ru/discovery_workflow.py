"""Shared discovery and deterministic customer-submitted reward statements."""

import hashlib
import json
import shlex
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import RootModel

from tau2.environment.tool import ToolExecutionRejected, as_tool
from tau2.environment.toolkit import ToolKitBase, ToolType, is_tool

SPECIAL_TOOLS = frozenset(
    {
        "get_reward_resolution_offers",
        "get_reward_resolution_components",
        "execute_reward_resolution",
        "refresh_cashback_settlement",
        "amend_cashback_investigation",
        "get_case_documents",
        "deliver_case_document",
        "get_bank_operation_status",
        "submit_cashback_review",
        "register_reward_statement",
        "get_reward_statement_status",
    }
)
CLIENT_TOOL = "submit_reward_statement"


class DiscoveryResponse(RootModel[object]):
    """Preserve the underlying bank JSON types."""


def statement_needed(tx):
    return tx.reward_discrepancy_type == "partner_activation_review"


def require_bank_acceptance(tools, customer_id, transaction_ids, case_id=None):
    for tid in transaction_ids:
        tx = tools._get_transaction(tid)
        if tx.customer_id != customer_id:
            raise ToolExecutionRejected("ERR_FOREIGN_RESOURCE")
        if statement_needed(tx) and not any(
            r["customer_id"] == customer_id
            and tid in r["transaction_ids"]
            and r["status"] == "accepted"
            and (case_id is None or r["case_id"] == case_id)
            for r in tools.db.reward_statement_registrations.values()
        ):
            raise ValueError(
                "ERR_STATEMENT_NOT_ACCEPTED: Банковское принятие заявления не получено"
            )


class DiscoveryBankingTools(ToolKitBase):
    """The same discovery protocol is available in every scenario."""

    @property
    def discovery_enabled(self):
        return bool(self.corpus.manifest.get("discoverable_workflows"))

    def get_tools(self, include=None):
        tools = super().get_tools(include)
        if self.discovery_enabled:
            tools = {k: v for k, v in tools.items() if k not in SPECIAL_TOOLS}
        return tools

    @is_tool(ToolType.READ, mutates_state=True)
    def unlock_discoverable_agent_tool(self, name: str) -> DiscoveryResponse:
        """Open the schema of a named special bank operation found in documentation.

        Args:
            name: Exact documented operation name.
        """
        if name not in SPECIAL_TOOLS:
            raise ValueError("ERR_UNKNOWN_TOOL")
        self.__dict__.setdefault("_unlocked_bank_tools", set()).add(name)
        return DiscoveryResponse(
            dict(
                name=name,
                description=as_tool(getattr(self, name))._get_description(),
                inputSchema=as_tool(getattr(self, name)).params.model_json_schema(),
            )
        )

    @is_tool(ToolType.WRITE)
    def call_discoverable_agent_tool(
        self, name: str, arguments: dict
    ) -> DiscoveryResponse:
        """Call a previously opened bank operation using its schema.

        Args:
            name: Exact name returned by unlock.
            arguments: JSON arguments matching the opened schema.
        """
        if name not in self.__dict__.get("_unlocked_bank_tools", set()):
            raise ValueError("ERR_TOOL_LOCKED")
        tool = as_tool(getattr(self, name))
        if set(arguments) - set(tool.params.model_fields):
            raise ValueError("ERR_INVALID_ARGUMENT")
        parsed = tool.params.model_validate(arguments, strict=True)
        values = {name: getattr(parsed, name) for name in tool.params.model_fields}
        self.__dict__["_discovery_dispatch_entered"] = True
        result = getattr(self, name)(**values)
        return result

    @is_tool(ToolType.READ, mutates_state=True)
    def give_discoverable_user_tool(self, name: str) -> DiscoveryResponse:
        """Make a documented action available to the deterministic customer channel.

        Args:
            name: Exact customer-action name from documentation.
        """
        if name != CLIENT_TOOL:
            raise ValueError("ERR_UNKNOWN_CUSTOMER_TOOL")
        self.__dict__.setdefault("_granted_customer_tools", set()).add(name)
        return DiscoveryResponse(
            dict(
                name=name,
                channel="ask_client",
                request_type="client_action",
                inputSchema=dict(
                    type="object",
                    properties=dict(
                        case_id=dict(type="string"),
                        transaction_ids=dict(
                            type="array", items=dict(type="string"), minItems=1
                        ),
                    ),
                    required=["case_id", "transaction_ids"],
                    additionalProperties=False,
                ),
            )
        )

    def perform_customer_action(self, customer_id, tool_name, arguments):
        self._get_customer(customer_id)
        self._require_verified(customer_id)

        def reject(message):
            self.db.reward_statement_attempts.append(
                dict(
                    customer_id=customer_id,
                    tool_name=tool_name,
                    arguments=deepcopy(arguments),
                    forbidden=True,
                )
            )
            raise ValueError(message)

        if tool_name != CLIENT_TOOL:
            reject("ERR_UNKNOWN_CUSTOMER_TOOL")
        if tool_name not in self.__dict__.get("_granted_customer_tools", set()):
            reject("ERR_CUSTOMER_TOOL_NOT_GIVEN")
        if set(arguments) != {"case_id", "transaction_ids"}:
            reject("ERR_INVALID_CLIENT_ACTION")
        case = self.db.cases.get(arguments["case_id"])
        ids = arguments["transaction_ids"]
        if not case or case.customer_id != customer_id:
            reject("ERR_FOREIGN_RESOURCE")
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            reject("ERR_INVALID_CLIENT_ACTION")
        for tid in ids:
            tx = self.db.transactions.get(tid)
            if not tx or tx.customer_id != customer_id:
                reject("ERR_FOREIGN_RESOURCE")
            period = (
                datetime.fromisoformat(tx.posted_at)
                .astimezone(ZoneInfo("Europe/Moscow"))
                .strftime("%Y-%m")
                if tx.posted_at
                else None
            )
            if (
                case.status != "open"
                or case.category != "cashback_investigation"
                or case.reward_period != period
                or case.reward_program
                not in ["standard_cashback", "cashback_reconciliation"]
            ):
                reject("ERR_CASE_SCOPE: Нужен открытый кейс того же расчётного периода")
            if not statement_needed(tx):
                reject(
                    "ERR_UNNECESSARY_STATEMENT: Для этого вида расхождения заявление не требуется"
                )
        key = (
            "client-statement:"
            + hashlib.sha256(
                json.dumps([customer_id, case.id, sorted(ids)]).encode()
            ).hexdigest()[:16]
        )
        row = dict(
            id=key,
            customer_id=customer_id,
            case_id=case.id,
            transaction_ids=sorted(ids),
            status="submitted",
            actor="customer",
        )
        self.db.reward_client_submissions.setdefault(key, row)
        return deepcopy(self.db.reward_client_submissions[key])

    @is_tool(ToolType.WRITE)
    def register_reward_statement(
        self, customer_id: str, submission_id: str, case_id: str
    ) -> DiscoveryResponse:
        """Register a received customer submission in bank processing.

        Args:
            customer_id: Identified owner.
            submission_id: Received customer submission ID.
            case_id: Own existing case.
        """
        self._get_customer(customer_id)
        self._require_verified(customer_id)
        row = self.db.reward_client_submissions.get(submission_id)
        case = self.db.cases.get(case_id)
        if (
            not row
            or row["customer_id"] != customer_id
            or row["case_id"] != case_id
            or not case
            or case.customer_id != customer_id
        ):
            raise ToolExecutionRejected("ERR_FOREIGN_OR_UNKNOWN_SUBMISSION")
        key = "bank-statement:" + submission_id
        self.db.reward_statement_registrations.setdefault(
            key,
            dict(
                id=key,
                submission_id=submission_id,
                customer_id=customer_id,
                case_id=case_id,
                transaction_ids=row["transaction_ids"],
                status="registered",
                update_available=True,
            ),
        )
        return DiscoveryResponse(deepcopy(self.db.reward_statement_registrations[key]))

    @is_tool(ToolType.READ, mutates_state=True)
    def get_reward_statement_status(
        self, customer_id: str, statement_id: str
    ) -> DiscoveryResponse:
        """Receive the bank processing event for a registered statement.

        Args:
            customer_id: Identified owner.
            statement_id: Bank registration ID.
        """
        self._get_customer(customer_id)
        self._require_verified(customer_id)
        row = self.db.reward_statement_registrations.get(statement_id)
        if not row or row["customer_id"] != customer_id:
            raise ToolExecutionRejected("ERR_FOREIGN_OR_UNKNOWN_STATEMENT")
        row.update(status="accepted", update_available=False)
        return DiscoveryResponse(deepcopy(row))

    def assert_reward_statements(self, customer_id: str) -> bool:
        if any(
            x["customer_id"] == customer_id and x["forbidden"]
            for x in self.db.reward_statement_attempts
        ):
            return False
        return all(
            any(
                r["customer_id"] == customer_id
                and tx.id in r["transaction_ids"]
                and r["status"] == "accepted"
                for r in self.db.reward_statement_registrations.values()
            )
            for tx in self.db.transactions.values()
            if tx.customer_id == customer_id and statement_needed(tx)
        )

    @is_tool(ToolType.READ)
    def corpus_command(self, command: str) -> DiscoveryResponse:
        """Read only the virtual bank corpus using one ls/find/grep/cat command.

        No OS shell, pipes, redirects or host paths. grep returns matches and full
        matching articles, up to 20 articles; use a narrower query if truncated.

        Args:
            command: One command; e.g. cat /knowledge/faq/doc.md or grep -E 'term' /knowledge.
        """
        words = shlex.split(command)
        if not words:
            raise ValueError("ERR_EMPTY_COMMAND")
        verb, *args = words
        if verb == "cat" and args:
            return DiscoveryResponse(
                dict(documents=[self.corpus.read(p) for p in args])
            )
        if verb == "ls" and len(args) <= 1:
            return DiscoveryResponse(self.corpus.list(args[0] if args else "/"))
        if verb == "find" and len(args) == 3 and args[1] == "-name":
            return DiscoveryResponse(self.corpus.find(args[2], args[0], 200))
        if verb == "grep":
            args = [a for a in args if a not in ["-n", "-E", "-i"]]
            if len(args) not in [1, 2]:
                raise ValueError("ERR_COMMAND_SYNTAX")
            found = self.corpus.search(args[0], args[1] if len(args) == 2 else "/", 200)
            paths = list(dict.fromkeys(x["path"] for x in found["matches"]))
            return DiscoveryResponse(
                dict(
                    **found,
                    documents=[self.corpus.read(p) for p in paths[:20]],
                    articles_truncated=len(paths) > 20,
                )
            )
        raise ValueError("ERR_COMMAND_NOT_SUPPORTED")
