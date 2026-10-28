"""Full-file source matching keeps received evidence and non-file guards intact."""

import pytest

from tau2.data_model.message import AssistantMessage, Tick, ToolCall, ToolMessage
from tau2.data_model.tasks import Action, EvaluationCriteria, Task, UserScenario
from tau2.evaluator.evaluator_action import ActionEvaluator, FullDuplexActionEvaluator


@pytest.mark.parametrize(
    "compare_args,arguments,want",
    [
        (None, {"path": "policies/cards.md"}, True),
        ([], {"path": "policies/cards.md"}, True),
        (["path"], {"path": "policies/other.md"}, False),
        (["path", "start_line"], {"path": "policies/cards.md", "start_line": 1}, True),
        (None, {}, False),
    ],
)
def test_file_read_matches_required_canonical_path(compare_args, arguments, want):
    action = Action(
        action_id="source",
        name="file_read",
        arguments={"path": "policies/cards.md"},
        compare_args=compare_args,
    )
    assert (
        action.compare_with_tool_call(
            ToolCall(id="read", name="file_read", arguments=arguments)
        )
        is want
    )


def test_file_read_keeps_declared_extra_constraints():
    action = Action(
        action_id="source",
        name="file_read",
        arguments={"path": "policies/cards.md"},
        argument_values={"revision": "current"},
    )
    assert not action.compare_with_tool_call(
        ToolCall(id="read", name="file_read", arguments={"path": "policies/cards.md"})
    )


def test_other_tools_keep_existing_argument_comparison():
    action = Action(action_id="write", name="set_limit", arguments={"amount": 500})
    assert not action.compare_with_tool_call(
        ToolCall(
            id="write", name="set_limit", arguments={"amount": 500, "channel": "cash"}
        )
    )
    assert action.model_copy(
        update={"compare_args": ["amount"]}
    ).compare_with_tool_call(
        ToolCall(
            id="write", name="set_limit", arguments={"amount": 500, "channel": "cash"}
        )
    )


@pytest.mark.parametrize(
    "mode,variant,want",
    [
        ("half", "received", 1.0),
        ("full", "received", 1.0),
        ("half", "optional_kwargs", 1.0),
        ("half", "wrong_revision", 0.0),
        ("half", "wrong_body", 0.0),
        ("half", "no_result", 0.0),
        ("half", "error", 0.0),
        ("half", "late_result", 0.0),
        ("full", "late_result", 0.0),
        ("half", "parallel", 0.0),
        ("full", "parallel", 0.0),
    ],
)
def test_file_read_source_requires_current_received_body_before_write(
    mode, variant, want
):
    task = Task(
        id="file-read-contract",
        user_scenario=UserScenario(instructions="Synthetic source dependency"),
        evaluation_criteria=EvaluationCriteria(
            reward_basis=["ACTION"],
            action_reward_scope="full_contract",
            require_successful_actions=True,
            actions=[
                Action(
                    action_id="source",
                    name="file_read",
                    arguments={"path": "policies/cards.md"},
                    result_contains=['"revision": "current"', "Applicable card rule"],
                ),
                Action(action_id="write", name="set_limit", arguments={"amount": 500}),
            ],
            action_order=[
                {
                    "before_action_id": "source",
                    "after_action_id": "write",
                    "require_result_before_target": True,
                }
            ],
        ),
    )
    args = {"path": "policies/cards.md"}
    if variant == "optional_kwargs":
        # Matcher compatibility does not authorize old range arguments in the API.
        args.update(start_line=1, end_line=200)
    elif variant == "different_path":
        args["path"] = "policies/other.md"
    elif variant == "missing_path":
        args = {}
    source = ToolCall(id="source", name="file_read", arguments=args)
    content = '{"revision": "current", "body": "Applicable card rule"}'
    if variant == "wrong_revision":
        content = '{"revision": "old", "body": "Applicable card rule"}'
    elif variant == "wrong_body":
        content = '{"revision": "current", "body": "Unrelated rule"}'
    source_result = ToolMessage(
        id="source", role="tool", content=content, error=variant == "error"
    )
    write = ToolCall(id="write", name="set_limit", arguments={"amount": 500})
    write_result = ToolMessage(id="write", role="tool", content="ok")
    if variant == "parallel":
        batches = [([source, write], [source_result, write_result])]
    elif variant == "late_result":
        batches = [([source], []), ([write], [write_result]), ([], [source_result])]
    else:
        batches = [
            ([source], [] if variant == "no_result" else [source_result]),
            ([write], [write_result]),
        ]
    if mode == "full":
        history = [
            Tick(
                tick_id=i,
                timestamp="2026-10-12T00:00:00",
                agent_tool_calls=calls,
                agent_tool_results=results,
            )
            for i, (calls, results) in enumerate(batches)
        ]
        reward = FullDuplexActionEvaluator.calculate_reward(task, history)
    else:
        history = []
        for calls, results in batches:
            history.append(AssistantMessage(role="assistant", tool_calls=calls))
            history.extend(results)
        reward = ActionEvaluator.calculate_reward(task, history)
    assert reward.reward == want


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "variant,want",
    [
        ("read_document", 1),
        ("cat", 1),
        ("file_read", 1),
        ("grep", 0),
        ("catalog", 0),
        ("wrong_revision", 0),
        ("wrong_hash", 0),
        ("wrong_body", 0),
        ("wrong_document", 0),
        ("error", 0),
        ("no_result", 0),
        ("late_result", 0),
        ("parallel", 0),
    ],
)
def test_equivalent_source_requires_verified_full_text_and_received_result(
    mode, variant, want
):
    import json

    from tau2.domains.banking_ru.environment import get_environment
    from tau2.platform.knowledge import KnowledgeMCP

    corpus = get_environment(task_id="Bank 2").tools.corpus
    path = "/knowledge/reward_product_scope.md"
    meta = corpus.documents[path]["metadata"]
    knowledge = KnowledgeMCP(corpus, token="source-test")
    payload = knowledge.read_document(meta["document_id"], meta["revision_id"])
    name = "read_document"
    args = {"document_id": meta["document_id"], "revision_id": meta["revision_id"]}
    if variant == "cat":
        name, args = "corpus_command", {"command": "cat " + path}
    elif variant == "file_read":
        name, args, payload = "file_read", {"path": path}, corpus.read(path)
    elif variant == "grep":
        # Even a spoofed full DTO in a grep response must not grant read credit.
        name, args = "corpus_command", {"command": "grep Договор /knowledge"}
    elif variant == "catalog":
        name, args = "list_documents", {}
    elif variant == "wrong_revision":
        args["revision_id"] = payload["revision_id"] = "old"
    elif variant == "wrong_hash":
        payload["content_sha256"] = "0" * 64
    elif variant == "wrong_body":
        payload["content"] += "\nChanged rule."
    elif variant == "wrong_document":
        args["document_id"] = "another-document"
    task = Task(
        id="knowledge-source-contract",
        user_scenario=UserScenario(instructions="Synthetic dependency"),
        evaluation_criteria=EvaluationCriteria(
            reward_basis=["DB", "ACTION"],
            business_process_only=True,
            banking_contract_version="db_action_v1",
            require_successful_actions=True,
            banking_source_read_contract="knowledge_v1",
            actions=[
                Action(action_id="source", name="file_read", arguments={"path": path}),
                Action(action_id="write", name="set_limit", arguments={"amount": 500}),
            ],
            action_order=[
                {
                    "before_action_id": "source",
                    "after_action_id": "write",
                    "require_result_before_target": True,
                }
            ],
            result_evidence=[
                {
                    "evidence_id": "source-body",
                    "tool_names": ["file_read"],
                    "record_id": meta["id"],
                    "expected_fields": {
                        "path": path,
                        "sha256": corpus.documents[path]["sha256"],
                        "complete": True,
                    },
                    "before_action_id": "write",
                }
            ],
        ),
    )
    source = ToolCall(id="read", name=name, arguments=args)
    result = ToolMessage(
        id="read", role="tool", content=json.dumps(payload), error=variant == "error"
    )
    write = ToolCall(id="write", name="set_limit", arguments={"amount": 500})
    written = ToolMessage(id="write", role="tool", content="ok")
    if variant == "parallel":
        batches = [([source, write], [result, written])]
    elif variant == "late_result":
        batches = [([source], []), ([write], [written]), ([], [result])]
    else:
        batches = [
            ([source], [] if variant == "no_result" else [result]),
            ([write], [written]),
        ]
    if mode == "full":
        trajectory = [
            Tick(
                tick_id=i,
                timestamp="2026-10-28T00:00:00Z",
                agent_tool_calls=c,
                agent_tool_results=r,
            )
            for i, (c, r) in enumerate(batches)
        ]
        evaluator = FullDuplexActionEvaluator
    else:
        trajectory = []
        for calls, results in batches:
            trajectory.append(AssistantMessage(role="assistant", tool_calls=calls))
            trajectory.extend(results)
        evaluator = ActionEvaluator
    before = [m.model_dump() for m in trajectory]
    reward = evaluator.calculate_reward(task, trajectory, source_corpus=corpus)
    assert reward.reward == want, reward.info
    assert [m.model_dump() for m in trajectory] == before
    if mode == "half" and variant == "read_document":
        with pytest.raises(ValueError, match="corpus snapshot"):
            evaluator.calculate_reward(task, trajectory)
    legacy = task.model_copy(deep=True)
    legacy.evaluation_criteria.banking_source_read_contract = None
    if variant in {"read_document", "cat"}:
        assert evaluator.calculate_reward(legacy, trajectory).reward == 0


def test_mixed_full_reads_preserve_compound_source_credit_without_mutating_corpus():
    import json
    from types import SimpleNamespace

    from tau2.domains.banking_ru.environment import get_environment
    from tau2.evaluator.source_reads import equivalent_source_reads
    from tau2.evaluator.trace_evidence import evidence_from_messages
    from tau2.platform.knowledge import KnowledgeMCP

    corpus = get_environment(task_id="Bank 41").tools.corpus
    k = KnowledgeMCP(corpus, token="compound-source")
    aggregate = "/knowledge/kb_final_41_part_1.md"
    sources = corpus.documents[aggregate]["metadata"]["source_paths"]
    history = []
    for i, path in enumerate(sources):
        meta = corpus.documents[path]["metadata"]
        if i % 3 == 0:
            name, args, payload = "file_read", {"path": path}, corpus.read(path)
        elif i % 3 == 1:
            name, args = "corpus_command", {"command": "cat " + path}
            payload = k.corpus_command(**args)
        else:
            name = "read_document"
            args = {
                "document_id": meta["document_id"],
                "revision_id": meta["revision_id"],
            }
            payload = k.read_document(**args)
        call = ToolCall(id=str(i), name=name, arguments=args)
        history.extend(
            [
                AssistantMessage(role="assistant", tool_calls=[call]),
                ToolMessage(id=call.id, role="tool", content=json.dumps(payload)),
            ]
        )
    before = set(corpus._read_paths)
    observed = evidence_from_messages(history).observed
    normalized = equivalent_source_reads(
        SimpleNamespace(banking_source_read_contract="knowledge_v1"), observed, corpus
    )
    last = json.loads(normalized[-1].result.content)
    assert any(doc["path"] == aggregate for doc in last["equivalent_documents"])
    assert all(
        "equivalent_documents" not in json.loads(item.result.content)
        for item in normalized[:-1]
    )
    assert corpus._read_paths == before
    meta = corpus.documents[aggregate]["metadata"]
    call = ToolCall(
        id="bundle",
        name="read_document",
        arguments={
            "document_id": meta["document_id"],
            "revision_id": meta["revision_id"],
        },
    )
    raw = evidence_from_messages(
        [
            AssistantMessage(role="assistant", tool_calls=[call]),
            ToolMessage(
                id=call.id,
                role="tool",
                content=json.dumps(k.read_document(**call.arguments)),
            ),
        ]
    ).observed
    bundle = equivalent_source_reads(
        SimpleNamespace(banking_source_read_contract="knowledge_v1"), raw, corpus
    )
    assert {
        doc["path"]
        for doc in json.loads(bundle[0].result.content)["included_documents"]
    } == set(sources)
