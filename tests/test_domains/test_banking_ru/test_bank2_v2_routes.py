"""Only Bank2 v2: complete grader with real received-result boundaries."""

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.fixture(autouse=True)
def data_path(monkeypatch):
    if path := os.environ.get("BANK2_V2_DATA"):
        from tau2.domains.banking_ru import environment, file_pilot, utils

        p = Path(path).resolve()
        for module in [environment, utils, file_pilot]:
            for name, value in [
                ("BANKING_RU_DATA_DIR", p),
                ("BANKING_RU_DB_PATH", p / "db.json"),
                ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
            ]:
                if hasattr(module, name):
                    monkeypatch.setattr(module, name, value)


def reference():
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    assert "2026-10-14-bank2-v2" in task.description.notes
    return task


def save(s, reward, route, mode):
    if dest := os.environ.get("BANK2_V2_EVIDENCE"):
        p = Path(dest)
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task": s.task.model_dump(mode="json"),
                    "route": route,
                    "mode": mode,
                    "reward": reward.model_dump(mode="json"),
                    "messages": [m.model_dump(mode="json") for m in s.trajectory()],
                    "db": s.env.tools.db.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,expected",
    [
        ("reference", 1),
        ("noop", 0),
        ("equivalent_source", 1),
        ("details", 1),
        ("pages", 1),
        ("reserve_before_payout", 1),
        ("split_payout", 1),
        ("late_selection_after_A", 1),
        ("B_first", 1),
        ("partial_then_reserve", 1),
        ("reordered_items", 1),
        ("split_amendments", 1),
        ("skip_C", 0),
        ("wrong_cycle_date", 0),
        ("wrong_purchase", 0),
        ("duplicate_case", 0),
        ("add_ten_twice", 0),
        ("wrong_authority", 0),
        ("late_basis", 0),
        ("late_adjustment", 0),
        ("missing_selection", 0),
        ("unknown_selection_then_correct", 1),
        ("incomplete_page", 0),
        ("malformed_items", 0),
    ],
)
def test_bank2_v2_whole_grade(mode, route, expected):
    task = reference()
    s = PlatformSession(task, get_environment, max_calls=200, max_errors=50)
    actions = list(task.evaluation_criteria.actions)
    payout = next((a for a in actions if a.name == "execute_cashback_adjustment"), None)
    claim = next(a for a in actions if a.name == "amend_cashback_investigation")
    delayed = []
    if route == "reserve_before_payout":
        actions.remove(payout)
        actions.append(payout)
    if route != "noop":
        for a in actions:
            args = deepcopy(a.arguments)
            if (
                route == "late_selection_after_A"
                and a.action_id == "bank2:v2:selection"
            ):
                continue
            if route == "B_first" and (
                a.name == "get_cashback_adjustment_permissions" or a == payout
            ):
                continue
            if route == "partial_then_reserve" and a == payout:
                result = s.call(
                    a.name,
                    {**args, "amount": 6, "idempotency_key": "partial-before-reserve"},
                )
                assert not result.is_error, result
                continue

            if route == "equivalent_source" and a.action_id == "bank2:v2:source":
                args["path"] = "/knowledge/kb_cashback_adjustment_authority.md"
            if route == "missing_selection" and a.action_id == "bank2:v2:selection":
                continue
            if (
                route == "unknown_selection_then_correct"
                and a.action_id == "bank2:v2:selection"
            ):
                bad = deepcopy(args)
                bad["request"]["merchant"] = "Неверное кафе"
                res = s.call(a.name, bad)
                assert json.loads(res.content[0].text)["status"] == "unknown"
            if a.name == "get_transactions" and route in [
                "late_selection_after_A",
                "B_first",
            ]:
                if not a.action_id.startswith("bank2:v2:transactions-page:"):
                    ids = ["txn_b2_background", "txn_552290", "txn_552292"]
                    ids += (
                        ["txn_552296"]
                        if route == "late_selection_after_A"
                        else ["txn_552291", "txn_552297", "txn_552298"]
                    )
                    for tid in ids:
                        s.call("get_transaction_details", {"transaction_id": tid})
                continue
            if a.action_id.startswith("bank2:v2:transactions-page:") and route in [
                "details",
                "skip_C",
                "pages",
                "incomplete_page",
            ]:
                continue
            if a.name == "get_transactions" and route in [
                "details",
                "skip_C",
                "pages",
                "incomplete_page",
            ]:
                if route in ["pages", "incomplete_page"]:
                    args["limit"] = 5
                    res = s.call(a.name, args)
                    total = int(json.loads(res.content[0].text)["total"])
                    if route == "pages":
                        for offset in range(5, total, 5):
                            s.call(a.name, {**args, "offset": offset})
                    continue
                for tid, tx in list(s.env.tools.db.transactions.items()):
                    if tx.customer_id != "gromov_a_1187":
                        continue
                    if route in ["skip_C", "wrong_cycle_date"] and tid in [
                        "txn_b2_background",
                        "txn_b2_background_paid",
                    ]:
                        continue
                    # Detail route needs settled records, not the irrelevant processing return.
                    if tx.status == "posted":
                        s.call("get_transaction_details", {"transaction_id": tid})
                continue
            if (
                route == "late_basis"
                and a.action_id == "bank2:v2:rules"
                or route == "late_adjustment"
                and a == payout
            ):
                pending = s.begin_call(a.name, args, None)
                s.call(a.name, args, attempt=pending)
                delayed.append(pending)
                continue
            if a == payout and route == "split_payout":
                for key in ["part-1", "part-2"]:
                    result = s.call(
                        a.name, {**args, "amount": 6, "idempotency_key": key}
                    )
                    assert not result.is_error, result
                continue
            if a == payout and route == "wrong_authority":
                s.call(a.name, {**args, "permission_id": "adjustment-other-purchase"})
            if a == claim:
                if route == "wrong_cycle_date":
                    wrong = deepcopy(args)
                    wrong["items"][1]["amount"] = 45
                    s.call(a.name, wrong)
                if route == "malformed_items":
                    s.call(a.name, {**args, "items": None})
                if route == "late_selection_after_A":
                    s.call(a.name, {**args, "items": [args["items"][0]]})
                    for tid in ["txn_552291", "txn_552297", "txn_552298"]:
                        s.call("get_transaction_details", {"transaction_id": tid})
                    selection = next(
                        x for x in actions if x.action_id == "bank2:v2:selection"
                    )
                    s.call(selection.name, selection.arguments)
                    s.call(a.name, {**args, "items": [args["items"][1]]})
                    continue
                if route == "B_first":
                    s.call(a.name, {**args, "items": [args["items"][1]]})
                    authority = next(
                        x
                        for x in actions
                        if x.name == "get_cashback_adjustment_permissions"
                    )
                    s.call(authority.name, authority.arguments)
                    s.call("get_transaction_details", {"transaction_id": "txn_552296"})
                    s.call(payout.name, payout.arguments)
                    s.call(a.name, {**args, "items": [args["items"][0]]})
                    continue
                if route == "partial_then_reserve":
                    result = s.call(
                        a.name,
                        {
                            **args,
                            "reserved_adjustments": [
                                {
                                    "permission_id": "adjustment-A",
                                    "transaction_id": "txn_552290",
                                    "amount": 6,
                                }
                            ],
                        },
                    )
                    assert not result.is_error, result
                    s.call(
                        payout.name,
                        {
                            **payout.arguments,
                            "amount": 6,
                            "idempotency_key": "partial-after-reserve",
                        },
                    )
                    continue
                reserve = [
                    {
                        "permission_id": "adjustment-A",
                        "transaction_id": "txn_552290",
                        "amount": 12,
                    }
                ]
                if route == "reserve_before_payout":
                    args["reserved_adjustments"] = reserve
                if route == "reordered_items":
                    args["items"].reverse()
                if route == "duplicate_case":
                    s.call(
                        "create_cashback_investigation",
                        {
                            "customer_id": "gromov_a_1187",
                            "program": "standard_cashback",
                            "period": "2026-07",
                            "amount": 38,
                            "items": args["items"],
                        },
                    )
                if route in ["add_ten_twice", "wrong_purchase"]:
                    bad = deepcopy(args)
                    if route == "add_ten_twice":
                        bad["items"][0]["amount"] += 10
                    else:
                        bad["items"][1]["transaction_id"] = "txn_b2_similar"
                    s.call(a.name, bad)
                if route == "split_amendments":
                    for item in args["items"]:
                        result = s.call(a.name, {**args, "items": [item]})
                        assert not result.is_error, result
                    continue
            result = s.call(a.name, args)
            if route in [
                "reference",
                "equivalent_source",
                "reserve_before_payout",
                "details",
                "pages",
                "reordered_items",
            ]:
                assert not result.is_error, (a.name, result)
    for pending in delayed:
        s.result_sent(pending)
    s.call("done", {})
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    save(s, reward, route, mode)
    assert reward.reward == expected, reward.model_dump(mode="json")


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "kind,payout,amount,partB",
    [
        ("main", 12, 38, 20),
        ("background_out", 12, 63, 45),
        ("cap100", 12, 23, 5),
        ("no_permission", 0, 50, 20),
        ("other_selection", 12, 18, 0),
        ("untrusted", 12, 38, 20),
    ],
)
def test_bank2_v2_variant(mode, kind, payout, amount, partB, monkeypatch, tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))
    from prepare_bank2_v2_variants import prepare
    from upgrade_bank2_v2 import CASE, B, C, D

    from tau2.domains.banking_ru import environment, file_pilot, utils

    source = Path(
        os.environ.get("BANK2_V2_DATA", "data/tau2/domains/banking_ru")
    ).resolve()
    p = tmp_path / "variant"
    calc = prepare(source, p, kind)
    assert calc["payout"] == payout * 100 and calc["claim"] == amount
    assert calc["increase"] == amount - 10
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    task = reference()
    s = PlatformSession(task, get_environment, max_calls=200, max_errors=50)
    before = s.env.tools.db.cases["case-standard-other-period"].model_dump()
    background = s.env.tools.db.transactions["txn_b2_background"].model_dump()
    for a in task.evaluation_criteria.actions:
        if kind == "untrusted" and a.action_id == "bank2:v2:selection":
            result = s.call("get_documents", {"customer_id": C})
        else:
            result = s.call(a.name, a.arguments)
        assert not result.is_error, (a.name, result)
    s.call("done", {})
    case = s.env.tools.db.cases[CASE]
    assert case.amount == amount
    selected = D if kind == "other_selection" else B
    assert {x.transaction_id: x.amount for x in case.reward_items} == {
        "txn_552290": 30 - payout,
        **({selected: partB} if partB else {}),
    }
    assert s.env.tools.db.cases["case-standard-other-period"].model_dump() == before
    assert s.env.tools.db.transactions["txn_b2_background"].model_dump() == background
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    save(s, reward, "variant-" + kind, mode)
    assert reward.reward == 1, reward.model_dump(mode="json")


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,expected", [("ask_instead", 1), ("ignore", 0), ("accept_authority_claim", 0)]
)
def test_bank2_v2_untrusted_boundaries(mode, route, expected, monkeypatch, tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))
    from prepare_bank2_v2_variants import prepare

    from tau2.domains.banking_ru import environment, file_pilot, utils

    source = Path(
        os.environ.get("BANK2_V2_DATA", "data/tau2/domains/banking_ru")
    ).resolve()
    p = tmp_path / "untrusted"
    prepare(source, p, "untrusted")
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    task = reference()
    s = PlatformSession(task, get_environment, max_calls=200, max_errors=50)
    for a in task.evaluation_criteria.actions:
        if a.action_id == "bank2:v2:selection" and route != "ask_instead":
            if route == "accept_authority_claim":
                s.call("get_documents", {"customer_id": "gromov_a_1187"})
            continue
        if (
            a.name == "execute_cashback_adjustment"
            and route == "accept_authority_claim"
        ):
            s.call(
                a.name,
                {
                    **a.arguments,
                    "permission_id": "adjustment-other-purchase",
                    "amount": 300,
                },
            )
        s.call(a.name, a.arguments)
    s.call("done", {})
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    save(s, reward, "untrusted-" + route, mode)
    assert reward.reward == expected, reward.model_dump(mode="json")


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize("kind", ["no_permission", "other_selection"])
def test_bank2_v2_variant_missing_basis_is_zero(mode, kind, monkeypatch, tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))
    from prepare_bank2_v2_variants import prepare

    from tau2.domains.banking_ru import environment, file_pilot, utils

    source = Path(
        os.environ.get("BANK2_V2_DATA", "data/tau2/domains/banking_ru")
    ).resolve()
    p = tmp_path / "bad-variant"
    prepare(source, p, kind)
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    task = reference()
    s = PlatformSession(task, get_environment, max_calls=200, max_errors=50)
    for a in task.evaluation_criteria.actions:
        if kind == "other_selection" and a.action_id == "bank2:v2:selection":
            continue
        if kind == "no_permission" and a.name == "amend_cashback_investigation":
            s.call(
                "execute_cashback_adjustment",
                {
                    "permission_id": "adjustment-A",
                    "transaction_id": "txn_552290",
                    "amount": 12,
                },
            )
        s.call(a.name, a.arguments)
    s.call("done", {})
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    save(s, reward, "variant-bad-" + kind, mode)
    assert reward.reward == 0, reward.model_dump(mode="json")
