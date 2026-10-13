"""Bank47 permits scoped protection referrals, never premature recovery."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.helpers import task
from tests.utils.banking_full_grade import grade_full_session

ARGS = {"customer_id": "titova_a_7729", "request": {"type": "access_protection"}}
CASES = [
    ("reference", 1),
    ("reference_repeat", 1),
    ("noop", 0),
    ("protection", 1),
    ("explicit_unit", 1),
    ("before_case", 1),
    ("wrong_type", 0),
    ("wrong_type_retry", 0),
    ("wrong_unit", 0),
    ("missing_request", 0),
    ("malformed_request", 0),
    ("string_request", 0),
    ("legacy_reason", 0),
    ("empty_case_id", 0),
    ("empty_transaction_id", 0),
    ("empty_card_id", 0),
    ("empty_loan_id", 0),
    ("duplicate", 0),
    ("foreign_owner", 0),
    ("foreign_read", 0),
    ("early_identity", 0),
    ("early_devices", 0),
    ("early_recognition", 0),
    ("early_source", 0),
    ("missing_identity", 0),
    ("missing_case", 0),
    ("unblock_unknown", 0),
    ("unblock_pending", 0),
]


@pytest.mark.parametrize("route,expected", CASES, ids=[r for r, _ in CASES])
def test_bank47_routes(route, expected):
    s = PlatformSession(task(47), get_environment, max_calls=250, max_errors=100)
    actions = deepcopy(task(47).evaluation_criteria.actions)
    if route == "noop":
        actions = []
    if route == "missing_identity":
        actions = [a for a in actions if a.name != "verify_identity"]
    if route == "missing_case":
        actions = [a for a in actions if a.name != "create_case"]
    if route == "early_identity":
        s.call("escalate_to_human", deepcopy(ARGS))
    deferred = []
    for a in actions:
        if (
            route == "early_source"
            and a.name == "file_read"
            and a.arguments["path"] == "/knowledge/kb_184.md"
        ):
            deferred.append(a)
            continue
        if (
            (route == "early_devices" and a.name == "get_devices")
            or (
                route == "early_recognition"
                and a.name == "ask_client"
                and a.arguments["request"]["type"] == "device_recognition"
            )
            or (route == "before_case" and a.name == "create_case")
        ):
            s.call("escalate_to_human", deepcopy(ARGS))
        s.call(a.name, deepcopy(a.arguments))
    if route not in {
        "reference",
        "reference_repeat",
        "noop",
        "before_case",
        "foreign_read",
        "unblock_unknown",
        "unblock_pending",
    }:
        args = deepcopy(ARGS)
        if route == "explicit_unit":
            args["responsible_unit"] = "support"
        if route in {"wrong_type", "wrong_type_retry"}:
            args["request"]["type"] = "app_access_support"
        if route == "wrong_unit":
            args["responsible_unit"] = "investment_products"
        if route == "missing_request":
            args.pop("request")
        if route == "string_request":
            args["request"] = '{"type": "access_protection"}'
        if route == "malformed_request":
            args["request"] = {}
        if route.startswith("empty_"):
            args[route.removeprefix("empty_")] = ""
        if route == "legacy_reason":
            args["reason"] = "Защита доступа"
        if route == "foreign_owner":
            args["customer_id"] = "egorov_m_1145"
        s.call("escalate_to_human", args)
        if route in {"duplicate", "wrong_type_retry"}:
            s.call("escalate_to_human", deepcopy(ARGS))
    for a in deferred:
        s.call(a.name, deepcopy(a.arguments))
    if route == "foreign_read":
        s.call("get_devices", {"customer_id": "volkova_s_6674"})
    if route.startswith("unblock_"):
        s.call(
            "unblock_device",
            {"device_id": "dev_8822" if route == "unblock_unknown" else "dev_8821"},
        )
    s.call("done", {})
    half = s.grade()
    full = grade_full_session(s).reward_info
    if folder := os.environ.get("BANK47_EVIDENCE_DIR"):
        dest = Path(folder)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{route}.json").write_text(
            json.dumps(
                {
                    "half": half.model_dump(mode="json"),
                    "full": full.model_dump(mode="json"),
                    "db": s.env.tools.db.model_dump(mode="json"),
                    "calls": [
                        {
                            "call": c.model_dump(mode="json"),
                            "result": r.model_dump(mode="json"),
                        }
                        for c, r in s.log
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    assert half.final_reward == full.final_reward == expected


def test_bank47_generator_is_scoped_and_composes(tmp_path):
    import importlib.util

    root = Path(__file__).resolve().parents[3]
    modules = []
    for name in (
        "build_bank47_access_protection_contract",
        "build_escalation_contract",
    ):
        spec = importlib.util.spec_from_file_location(
            name, root / "scripts/banking_ru" / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    tasks = json.loads((root / "data/tau2/domains/banking_ru/tasks.json").read_text())
    before = deepcopy(tasks)
    updated = modules[0].with_contract(tasks)
    assert tasks == before
    assert [t for t in updated if t["id"] != "Bank 47"] == [
        t for t in before if t["id"] != "Bank 47"
    ]
    assert modules[0].with_contract(updated) == updated
    assert modules[1].with_contract(updated) == updated
    db = json.loads((root / "data/tau2/domains/banking_ru/db.json").read_text())
    for name in ("build_confidentiality_contract", "build_discovery_contract"):
        spec = importlib.util.spec_from_file_location(
            name, root / "scripts/banking_ru" / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        import sys

        sys.path.insert(0, str(root / "scripts/banking_ru"))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        assert module.with_contract(deepcopy(updated), db) == updated

    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(updated, ensure_ascii=False))
    # Test actual generator application only to temporary data.
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            str(root / "scripts/banking_ru/build_bank47_access_protection_contract.py"),
            "--data-dir",
            str(tmp_path),
            "--apply",
        ],
        check=True,
        capture_output=True,
    )
    assert json.loads(path.read_text()) == updated


def test_bank47_invalid_db_insertion_is_rejected():
    from pydantic import ValidationError

    from tau2.data_model.tasks import Task

    raw = task(47).model_dump(mode="json")
    raw["evaluation_criteria"]["db_alternative_action_additions"][1][0][
        "db_reference_before_action_id"
    ] = "missing-reference"
    with pytest.raises(ValidationError, match="insertion must name a reference action"):
        Task.model_validate(raw)
