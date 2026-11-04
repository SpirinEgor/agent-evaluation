"""Offline guarantees for optional export of completed bank attempts."""

import json
from contextlib import contextmanager

import pytest

from tau2.platform.langfuse_export import export_attempt


class Client:
    def __init__(self):
        self.observations = []
        self.scores = []
        self.flushed = False

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.observations.append(kwargs)
        yield self

    def update_trace(self, **kwargs):
        self.trace = kwargs

    def create_score(self, **kwargs):
        self.scores.append(kwargs)

    def flush(self):
        self.flushed = True


def record():
    return {
        "task_id": "Bank 2",
        "token": "private-session-token",
        "final_reward": 1,
        "classification": "success",
        "result": {
            "reward": 1,
            "reward_breakdown": {"DB": 1, "ACTION": 1},
            "mcp_events": [
                {
                    "system": "crm",
                    "canonical_name": "find_customer",
                    "original_name": "find_customer",
                    "arguments": {"phone": "synthetic-phone"},
                    "response": "private-session-token found",
                    "error": False,
                    "requested_at": "2026-10-27T10:00:00Z",
                },
                {
                    "system": "cards",
                    "original_name": "get_cards",
                    "arguments": {"unknown": 3},
                    "response": "ERR_INVALID_ARGUMENT",
                    "error": True,
                },
            ],
        },
        "run": {
            "stdout": "\n".join(
                [
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "a",
                                "type": "agent_message",
                                "text": "I will check",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn.completed",
                            "usage": {"input_tokens": 10, "output_tokens": 2},
                        }
                    ),
                ]
            )
        },
    }


def test_exports_messages_tools_and_scores_without_session_credential():
    c = Client()
    original = record()
    before = json.dumps(original, sort_keys=True)
    result = export_attempt(
        original, model="fixture-model", ticket="synthetic ticket", client=c
    )
    assert result["status"] == "submitted"
    assert c.flushed
    assert c.observations[0]["as_type"] == "agent"
    assert [o["name"] for o in c.observations[1:]] == [
        "agent.message",
        "crm.find_customer",
        "cards.get_cards",
    ]
    assert c.observations[-1]["level"] == "ERROR"
    assert c.observations[1]["output"] == "I will check"
    assert c.observations[2]["input"] == {"phone": "synthetic-phone"}
    assert "private-session-token" not in json.dumps(c.observations)
    assert c.scores[0]["value"] == 1
    assert {s["name"] for s in c.scores} == {"bank_reward", "DB", "ACTION"}
    assert before == json.dumps(original, sort_keys=True)


def test_disabled_export_never_constructs_client(monkeypatch):
    monkeypatch.delenv("BANKING_LANGFUSE_ENABLED", raising=False)
    assert export_attempt(record(), model="m", ticket="t") == {"status": "disabled"}


def test_export_failure_does_not_change_business_result():
    class Broken(Client):
        def flush(self):
            raise RuntimeError("secret transport message")

    r = record()
    result = export_attempt(r, model="m", ticket="t", client=Broken())
    assert result == {"status": "failed", "error_type": "RuntimeError"}
    assert r["final_reward"] == 1


def test_missing_usage_is_explicit_and_no_fake_generation():
    c = Client()
    r = record()
    r["run"] = {"stdout": "not json\n"}
    export_attempt(r, model="m", ticket="t", client=c)
    assert c.observations[0]["metadata"]["usage"] is None
    assert not any(o["as_type"] == "generation" for o in c.observations)


def test_export_loads_only_hash_bound_platform_events(tmp_path):
    import hashlib
    import json

    from tau2.platform.langfuse_export import recorded_events

    journal = {
        "id": "session",
        "task_id": "Bank 2",
        "info": {"mcp_events": [{"canonical_name": "get_accounts"}]},
    }
    path = tmp_path / "platform" / "session.json"
    path.parent.mkdir()
    path.write_text(json.dumps(journal))
    record = {
        "token": "session",
        "task_id": "Bank 2",
        "platform_event_status": "linked",
        "platform_event_path": "platform/session.json",
        "platform_event_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "result": {},
    }
    assert recorded_events(record, tmp_path) == journal["info"]["mcp_events"]
    path.write_text("{}")
    with pytest.raises(ValueError, match="hash"):
        recorded_events(record, tmp_path)


def test_exports_observed_metrics_and_skips_unknown_values():
    c = Client()
    original = record()
    original["metrics"] = {
        "resolution_seconds": 12.5,
        "calls": {"total": 7, "discovery": 2},
        "format_accuracy": {"rate": 1.0},
        "compute": {"model_requests": None},
    }
    export_attempt(original, model="fixture-model", ticket="ticket", client=c)
    scores = {s["name"]: s["value"] for s in c.scores}
    assert scores["tool_calls"] == 7
    assert scores["resolution_seconds"] == 12.5
    assert "model_requests" not in scores


@pytest.fixture
def sdk_attempt(tmp_path):
    files = {
        "evaluation-input.json": {
            "task_id": "Bank fixture",
            "report": {"bank_calls": 1, "discovery_calls": 0, "status": "done"},
            "journal": [
                {
                    "seq": 0,
                    "applied": True,
                    "action": {
                        "kind": "tool_call",
                        "name": "bank_call",
                        "arguments": {
                            "system_id": "support",
                            "tool_name": "done",
                            "arguments": {},
                        },
                    },
                    "content": "###STOP###",
                }
            ],
        },
        "evaluation.json": {
            "values": {
                "reward": 1,
                "DB": 1,
                "ACTION": 1,
                "bank_calls": 1,
                "discovery_calls": 0,
                "protocol_completion": True,
            }
        },
        "sdk/task-input.json": {
            "id": "Bank fixture",
            "input": {"ticket": "Saved ticket"},
        },
        "native-run.json": {
            "request_contract": {
                "prompt": "Saved ticket",
                "startup": "Saved policy",
                "model": "test-model",
                "effort": "low",
            },
            "agent_profile": {"version": "V1"},
            "elapsed_seconds": 2.5,
            "exit_code": 0,
            "stdout": "",
            "workbench_events": [
                {"arguments": {"operation": "compute"}, "output": "2", "error": False}
            ],
        },
        "started.json": {"model": "test-model", "git_commit": "saved-commit"},
    }
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data))
    return tmp_path


def test_sdk_import_uses_saved_inputs_results_and_workbench(sdk_attempt):
    from tau2.platform.langfuse_export import sdk_attempt_record

    before = {p: p.read_bytes() for p in sdk_attempt.rglob("*.json")}
    r, metadata, model = sdk_attempt_record(sdk_attempt)
    c = Client()
    export_attempt(r, model=model, ticket=r["ticket"], metadata=metadata, client=c)
    assert c.observations[0]["input"] == {
        "ticket": "Saved ticket",
        "startup": "Saved policy",
        "agent_input": "Saved ticket",
    }
    assert [x["name"] for x in c.observations[1:]] == ["support.done", "v1.workbench"]
    assert c.observations[1]["output"] == "###STOP###"
    assert {s["name"]: s["value"] for s in c.scores}["DB"] == 1
    assert metadata["git_commit"] == "saved-commit"
    assert len(metadata["source_sha256"]) == 5
    assert r["metrics"]["compute"]["tokens"] is None
    assert before == {p: p.read_bytes() for p in sdk_attempt.rglob("*.json")}
    again = sdk_attempt_record(sdk_attempt)
    assert again[0]["token"] == r["token"]


@pytest.mark.parametrize(
    "defect", ["task", "ticket", "model", "counter", "grade", "order"]
)
def test_sdk_import_rejects_inconsistent_evidence(sdk_attempt, defect):
    from tau2.platform.langfuse_export import sdk_attempt_record

    path = sdk_attempt / (
        "evaluation.json" if defect in {"counter", "grade"} else "evaluation-input.json"
    )
    if defect in {"ticket", "model"}:
        path = sdk_attempt / "native-run.json"
    data = json.loads(path.read_text())
    if defect == "task":
        data["task_id"] = "Other task"
    elif defect == "ticket":
        data["request_contract"]["prompt"] = "Other ticket"
    elif defect == "model":
        data["request_contract"]["model"] = "Other model"
    elif defect == "counter":
        data["values"]["bank_calls"] = 2
    elif defect == "grade":
        data["values"]["DB"] = 0
    else:
        data["journal"].append(data["journal"][0])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        sdk_attempt_record(sdk_attempt)


def test_sdk_resolution_duration_uses_recorded_calls_not_import_time(sdk_attempt):
    from tau2.platform.langfuse_export import sdk_attempt_record

    rows = [
        {"b": "start", "name": "agent.turn", "t_ns": 2_000_000_000},
        {"b": "end", "t_ns": 7_000_000_000},
    ]
    (sdk_attempt / "calls.jsonl").write_text("\n".join(map(json.dumps, rows)))
    r, metadata, _ = sdk_attempt_record(sdk_attempt)
    assert r["metrics"]["resolution_seconds"] == 5
    assert "calls.jsonl" in metadata["source_sha256"]
    assert metadata["resolution_timing_source"].startswith("calls.jsonl")
