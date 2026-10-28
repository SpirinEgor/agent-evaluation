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
