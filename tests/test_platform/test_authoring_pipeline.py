"""Authoring-only durability and provider boundaries; no live model calls."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = importlib.util.spec_from_file_location(
    "authoring_pipeline", ROOT / "scripts/banking_ru/authoring_pipeline.py"
)
p = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(p)
SPEC = json.loads((ROOT / "tests/fixtures/authoring/neutral.json").read_text())
CANDIDATE = {
    "status": "completed",
    "slots": [
        {
            "id": "notice",
            "text": "Офис «Пример» работает по будням с 09:00 до 18:00. В субботу и воскресенье офис закрыт.",
        }
    ],
    "explanation": "Все факты сохранены.",
}
REVIEW = {
    "verdict": "approve",
    "findings": [],
    "checks": [
        {"requirement": key, "passed": True, "evidence": value}
        for key, value in p.requirements(SPEC).items()
    ],
    "explanation": "Соответствует заданию.",
}


def stub(calls, candidate=None, review=None):
    def execute(stage, model, prompt, attempt, timeout):
        calls.append(stage)
        p.write_json(
            attempt / "request.json",
            {
                "stage": stage,
                "model": model,
                "prompt_sha256": p.hashlib.sha256(prompt.encode()).hexdigest(),
            },
        )
        events = [
            {
                "event": "init",
                "conversation_id": stage,
                "init": {
                    "model": model,
                    "tools": ["finish"],
                    "permission_mode": "strict",
                },
            },
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "conversation_id": stage,
                    "structured_output": (candidate or CANDIDATE)
                    if stage == "generate"
                    else (review or REVIEW),
                },
            },
        ]
        (attempt / "guard.jsonl").write_text(
            json.dumps(
                {
                    "phase": "start",
                    "conversation_id": stage,
                    "tool": None,
                }
            )
            + "\n"
        )
        (attempt / "stdout.jsonl").write_text("\n".join(json.dumps(e) for e in events))
        (attempt / "stderr.log").write_text("")
        p.write_json(
            attempt / "receipt.json",
            {
                "status": "exited",
                "returncode": 0,
                "files": {
                    name: p.digest(attempt / name)
                    for name in (
                        "request.json",
                        "stdout.jsonl",
                        "stderr.log",
                        "guard.jsonl",
                    )
                },
            },
        )

    return execute


@pytest.mark.parametrize("stage", p.STAGES)
def test_resume_after_durable_response_does_not_repeat_model(stage, tmp_path):
    calls = []
    with pytest.raises(RuntimeError, match="Injected failure"):
        p.pipeline(
            SPEC, tmp_path, live=True, fail_after_receipt=stage, executor=stub(calls)
        )
    state = p.pipeline(SPEC, tmp_path, live=True, executor=stub(calls))
    assert calls == ["generate", "review"]
    assert state["status"] == "reviewed"
    assert state["banking_validation"] == state["target_agent_run"] == "not_run"
    assert p.pipeline(SPEC, tmp_path, live=False, executor=stub(calls)) == state
    assert calls == ["generate", "review"]


def test_no_live_flag_never_calls_models(tmp_path):
    calls = []
    with pytest.raises(ValueError, match="--live"):
        p.pipeline(SPEC, tmp_path, executor=stub(calls))
    assert calls == []


def test_interrupted_unreceipted_request_needs_explicit_retry(tmp_path):
    calls = []

    def interrupted(stage, model, prompt, attempt, timeout):
        (attempt / "stdout.jsonl").write_text('{"partial":true}\n')
        raise RuntimeError("Simulated process death")

    with pytest.raises(RuntimeError):
        p.pipeline(SPEC, tmp_path, live=True, executor=interrupted)
    with pytest.raises(ValueError, match="unknown outcome"):
        p.pipeline(SPEC, tmp_path, live=True, executor=stub(calls))
    assert calls == []
    state = p.pipeline(
        SPEC, tmp_path, live=True, retry_incomplete=True, executor=stub(calls)
    )
    assert state["status"] == "reviewed"
    assert (
        tmp_path / "generate-attempt-01/stdout.jsonl"
    ).read_text() == '{"partial":true}\n'
    assert (tmp_path / "generate-attempt-02/receipt.json").exists()


def test_changed_spec_and_tampered_raw_output_fail_before_call(tmp_path):
    calls = []
    p.pipeline(SPEC, tmp_path, live=True, executor=stub(calls))
    changed = {**SPEC, "objective": "Something else"}
    with pytest.raises(ValueError, match="Inputs/model/engine"):
        p.pipeline(changed, tmp_path, live=True, executor=stub(calls))
    (tmp_path / "generate-attempt-01/stdout.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        p.pipeline(SPEC, tmp_path, live=True, executor=stub(calls))
    assert calls == ["generate", "review"]


def test_refusal_is_terminal_not_rerouted(tmp_path):
    calls = []
    refused = {"status": "refused", "slots": [], "explanation": "Cannot comply."}
    state = p.pipeline(
        SPEC, tmp_path, live=True, executor=stub(calls, candidate=refused)
    )
    assert state["status"] == "author_refused"
    p.pipeline(SPEC, tmp_path, live=True, executor=stub(calls))
    assert calls == ["generate"]


def test_rejection_preserves_candidate_and_is_not_success(tmp_path):
    review = {**REVIEW, "verdict": "reject", "findings": ["Unsupported claim."]}
    state = p.pipeline(SPEC, tmp_path, live=True, executor=stub([], review=review))
    assert state["status"] == "rejected"
    assert p.read_json(tmp_path / "candidate.json") == CANDIDATE


def test_contradictory_review_and_extra_slots_are_invalid():
    with pytest.raises(ValueError, match="Approval contradicts"):
        p.validate_output("review", {**REVIEW, "findings": ["Problem"]}, SPEC)
    extra = {
        **CANDIDATE,
        "slots": CANDIDATE["slots"] + [{"id": "secret", "text": "extra"}],
    }
    with pytest.raises(ValueError, match="exactly"):
        p.validate_output("generate", extra, SPEC)
    with pytest.raises(ValueError, match="every requirement"):
        p.validate_output("review", {**REVIEW, "checks": REVIEW["checks"][:1]}, SPEC)


def test_changed_completed_candidate_is_not_silently_overwritten(tmp_path):
    calls = []
    p.pipeline(SPEC, tmp_path, live=True, executor=stub(calls))
    (tmp_path / "candidate.json").write_text("{}")
    with pytest.raises(ValueError, match="Completed output integrity"):
        p.pipeline(SPEC, tmp_path, live=True, executor=stub(calls))
    assert calls == ["generate", "review"]


def test_timeout_retry_is_explicit_and_keeps_failed_attempt(tmp_path):
    def limited(stage, model, prompt, attempt, timeout):
        stub([])(stage, model, prompt, attempt, timeout)
        (attempt / "stdout.jsonl").write_text('{"event":"partial"}')
        receipt = p.read_json(attempt / "receipt.json")
        receipt["returncode"] = 1
        receipt["status"] = "timeout"
        receipt["files"]["stdout.jsonl"] = p.digest(attempt / "stdout.jsonl")
        p.write_json(attempt / "receipt.json", receipt)

    with pytest.raises(ValueError, match="successfully"):
        p.pipeline(SPEC, tmp_path, live=True, executor=limited)
    calls = []
    with pytest.raises(ValueError, match="successfully"):
        p.pipeline(SPEC, tmp_path, live=True, executor=stub(calls))
    assert not calls
    assert (
        p.pipeline(
            SPEC, tmp_path, live=True, retry_incomplete=True, executor=stub(calls)
        )["status"]
        == "reviewed"
    )
    assert p.read_json(tmp_path / "generate-attempt-01/receipt.json")["returncode"] == 1


def test_agy_unconfirmed_permissions_and_wrong_model_fail_closed(tmp_path):
    path = tmp_path / "events.jsonl"
    for config in (
        {"model": p.REVIEWER, "tools": ["run_command"]},
        {"model": "gpt", "tools": []},
    ):
        path.write_text(json.dumps({"event": "init", "init": config}))
        with pytest.raises(ValueError):
            p.parse_events("review", path)


def test_concurrent_run_cannot_acquire_lock(tmp_path):
    with p.run_lock(tmp_path):
        with pytest.raises(ValueError, match="locked"):
            with p.run_lock(tmp_path):
                pytest.fail("Lock should not be acquired")


def test_timeout_retains_partial_output_and_kills_process(tmp_path, monkeypatch):
    import sys

    def command(*args):
        return [
            sys.executable,
            "-c",
            "import time; print('partial', flush=True); time.sleep(20)",
        ], {}

    monkeypatch.setattr(p, "provider_command", command)
    p.execute("generate", p.WRITERS[0], "test", tmp_path, 0.2)
    receipt = p.read_json(tmp_path / "receipt.json")
    assert receipt["status"] == "timeout"
    assert "partial" in (tmp_path / "stdout.jsonl").read_text()
    with pytest.raises(ValueError, match="timeout"):
        p.verify_receipt(tmp_path)


def test_tool_call_and_missing_guard_are_rejected(tmp_path):
    stub([])("review", p.REVIEWER, "prompt", tmp_path, 10)
    (tmp_path / "guard.jsonl").write_text("")
    with pytest.raises(ValueError, match="tool-guard"):
        p.parse_events("review", tmp_path / "stdout.jsonl")
    stub([])("review", p.REVIEWER, "prompt", tmp_path, 10)
    with (tmp_path / "guard.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {"phase": "tool", "conversation_id": "review", "tool": "run_command"}
            )
            + "\n"
        )
    with pytest.raises(ValueError, match="forbidden tool"):
        p.parse_events("review", tmp_path / "stdout.jsonl")


def test_hook_denies_unknown_tools_and_allows_only_finish(tmp_path):
    import subprocess
    import sys

    guard = tmp_path / "guard.py"
    guard.write_text(p.GUARD_SOURCE)
    for name in ("run_command", "send_message", "new_unknown_tool", "finish"):
        result = subprocess.run(
            [sys.executable, str(guard), "tool", str(tmp_path / "audit.jsonl")],
            input=json.dumps({"conversationId": "test", "toolCall": {"name": name}}),
            text=True,
            capture_output=True,
            check=True,
        )
        assert json.loads(result.stdout)["decision"] == (
            "allow" if name == "finish" else "deny"
        )


def test_timeout_stops_child_even_when_leader_exits(tmp_path, monkeypatch):
    import os
    import signal
    import sys
    import time

    child_source = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"
    source = (
        "import subprocess,sys,time,pathlib; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_source!r}]); "
        f"pathlib.Path({str(tmp_path / 'child.pid')!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )
    monkeypatch.setattr(
        p, "provider_command", lambda *a: ([sys.executable, "-c", source], {})
    )
    p.execute("generate", p.WRITERS[0], "test", tmp_path, 0.5)
    pid = int((tmp_path / "child.pid").read_text())
    try:
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("Child survived timeout cleanup")
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_real_cli_subprocesses_recover_at_both_commit_boundaries(tmp_path, monkeypatch):
    """Real entrypoint + child processes, fake provider only. Never reaches a model."""
    import os
    import subprocess
    import sys

    cli = tmp_path / "agy"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, shlex, subprocess, sys\n"
        "args=sys.argv[1:]\n"
        "model=args[args.index('--model')+1]\n"
        "schema=json.loads(args[args.index('--json-schema')+1])\n"
        "stage='generate' if 'slots' in schema['properties'] else 'review'\n"
        "assert '--continue' not in args and '--conversation' not in args\n"
        "root=pathlib.Path(args[args.index('--add-dir')+1])\n"
        "hooks=json.loads((root/'.agents/hooks.json').read_text())\n"
        "cmd=hooks['authoring-tool-boundary']['SessionStart'][0]['command']\n"
        "subprocess.run(shlex.split(cmd), input=json.dumps({'conversationId':stage}),text=True,check=True,capture_output=True)\n"
        "with open(os.environ['AUTHORING_STUB_CALLS'],'a') as f:f.write(stage+'\\n')\n"
        f"result={repr({'generate': CANDIDATE, 'review': REVIEW})}[stage]\n"
        "print(json.dumps({'event':'init','conversation_id':stage,'init':{'model':model,'permission_mode':'strict','tools':['finish']}}))\n"
        "print(json.dumps({'event':'result','result':{'status':'SUCCESS','conversation_id':stage,'structured_output':result}}))\n"
    )
    cli.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    count = tmp_path / "calls.txt"
    monkeypatch.setenv("AUTHORING_STUB_CALLS", str(count))
    monkeypatch.setenv("BANKING_AUTHORING_PRIVATE_ROOT", str(tmp_path / "private"))
    output = tmp_path / "run"
    base = [
        sys.executable,
        str(ROOT / "scripts/banking_ru/authoring_pipeline.py"),
        "--spec",
        str(ROOT / "tests/fixtures/authoring/neutral.json"),
        "--output",
        str(output),
    ]
    for stage in p.STAGES:
        result = subprocess.run(
            base + ["--live", "--fail-after-receipt", stage],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2, result.stderr
    result = subprocess.run(base, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "reviewed"
    assert CANDIDATE["slots"][0]["text"] not in result.stdout
    assert not (output / "candidate.json").exists()
    assert not (output / "review.json").exists()
    assert {x.name for x in output.iterdir()} == {
        "status.json",
        ".lock",
        *[x.name for x in output.glob("failure-*.json")],
    }
    assert CANDIDATE["slots"][0]["text"] not in (output / "status.json").read_text()
    assert count.read_text().splitlines() == ["generate", "review"]


def test_sigterm_to_coordinator_saves_interrupted_receipt(tmp_path):
    import signal
    import subprocess
    import sys
    import time

    script = (
        "import importlib.util,sys; "
        f"spec=importlib.util.spec_from_file_location('p',{str(ROOT / 'scripts/banking_ru/authoring_pipeline.py')!r}); "
        "p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p); "
        "p.provider_command=lambda *a:([sys.executable,'-c',\"import time; print('ready',flush=True); time.sleep(30)\"],{}); "
        f"p.execute('generate',p.WRITERS[0],'test',p.Path({str(tmp_path)!r}),30)"
    )
    process = subprocess.Popen([sys.executable, "-c", script])
    try:
        for _ in range(200):
            output = tmp_path / "stdout.jsonl"
            if output.exists() and "ready" in output.read_text():
                break
            time.sleep(0.01)
        else:
            pytest.fail("Stub did not start")
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0
        assert p.read_json(tmp_path / "receipt.json")["status"] == "interrupted"
        assert "ready" in output.read_text()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_public_job_never_publishes_model_text_and_lock_cannot_overwrite_status(
    tmp_path,
):
    output, private = tmp_path / "public", tmp_path / "private"
    calls = []
    state = p.public_job(SPEC, output, private, live=True, executor=stub(calls))
    assert state["status"] == "reviewed"
    saved = (output / "status.json").read_bytes()
    assert CANDIDATE["slots"][0]["text"].encode() not in saved
    assert REVIEW["explanation"].encode() not in saved
    with p.run_lock(output):
        with pytest.raises(ValueError, match="locked"):
            p.public_job(SPEC, output, private, live=True, executor=stub(calls))
    assert (output / "status.json").read_bytes() == saved
    assert calls == ["generate", "review"]
