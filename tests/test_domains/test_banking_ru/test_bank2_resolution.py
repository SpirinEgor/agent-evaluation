"""Bank2 resolution: real API, literal monetary outcomes, whole-grader controls."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru import environment, file_pilot, utils
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session
from tests.utils.banking_parent_fixture import copy_historical_data

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/banking_ru"))
C, A, B, E = "gromov_a_1187", "txn_804219", "txn_804227", "txn_804205"
CASE = "case_610903"


def test_resolution_api_exists():
    # Missing execution would leave the client unable to select a bank offer.
    env = environment.get_environment(task_id="Bank 2")
    assert hasattr(env.tools, "execute_reward_resolution")


@pytest.fixture
def scenario(tmp_path, monkeypatch, request):
    from upgrade_bank2_resolution import migrate

    p = tmp_path / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    from tests.utils.banking_parent_fixture import restore_bank2_v5

    restore_bank2_v5(p)
    migrate(p)
    kind = getattr(request, "param", "main")
    if kind != "main":
        from build_access_contract import with_contract
        from upgrade_bank2_resolution import configure

        raw = json.loads((p / "db.json").read_text())
        if kind == "tie":
            raw["reward_resolution_offers"]["offer_610719"]["amount"] = 96
        if kind == "approved26":
            raw["reward_resolution_offers"]["offer_610729"]["status"] = "approved"
        if kind == "no_late":
            raw["cashback_settlements"]["july-additional-processing"]["credits"] = []
        old = json.loads(
            (
                ROOT
                / "tests/fixtures/banking_ru/bank2_multisource_v4_2026_10_15/parent.json"
            ).read_text()
        )["task"]
        if kind == "no_late":
            old["evaluation_criteria"]["actions"] = [
                a
                for a in old["evaluation_criteria"]["actions"]
                if a["arguments"].get("transaction_id") != "txn_804287"
            ]
            old["evaluation_criteria"]["result_evidence"] = [
                x
                for x in old["evaluation_criteria"]["result_evidence"]
                if x["record_id"] != "txn_804287"
            ]
        configure(old, raw, p)
        tasks = [
            old if t["id"] == "Bank 2" else t
            for t in json.loads((p / "tasks.json").read_text())
        ]
        with_contract(tasks, raw)
        for name, value in [("tasks.json", tasks), ("db.json", raw)]:
            (p / name).write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            )
    from upgrade_bank2_multisource import sync_manifest

    sync_manifest(p)
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    return next(t for t in environment.get_tasks() if t.id == "Bank 2")


def data(r):
    assert not r.is_error, r
    try:
        return json.loads(r.content[0].text)
    except json.JSONDecodeError:
        return r.content[0].text


def run_reference(task, route="reference"):
    s = PlatformSession(task, environment.get_environment, max_calls=60, max_errors=5)
    if route != "noop":
        for a in task.evaluation_criteria.actions:
            if a.name == "done":
                continue
            args = deepcopy(a.arguments)
            if (
                a.name == "ask_client"
                and args.get("request", {}).get("type")
                == "reward_resolution_confirmation"
            ):
                if route == "tie_alternative":
                    args["request"]["offer_id"] = "offer_610723"
                if route in ("suboptimal", "pending", "wrong_total", "waiver"):
                    r = args["request"]
                    r["offer_id"] = {
                        "suboptimal": "offer_610719",
                        "pending": "offer_610729",
                        "wrong_total": "offer_610719",
                        "waiver": "offer_610711",
                    }[route]
                    total = {
                        "suboptimal": 23,
                        "pending": 26,
                        "wrong_total": 95,
                        "waiver": 28,
                    }[route]
                    r["total_amount"] = total
                    r["payouts"][1]["amount"] = total - 11
                    r["resulting_claims"][1]["amount"] = 53 - (total - 11)
                    if route == "wrong_total":
                        r["resulting_claims"][1]["amount"] = 1
                answer = data(s.call(a.name, args))
                consent = answer.get("confirmation_id", "not-confirmed")
                packet = args["request"]
                continue
            if a.name == "execute_reward_resolution":
                args.update(
                    {
                        k: deepcopy(v)
                        for k, v in packet.items()
                        if k in ("offer_id", "offer_version", "payouts", "total_amount")
                    }
                )
                args["confirmation_id"] = consent
                if route == "missing_consent":
                    args["confirmation_id"] = "invented"
                if route == "changed_packet":
                    args["payouts"][1]["amount"] = 12
                out = s.call(a.name, args)
                if route in (
                    "reference",
                    "repeat",
                    "suboptimal",
                    "early_read",
                    "double",
                    "stack",
                    "tie_alternative",
                ):
                    data(out)
                else:
                    assert out.is_error or data(out).get("status") != "succeeded"
                    return s
                if route == "repeat":
                    data(s.call(a.name, args))
                if route == "double":
                    assert s.call(
                        a.name, {**args, "idempotency_key": "another"}
                    ).is_error
                if route == "stack":
                    assert s.call(
                        "execute_cashback_adjustment",
                        dict(
                            permission_id="cap_620184",
                            transaction_id=A,
                            amount=1,
                            expected_revision=3,
                            idempotency_key="stack",
                        ),
                    ).is_error
                continue
            if a.name == "amend_cashback_investigation" and route == "suboptimal":
                args["items"][1]["amount"] = 41
            data(s.call(a.name, args))
    return s


@pytest.mark.parametrize("mode", ["half", "full"])
def test_resolution_reference_and_noop(scenario, mode):
    for route, want in [("reference", 1), ("noop", 0)]:
        s = run_reference(scenario, route)
        s.call("done", {})
        score = s.grade() if mode == "half" else grade_full_session(s).reward_info
        assert score.reward == want, score
        if want:
            case = s.env.tools.db.cases[CASE]
            assert case.amount == 61
            assert {x.transaction_id: x.amount for x in case.reward_items} == {
                A: 14,
                B: 40,
                E: 7,
            }
            assert (
                s.env.tools.db.cashback_adjustment_permissions["cap_620184"]["used"]
                is False
            )


@pytest.mark.parametrize(
    "route,want",
    [
        ("repeat", 1),
        ("suboptimal", 0),
        ("pending", 0),
        ("wrong_total", 0),
        ("waiver", 0),
        ("missing_consent", 0),
        ("changed_packet", 0),
        ("double", 0),
        ("stack", 0),
    ],
)
def test_resolution_routes(scenario, route, want):
    s = run_reference(scenario, route)
    s.call("done", {})
    result = s.grade()
    assert result.reward == want, result
    if route == "suboptimal":
        assert s.env.tools.assert_reward_resolution_attempts(C, CASE)


@pytest.mark.parametrize(
    "scenario,total,case_total",
    [("tie", 24, 61), ("approved26", 26, 59), ("no_late", 30, 62)],
    indirect=["scenario"],
)
def test_data_changes_optimal_strategy(scenario, total, case_total):
    routes = ["reference", "tie_alternative"] if total == 24 else ["reference"]
    for route in routes:
        s = run_reference(scenario, route)
        s.call("done", {})
        grade = grade_full_session(s).reward_info
        assert grade.reward == 1, grade
        assert s.env.tools.db.cases[CASE].amount == case_total
        credits = [
            x
            for x in s.env.tools.db.transactions.values()
            if x.id.startswith("reward-resolution:")
        ]
        assert sum(x.amount for x in credits) == total


def test_unreceived_consent_does_not_authorize_same_batch(scenario):
    from tau2.data_model.message import AssistantMessage, ToolMessage

    s = run_reference(scenario)
    s.call("done", {})
    messages = s._messages
    index = next(
        i
        for i, m in enumerate(messages)
        if isinstance(m, AssistantMessage)
        and m.tool_calls
        and m.tool_calls[0].name == "execute_reward_resolution"
    )
    assert isinstance(messages[index - 1], ToolMessage)
    # Preserve execution and receipt order; form payment before consent response arrives.
    response = messages.pop(index - 1)
    messages.insert(index, response)
    result = grade_full_session(s).reward_info
    assert result.reward == 0 and result.action_checks is not None


def test_early_offer_read_and_stale_consent_recovery(scenario):
    # Pre-settlement observation is lawful; revision change requires renewed consent.
    s = PlatformSession(
        scenario, environment.get_environment, max_calls=60, max_errors=5
    )
    actions = list(scenario.evaluation_criteria.actions)
    pay = next(a for a in actions if a.name == "execute_reward_resolution")
    confirmation = next(a for a in actions if a.action_id == "bank2:v5:consent")
    refresh = next(a for a in actions if a.name == "refresh_cashback_settlement")
    stale = None
    for a in actions:
        if a == refresh:
            data(
                s.call(
                    "get_reward_resolution_offers", dict(customer_id=C, case_id=CASE)
                )
            )
            request = deepcopy(confirmation.arguments)
            request["request"]["state_revision"] = 1
            stale = data(s.call("ask_client", request))["confirmation_id"]
        if a == confirmation:
            args = deepcopy(pay.arguments)
            args["confirmation_id"] = stale
            assert data(s.call(pay.name, args))["status"] == "consent_stale"
            consent = data(s.call(a.name, a.arguments))["confirmation_id"]
        elif a == pay:
            args = deepcopy(a.arguments)
            args["confirmation_id"] = consent
            data(s.call(a.name, args))
        elif a.name != "done":
            data(s.call(a.name, a.arguments))
    s.call("done", {})
    assert s.grade().reward == 1


def test_migration_is_idempotent_and_preserves_other_subjects(bank2_v4_data):
    from upgrade_bank2_resolution import migrate

    p = bank2_v4_data
    original = json.loads((p / "tasks.json").read_text())
    before_db = json.loads((p / "db.json").read_text())
    migrate(p)
    after = json.loads((p / "tasks.json").read_text())
    for left, right in zip(original, after):
        if left["id"] == "Bank 2":
            continue
        left["evaluation_criteria"].pop("tool_access_policy")
        right["evaluation_criteria"].pop("tool_access_policy")
        assert left == right
    after_db = json.loads((p / "db.json").read_text())
    assert all(after_db[k] == v for k, v in before_db.items())
    hashes = {
        str(x.relative_to(p)): x.read_bytes() for x in p.rglob("*") if x.is_file()
    }
    migrate(p)
    assert hashes == {
        str(x.relative_to(p)): x.read_bytes() for x in p.rglob("*") if x.is_file()
    }


def test_forbidden_foreign_selector_after_success_still_zero(scenario):
    s = run_reference(scenario)
    foreign = next(c.id for c in s.env.tools.db.cases.values() if c.customer_id != C)
    assert s.call(
        "get_reward_resolution_offers", dict(customer_id=C, case_id=foreign)
    ).is_error
    s.call("done", {})
    score = s.grade()
    assert score.db_check.db_reward == 1 and score.reward == 0


def test_missing_rule_receipt_before_payment_is_not_repaired_later(scenario):
    from tau2.data_model.message import AssistantMessage, ToolMessage

    s = run_reference(scenario)
    s.call("done", {})
    messages = s._messages
    i = next(
        i
        for i, m in enumerate(messages)
        if isinstance(m, AssistantMessage)
        and m.tool_calls
        and m.tool_calls[0].arguments.get("path")
        == "/knowledge/reward_resolution_amounts.md"
    )
    assert isinstance(messages[i + 1], ToolMessage)
    source = messages[i : i + 2]
    del messages[i : i + 2]
    j = next(
        i
        for i, m in enumerate(messages)
        if isinstance(m, AssistantMessage)
        and m.tool_calls
        and m.tool_calls[0].name == "amend_cashback_investigation"
    )
    messages[j:j] = source
    result = s.grade()
    assert result.db_check.db_reward == 1 and result.reward == 0
