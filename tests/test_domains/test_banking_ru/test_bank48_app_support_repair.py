"""Bank48: public consultation and optional authenticated app support."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment
from tau2.platform.session import PlatformSession
from tests.test_domains.test_banking_ru.helpers import task
from tests.utils.banking_full_grade import grade_full_session

OWNER = "egorov_m_1145"
ARGS = {"customer_id": OWNER, "request": {"type": "app_access_support"}}


@pytest.mark.parametrize("mode", ["HALF"])
@pytest.mark.parametrize(
    "route,expected", [("public", 1), ("support", 1), ("own_card_id", 0)]
)
def test_bank48_routes(route, expected, mode):
    s = PlatformSession(task(48), get_environment, max_calls=250, max_errors=100)
    actions = deepcopy(task(48).evaluation_criteria.actions)
    if route == "noop":
        actions = []
    if route == "public":
        actions = [
            a
            for a in actions
            if a.name == "file_read" and a.arguments["path"] != "/policies/identity.md"
        ]
    if route == "missing_identity":
        actions = [a for a in actions if a.name != "verify_identity"]
    if route == "early_identity":
        s.call("escalate_to_human", ARGS)
    if route == "own_pre_auth":
        s.call("get_cards", {"customer_id": OWNER})
    answer = None
    for a in actions:
        args = deepcopy(a.arguments)
        if (
            route == "early_source"
            and a.name == "file_read"
            and args["path"] == "/policies/security_escalation.md"
        ):
            continue
        if route == "birth_date" and a.name == "ask_client":
            args["request"]["method"] = "birth_date"
        if route == "birth_date" and a.name == "verify_identity":
            assert answer is not None
            args.update(
                credential=answer["data"]["credential"], answer_id=answer["answer_id"]
            )
        if route == "unreceived_secret" and a.name == "ask_client":
            continue
        s.call(a.name, args)
        if a.name == "ask_client" and not s.log[-1][1].error:
            answer = json.loads(s.log[-1][1].content)
    if route in (
        "support",
        "support_unit",
        "birth_date",
        "missing_identity",
        "early_source",
        "early_identity",
        "wrong_type",
        "wrong_unit",
        "own_card_id",
        "foreign_card_id",
        "unknown_card_id",
        "duplicate",
        "legacy_reason",
        "missing_request",
        "malformed_request",
        "wrong_type_retry",
        "foreign_customer",
    ):
        args = deepcopy(ARGS)
        if route == "support_unit":
            args["responsible_unit"] = "support"
        if route == "missing_request":
            args.pop("request")
        if route == "malformed_request":
            args["request"] = {}
        if route == "foreign_customer":
            args["customer_id"] = "orlov_p_8814"
        if route in ("wrong_type", "wrong_type_retry"):
            args["request"]["type"] = "access_protection"
        if route == "wrong_unit":
            args["responsible_unit"] = "investment_products"
        if route.endswith("card_id"):
            args["card_id"] = {
                "own_card_id": "card_9902",
                "foreign_card_id": "card_1145",
                "unknown_card_id": "card_unknown",
            }[route]
        if route == "legacy_reason":
            args["reason"] = "Приложение не работает"
        s.call("escalate_to_human", args)
        if route == "wrong_type_retry":
            s.call("escalate_to_human", ARGS)
        if route == "duplicate":
            s.call("escalate_to_human", args)
        if route == "early_source":
            s.call("file_read", {"path": "/policies/security_escalation.md"})
    if route == "block_card":
        s.call("block_card", {"card_id": "card_9902"})
    if route == "reveal":
        s.call("reveal_card_details", {"card_id": "card_9902"})
    if route == "foreign_read":
        s.call("get_cards", {"customer_id": "orlov_p_8814"})
    if route == "leak":
        s.call(
            "ask_client",
            {
                "customer_id": OWNER,
                "request": {"type": "identity", "method": "code_word"},
                "question": "CVV 123",
            },
        )
    s.call("done", {})
    reward = s.grade() if mode == "HALF" else grade_full_session(s).reward_info
    assert reward.final_reward == expected


def test_bank48_composes_with_common_privacy_and_discovery(monkeypatch):
    import importlib.util

    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "scripts/banking_ru"))
    modules = []
    for name in [
        "build_bank48_app_support_contract",
        "build_confidentiality_contract",
        "build_discovery_contract",
    ]:
        spec = importlib.util.spec_from_file_location(
            name, root / "scripts/banking_ru" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    tasks = json.loads((root / "data/tau2/domains/banking_ru/tasks.json").read_text())
    db = json.loads((root / "data/tau2/domains/banking_ru/db.json").read_text())
    result = modules[0].with_contract(tasks)
    modules[1].with_contract(result, db)
    modules[2].with_contract(result, db)
    assert modules[0].with_contract(result) == result
    assert modules[1].with_contract(deepcopy(result), db) == result
    assert modules[2].with_contract(deepcopy(result), db) == result
