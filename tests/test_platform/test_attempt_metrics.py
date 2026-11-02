from tau2.platform.attempt_metrics import model_metrics, tool_metrics


def test_format_business_recovery_repeats_and_mutations_are_separate():
    def event(name, args=None, **kw):
        return dict(canonical_name=name, arguments=args or {}, response="same", **kw)

    rows = [
        event("bank_directory", budget="discovery"),
        event("get", error=True, diagnostic_code="INVALID_ARGUMENT"),
        event("get"),
        event("get"),
        event(
            "pay",
            error=True,
            response_unused=True,
            diagnostic_code="ERR_NOT_AUTHORIZED",
        ),
        event("get"),
        event("pay"),
    ]
    m = tool_metrics(rows, read_tools={"get"}, mutating_tools={"pay"})
    assert m["calls"] == dict(total=7, discovery=1, other=6, state_mutating_attempts=2)
    assert m["format_accuracy"]["rate"] == 6 / 7
    assert m["errors"]["by_category"] == {"format": 1, "business_rejection": 1}
    assert m["recovery"]["recovered"] == 2
    assert m["repeated_reads"] == 1


def test_empty_unknown_usage_and_subscription_cost_are_not_zero():
    assert model_metrics({})["tokens"] is None
    assert model_metrics({})["model_requests"] is None
    assert model_metrics({})["cost"]["amount"] is None
    assert (
        tool_metrics([], read_tools=set(), mutating_tools=set())["format_accuracy"][
            "rate"
        ]
        is None
    )


def test_model_accounting_counts_requests_and_requires_complete_usage():
    usage = dict(input_tokens=100, output_tokens=10, total_tokens=110)
    e = {"event": "response_completed", "usage": usage}
    run = {"broker_evidence": {"upstream_requests": 2, "events": [e, e]}}
    assert model_metrics(run)["tokens"]["total_tokens"] == 220
    run["broker_evidence"]["events"].append({"event": "response_completed"})
    assert model_metrics(run)["tokens"] is None


def test_invalid_knowledge_command_is_not_a_schema_type_error():
    m = tool_metrics(
        [
            dict(
                canonical_name="terminal",
                error=True,
                diagnostic_code="INVALID_COMMAND",
                response="bad command",
            )
        ],
        read_tools=set(),
        mutating_tools=set(),
    )
    assert m["errors"]["by_category"] == {"command": 1}
    assert m["format_accuracy"]["rate"] == 1.0
