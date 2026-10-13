"""Bank57 addressed contract: cancel both mechanisms for one Ivi contract."""

import importlib.util
import json
import os
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.utils import BANKING_RU_DATA_DIR
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

OWNER = "orlova_m_4400"
BACKUP = "ap_4401"


def _payload(result):
    assert not result.is_error
    try:
        return json.loads(result.content[0].text)
    except json.JSONDecodeError:
        return result.content[0].text


def _session():
    task = deepcopy(next(t for t in get_tasks() if t.id == "Bank 57"))
    return PlatformSession(task, get_environment, max_calls=100, max_errors=50)


def _identity(session, method="code_word"):
    for path in ["/policies/general.md", "/policies/identity.md"]:
        _payload(session.call("file_read", {"path": path}))
    _payload(session.call("find_customer", {"phone": "89024400719"}))
    answer = _payload(
        session.call(
            "ask_client",
            {"customer_id": OWNER, "request": {"type": "identity", "method": method}},
        )
    )
    _payload(
        session.call(
            "verify_identity",
            {
                "customer_id": OWNER,
                "credential": answer["data"]["credential"],
                "answer_id": answer["answer_id"],
            },
        )
    )


def _finish_and_grade(session, variant, expected):
    _payload(session.call("done", {}))
    full = grade_full_session(session).reward_info
    half = session.grade()
    out = os.environ.get("BANK57_HARDENING_EVIDENCE")
    if out:
        target = Path(out)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"Bank57-{variant}.json").write_text(
            json.dumps(
                {
                    "task_id": session.task.id,
                    "variant": variant,
                    "expected": expected,
                    "half": half.model_dump(mode="json"),
                    "full": full.model_dump(mode="json"),
                    "messages": [
                        m.model_dump(mode="json") for m in session.trajectory()
                    ],
                    "mcp_events": session.mcp_events,
                    "errors": session.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    assert half.final_reward == full.final_reward == expected


CASES = [
    (name, 1)
    for name in [
        "both",
        "reverse",
        "birth_date",
        "repeat_read",
        "data_before_source",
        "selection",
        "unknown_retry",
    ]
] + [
    (name, 0)
    for name in [
        "noop",
        "only_subscription",
        "only_autopayment",
        "other_ivi",
        "kinopoisk",
        "mobile",
        "wrong_then_repair",
        "early_attempt_then_repair",
        "failed_other_then_repair",
        "source_after_attempt",
        "data_result_after_attempt",
        "source_result_after_attempt",
    ]
]


@pytest.mark.parametrize("variant,expected", CASES)
def test_bank57_full_grade(variant, expected):
    session = _session()
    if variant == "noop":
        _finish_and_grade(session, variant, expected)
        return
    if variant == "early_attempt_then_repair":
        assert session.call(
            "cancel_subscription", {"subscription_id": "sub_4401"}
        ).is_error
    if variant == "failed_other_then_repair":
        assert session.call(
            "cancel_subscription", {"subscription_id": "sub_4403"}
        ).is_error
    _identity(session, "birth_date" if variant == "birth_date" else "code_word")
    delayed_source = None
    if variant == "source_result_after_attempt":
        delayed_source = session.begin_call(
            "file_read", {"path": "/policies/subscriptions.md"}, None
        )
        _payload(
            session.call(
                "file_read",
                {"path": "/policies/subscriptions.md"},
                attempt=delayed_source,
            )
        )
    elif variant not in {"source_after_attempt", "data_before_source"}:
        _payload(session.call("file_read", {"path": "/policies/subscriptions.md"}))
    delayed_data = None
    if variant == "data_result_after_attempt":
        delayed_data = session.begin_call(
            "get_subscriptions", {"customer_id": OWNER}, None
        )
        _payload(
            session.call(
                "get_subscriptions", {"customer_id": OWNER}, attempt=delayed_data
            )
        )
    else:
        _payload(session.call("get_subscriptions", {"customer_id": OWNER}))
    if variant == "data_before_source":
        _payload(session.call("file_read", {"path": "/policies/subscriptions.md"}))
    if variant == "repeat_read":
        _payload(session.call("get_subscriptions", {"customer_id": OWNER}))
    if variant == "selection":
        answer = _payload(
            session.call(
                "ask_client",
                {
                    "customer_id": OWNER,
                    "request": {
                        "type": "subscription_selection",
                        "merchant_id": "merchant_ivi",
                    },
                },
            )
        )
        assert answer["data"]["contract_reference"] == "ivi-personal-4400"
    if variant == "unknown_retry":
        assert session.call(
            "cancel_subscription", {"subscription_id": "unknown-sub"}
        ).is_error
    wrong = {
        "other_ivi": "sub_4403",
        "kinopoisk": "sub_4402",
        "wrong_then_repair": "sub_4403",
    }
    if variant in wrong:
        _payload(
            session.call("cancel_subscription", {"subscription_id": wrong[variant]})
        )
    if variant == "mobile":
        _payload(session.call("cancel_autopayment", {"autopayment_id": "ap_4400"}))
    if variant == "only_autopayment":
        _payload(session.call("cancel_autopayment", {"autopayment_id": BACKUP}))
    elif variant == "reverse":
        _payload(session.call("cancel_autopayment", {"autopayment_id": BACKUP}))
        _payload(session.call("cancel_subscription", {"subscription_id": "sub_4401"}))
    else:
        _payload(session.call("cancel_subscription", {"subscription_id": "sub_4401"}))
        if variant == "source_after_attempt":
            _payload(session.call("file_read", {"path": "/policies/subscriptions.md"}))
        if delayed_source:
            session.result_sent(delayed_source)
        if delayed_data:
            session.result_sent(delayed_data)
        if variant != "only_subscription":
            _payload(session.call("cancel_autopayment", {"autopayment_id": BACKUP}))
    _finish_and_grade(session, variant, expected)


def _migration():
    path = Path(__file__).resolve().parents[3] / "scripts/banking_ru/harden_bank57.py"
    spec = importlib.util.spec_from_file_location("harden_bank57_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_scoped_idempotent(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(BANKING_RU_DATA_DIR, data)
    before = json.loads((data / "tasks.json").read_text())
    expected = _migration().build(data)
    for path, raw in expected.items():
        path.write_bytes(raw)
    after = json.loads((data / "tasks.json").read_text())
    assert [t for t in before if t["id"] != "Bank 57"] == [
        t for t in after if t["id"] != "Bank 57"
    ]
    assert expected == _migration().build(data)
    script_dir = Path(__file__).resolve().parents[3] / "scripts/banking_ru"
    monkeypatch.syspath_prepend(str(script_dir))
    from build_discovery_contract import with_contract

    discovered = with_contract(
        deepcopy(after), json.loads((data / "db.json").read_text())
    )
    assert discovered == after
    (data / "tasks.json").write_bytes(_migration()._bytes(discovered))
    assert expected == _migration().build(data)


@pytest.mark.parametrize("fault", ["missing", "duplicate"])
def test_migration_rejects_ambiguous_id(tmp_path, fault):
    data = tmp_path / "data"
    shutil.copytree(BANKING_RU_DATA_DIR, data)
    tasks = json.loads((data / "tasks.json").read_text())
    selected = next(t for t in tasks if t["id"] == "Bank 57")
    if fault == "missing":
        tasks.remove(selected)
    else:
        tasks.append(deepcopy(selected))
    (data / "tasks.json").write_text(json.dumps(tasks))
    with pytest.raises(ValueError, match="exactly one Bank 57"):
        _migration().build(data)


def test_canonical_receipt_refresh_preserves_hardened_subscription_revision(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(BANKING_RU_DATA_DIR, data)
    for path, raw in _migration().build(data).items():
        path.write_bytes(raw)
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts/banking_ru/build_subscription_regulation_contract.py"
    )
    spec = importlib.util.spec_from_file_location("subscription_refresh_bank57", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = json.loads((data / "manifest.json").read_text())["documents"][
        "/policies/subscriptions.md"
    ]
    refreshed = json.loads(module.build(data)[data / "manifest.json"])["documents"][
        "/policies/subscriptions.md"
    ]
    assert refreshed == before
    assert refreshed["metadata"]["version"] == "3.1"
    assert refreshed["metadata"]["revision_date"] == "2026-10-13"
