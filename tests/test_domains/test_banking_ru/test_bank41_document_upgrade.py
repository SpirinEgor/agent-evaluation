"""Small Bank41 DB/ENV/ACTION trajectory audit in actual HALF and FULL graders."""

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tau2.data_model.message import AssistantMessage, Tick, ToolMessage
from tau2.data_model.simulation import SimulationRun
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.orchestrator.modes import CommunicationMode
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

DOC = "case_1000:registration_confirmation"
CONTACT = "doc-contact-egorova_n_3358"
ROUTES = [
    ("reference", 1),
    ("archived_attempt_repaired", 0),
    ("other_owner_case_attempt_repaired", 0),
    ("unfinished", 0),
    ("noop", 0),
    ("without_document", 0),
    ("direct_document_read", 1),
    ("skip_status_ready", 1),
    ("idempotent_delivery_retry", 1),
    ("preparing_poll", 1),
    ("temporary_delivery_retry", 1),
    ("internal_document", 0),
    ("foreign_document", 0),
    ("unverified_contact", 0),
    ("early_delivery_repaired", 0),
    ("case_id_before_received", 0),
    ("metadata_before_received", 0),
    ("delivery_before_protocol", 0),
    ("preparing_delivery_repaired", 1),
    ("wrong_case_document", 0),
    ("internal_attempt_repaired", 0),
    ("foreign_attempt_repaired", 0),
    ("unverified_attempt_repaired", 0),
]


@pytest.mark.parametrize("route,expected", ROUTES, ids=[r for r, _ in ROUTES])
def test_bank41_document_routes(route, expected):
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 41"))
    if route in {
        "preparing_poll",
        "preparing_delivery_repaired",
        "temporary_delivery_retry",
    }:
        task.initial_state.initialization_data.agent_data[
            "document_service_profiles"
        ] = {
            "egorova_n_3358": dict(
                confirmation_readiness="preparing"
                if route != "temporary_delivery_retry"
                else "ready",
                confirmation_version="1",
                transient_delivery_failures=1
                if route == "temporary_delivery_retry"
                else 0,
            )
        }
    session = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
    pending = []
    delivery_args = dict(
        document_id=DOC, contact_id=CONTACT, idempotency_key="customer-confirmation"
    )
    if route != "noop":
        for action in task.evaluation_criteria.actions:
            name, args = action.name, deepcopy(action.arguments)
            if action.action_id == "bank41:case-id-use":
                continue
            if route == "without_document" and action.action_id.startswith(
                "bank41:document"
            ):
                continue
            if (
                route in {"skip_status_ready", "metadata_before_received"}
                and name == "get_document_status"
            ):
                continue
            if (
                route == "delivery_before_protocol"
                and action.action_id == "bank41:delivery-protocol"
            ):
                continue
            if name == "get_case_documents" and route == "direct_document_read":
                name, args = "get_documents", {"customer_id": "egorova_n_3358"}
            if name == "get_document_status" and route == "preparing_delivery_repaired":
                session.call("deliver_case_document", delivery_args)
            if name == "get_case_documents" and route == "early_delivery_repaired":
                session.call("deliver_case_document", delivery_args)
            if (
                name == "create_cashback_investigation"
                and route == "case_id_before_received"
            ):
                attempt = session.begin_call(name, args, None)
                session.call(name, args, attempt=attempt)
                pending.append(attempt)
                continue
            if name == "get_case_documents" and route == "metadata_before_received":
                attempt = session.begin_call(name, args, None)
                session.call(name, args, attempt=attempt)
                pending.append(attempt)
                continue
            if name == "deliver_case_document":
                args = deepcopy(delivery_args)
                if route == "internal_document":
                    args["document_id"] = "case_1000:internal_diagnostics"
                if route == "foreign_document":
                    args["document_id"] = "doc_foreign_registration"
                if route == "unverified_contact":
                    args["contact_id"] = "doc-contact-unconfirmed-egorova_n_3358"
                if route == "wrong_case_document":
                    args["document_id"] = "doc_refusal_3358"
                if route in {
                    "internal_attempt_repaired",
                    "foreign_attempt_repaired",
                    "unverified_attempt_repaired",
                    "archived_attempt_repaired",
                    "other_owner_case_attempt_repaired",
                }:
                    bad = deepcopy(args)
                    if route == "internal_attempt_repaired":
                        bad["document_id"] = "case_1000:internal_diagnostics"
                    if route == "foreign_attempt_repaired":
                        bad["document_id"] = "doc_foreign_registration"
                    if route == "unverified_attempt_repaired":
                        bad["contact_id"] = "doc-contact-unconfirmed-egorova_n_3358"
                    if route == "archived_attempt_repaired":
                        bad["document_id"] = (
                            "case_1000:registration_confirmation:archived"
                        )
                    if route == "other_owner_case_attempt_repaired":
                        bad["document_id"] = "doc_other_case_3358"
                    session.call(name, bad)
                if route == "temporary_delivery_retry":
                    session.call(name, deepcopy(args))
            session.call(name, args)
            if name == "deliver_case_document" and route == "idempotent_delivery_retry":
                session.call(name, deepcopy(args))
        for attempt in pending:
            session.result_sent(attempt)
    if route != "unfinished":
        session.call("done", {})
    half = session.grade()
    if route == "unfinished":
        ticks = [
            Tick(
                tick_id=i,
                timestamp=session.created_at.isoformat(),
                agent_chunk=message if isinstance(message, AssistantMessage) else None,
                agent_tool_calls=(message.tool_calls or [])
                if isinstance(message, AssistantMessage)
                else [],
                agent_tool_results=[message]
                if isinstance(message, ToolMessage)
                else [],
            )
            for i, message in enumerate(session.trajectory())
        ]
        end = datetime.now(timezone.utc)
        simulation = SimulationRun(
            id=session.token,
            task_id=task.id,
            start_time=session.created_at.isoformat(),
            end_time=end.isoformat(),
            duration=(end - session.created_at).total_seconds(),
            termination_reason=session.termination_reason(),
            ticks=ticks,
            mode=CommunicationMode.FULL_DUPLEX.value,
            info={"platform": "mcp", "mcp_events": session.mcp_events},
        )
        full = SimpleNamespace(
            reward_info=evaluate_simulation(
                simulation=simulation,
                task=task,
                evaluation_type=EvaluationType.ALL,
                solo_mode=True,
                domain=session.env.domain_name,
                mode=CommunicationMode.FULL_DUPLEX,
                strict_replay=True,
                env_kwargs=session.env.evaluation_env_kwargs,
            ),
            invocation=dict(mode="full_duplex", scope="unfinished_actual_session"),
        )
    else:
        full = grade_full_session(session)
    if dest := os.environ.get("BANK41_DOCUMENT_EVIDENCE"):
        folder = Path(dest)
        folder.mkdir(parents=True, exist_ok=True)
        payload = dict(
            route=route,
            expected=expected,
            half=half.model_dump(mode="json"),
            full=full.reward_info.model_dump(mode="json"),
            invocation=full.invocation,
            errors=session.errors,
            calls=[
                dict(call=c.model_dump(mode="json"), result=r.model_dump(mode="json"))
                for c, r in session.log
            ],
            mcp_events=session.mcp_events,
        )
        (folder / (route + ".json")).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2).replace(
                "мускат", "[MASKED]"
            )
        )
    assert half.reward == expected, half.info
    assert full.reward_info.reward == expected, full.reward_info.info


# Explicitly retain the historical scenario version; composite tests live separately.
pytestmark = pytest.mark.usefixtures("fees_rewards_parent_scenarios")
