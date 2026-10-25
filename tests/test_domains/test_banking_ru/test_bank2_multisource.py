"""Addressed Bank2 v4 whole-grader alternatives and forbidden paths."""

import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru import environment, file_pilot, utils
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/banking_ru"))
C, A, B, D, E = "gromov_a_1187", "txn_804219", "txn_804227", "txn_804231", "txn_804205"
CASE, PERIOD, CAP = "case_610903", "2026-07", "cap_620184"


@pytest.fixture
def scenario(tmp_path, monkeypatch, request, bank2_v4_data):
    from upgrade_bank2_multisource import migrate

    p = tmp_path / "data"
    shutil.copytree(bank2_v4_data, p)
    migrate(p)
    kind = request.param
    if kind != "main":
        # Variant changes are explicitly made to a temporary scenario only.
        from upgrade_bank2_multisource import apply_variant

        apply_variant(p, kind)
    from build_access_contract import with_contract

    tasks = json.loads((p / "tasks.json").read_text())
    raw_db = json.loads((p / "db.json").read_text())
    with_contract(tasks, raw_db)
    (p / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2) + "\n"
    )
    for module in [environment, utils, file_pilot]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", p),
            ("BANKING_RU_DB_PATH", p / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", p / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    yield next(t for t in environment.get_tasks() if t.id == "Bank 2"), kind


def payload(result):
    return json.loads(result.content[0].text)


ROUTES = [
    ("reference", 1),
    ("shortest", 1),
    ("independent_A", 1),
    ("preserve_E", 1),
    ("B_first", 1),
    ("noop", 0),
    ("reserve", 1),
    ("split_payment", 1),
    ("split_amendments", 1),
    ("lose_E", 0),
    ("stack_programs", 0),
    ("ignore_A5", 0),
    ("wrong_activation", 0),
    ("wrong_cap", 0),
    ("add_D", 0),
    ("repeat_B7", 0),
    ("unresolved", 0),
]


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "scenario,route,want",
    [("main", route, want) for route, want in ROUTES]
    + [
        (kind, "reference", 1)
        for kind in ["late_activation", "no_compensation", "no_authority", "select_D"]
    ],
    indirect=["scenario"],
)
def test_bank2_multisource_whole_grader(scenario, mode, route, want, tmp_path):
    task, kind = scenario
    s = PlatformSession(task, environment.get_environment, max_calls=60, max_errors=5)
    actions = list(task.evaluation_criteria.actions)
    payment = next(
        (a for a in actions if a.name == "execute_cashback_adjustment"), None
    )
    amend = next(a for a in actions if a.name == "amend_cashback_investigation")
    if route == "shortest":
        actions = [a for a in actions if a.name != "get_cards"]
        transaction_actions = [a for a in actions if a.name == "get_transactions"]
        actions.remove(transaction_actions[1])
        transaction_actions[0] = transaction_actions[0].model_copy(
            update={
                "arguments": dict(
                    customer_id=C,
                    posted_from="2026-07-01",
                    posted_to="2026-08-28",
                    limit=50,
                )
            }
        )
        actions = [
            transaction_actions[0] if a.name == "get_transactions" else a
            for a in actions
        ]
    if route == "independent_A":
        selected_actions = [
            a
            for a in actions
            if a.name == "ask_client"
            and a.arguments.get("request", {}).get("type") == "transaction_selection"
        ]
        for a in selected_actions:
            actions.remove(a)
            actions.insert(actions.index(amend), a)
    if route == "reserve":
        actions.remove(amend)
        actions.insert(actions.index(payment), amend)
    prefix_ids = []
    if route != "noop":
        for a in actions:
            args = deepcopy(a.arguments)
            if (
                route == "shortest"
                and a.name == "file_read"
                and a.arguments.get("path") == "/knowledge/reward_product_scope.md"
            ):
                # The model starts without source addresses; discover the revision.
                discovered = s.call(
                    "file_search",
                    dict(pattern="multi-program-v1", root="/knowledge", limit=200),
                )
                assert not discovered.is_error
            if a.name in ["done", "get_cashback_adjustment_status"]:
                continue
            if route == "independent_A" and a.name == "get_transactions":
                # Discover the independent prefix using the allowed posting filter.
                if a.action_id.endswith(":purchases"):
                    page = payload(s.call(a.name, {**args, "posted_to": "2026-07-25"}))
                    prefix_ids = [
                        row["id"] for row in page["items"] if row["kind"] == "purchase"
                    ]
                else:
                    for purchase_id in prefix_ids:
                        s.call(a.name, {**args, "original_transaction_id": purchase_id})
                continue
            if (
                route == "independent_A"
                and a.name == "get_transaction_details"
                and args.get("transaction_id") == "txn_804287"
            ):
                continue
            if route == "independent_A" and a == amend:
                page = payload(
                    s.call(
                        "get_transactions",
                        dict(
                            customer_id=C,
                            posted_from="2026-07-26",
                            posted_to="2026-07-31",
                            limit=50,
                        ),
                    )
                )
                for row in page["items"]:
                    if row["kind"] == "purchase":
                        s.call(
                            "get_transactions",
                            dict(
                                customer_id=C,
                                original_transaction_id=row["id"],
                                posted_to="2026-08-28",
                                limit=50,
                            ),
                        )
            if route == "B_first" and a.name == "get_transactions":
                if a.action_id.endswith(":purchases"):
                    s.call(
                        a.name,
                        {
                            **args,
                            "posted_from": "2026-07-26",
                            "posted_to": "2026-07-26",
                        },
                    )
                else:
                    s.call(a.name, {**args, "original_transaction_id": B})
                continue
            if route == "B_first" and a.name == "execute_cashback_adjustment":
                result = s.call(
                    amend.name,
                    {
                        **amend.arguments,
                        "items": [dict(transaction_id=B, amount=53)],
                        "expected_revision": s.env.tools.db.cashback_state_revisions[
                            C + ":" + PERIOD
                        ],
                    },
                )
                assert not result.is_error, result
                page = payload(
                    s.call(
                        "get_transactions",
                        dict(
                            customer_id=C,
                            posted_from="2026-07-01",
                            posted_to="2026-07-25",
                            limit=50,
                        ),
                    )
                )
                for row in page["items"]:
                    if row["kind"] == "purchase":
                        s.call(
                            "get_transactions",
                            dict(
                                customer_id=C,
                                original_transaction_id=row["id"],
                                posted_to="2026-08-28",
                                limit=50,
                            ),
                        )
            if a.name == "execute_cashback_adjustment":
                args["expected_revision"] = s.env.tools.db.cashback_state_revisions[
                    C + ":" + PERIOD
                ]
                if route == "wrong_cap":
                    s.call(a.name, {**args, "permission_id": "cap_620197"})
                for index in range(2 if route == "split_payment" else 1):
                    parameters = {
                        **args,
                        "expected_revision": s.env.tools.db.cashback_state_revisions[
                            C + ":" + PERIOD
                        ],
                    }
                    if route == "split_payment":
                        parameters.update(amount=6, idempotency_key=f"part-{index}")
                    r = s.call(a.name, parameters)
                    assert not r.is_error, r
                    value = payload(r)
                    if (
                        value.get("status") == "execution_unknown"
                        and route != "unresolved"
                    ):
                        s.call(
                            "get_cashback_adjustment_status",
                            {"operation_id": value["operation_id"]},
                        )
                continue
            if a.name == "amend_cashback_investigation":
                if route == "preserve_E":
                    args["items"].append(dict(transaction_id=E, amount=7))
                args["expected_revision"] = s.env.tools.db.cashback_state_revisions[
                    C + ":" + PERIOD
                ]
                if route == "reserve":
                    args["reserved_adjustments"] = [
                        dict(permission_id=CAP, transaction_id=A, amount=12)
                    ]
                if route in ["stack_programs", "ignore_A5", "wrong_activation"]:
                    for row in args["items"]:
                        if row["transaction_id"] == (B if route != "ignore_A5" else A):
                            row["amount"] += 5 if route == "ignore_A5" else 20
                if route == "add_D":
                    args["items"].append(dict(transaction_id=D, amount=15))
                if route == "lose_E":
                    s.call(
                        a.name, {**args, "items": [dict(transaction_id=E, amount=0)]}
                    )
                if route == "split_amendments":
                    for row in args["items"]:
                        r = s.call(
                            a.name,
                            {
                                **args,
                                "items": [row],
                                "expected_revision": s.env.tools.db.cashback_state_revisions[
                                    C + ":" + PERIOD
                                ],
                            },
                        )
                        assert not r.is_error, r
                    continue
            r = s.call(a.name, args)
            if route == "repeat_B7" and a.name == "refresh_cashback_settlement":
                # Legitimate refresh is idempotent; duplicate grant is a forbidden attempt.
                assert payload(s.call(a.name, args))["changed_transaction_ids"] == []
                s.call("grant_cashback", {"transaction_id": B, "amount": 7})
    s.call("done", {})
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    dest = tmp_path / "evidence"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{kind}-{route}-{mode}.json").write_text(
        json.dumps(
            dict(
                calls=len(s.mcp_events),
                reward=reward.model_dump(mode="json"),
                task=task.model_dump(mode="json"),
                messages=[m.model_dump(mode="json") for m in s.trajectory()],
                db=s.env.tools.db.model_dump(mode="json"),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    assert reward.reward == want, reward.model_dump(mode="json")
    if want:
        expected = {
            "main": 73,
            "late_activation": 20,
            "no_compensation": 78,
            "no_authority": 85,
            "select_D": 35,
        }[kind]
        assert s.env.tools.db.cases[CASE].amount == expected
        assert (
            next(
                i
                for i in s.env.tools.db.cases[CASE].reward_items
                if i.transaction_id == E
            ).amount
            == 7
        )
        assert not s.env.tools.db.cases[CASE].adjustment_reservations


@pytest.mark.parametrize("scenario", ["main"], indirect=True)
@pytest.mark.parametrize("mode", ["half", "full"])
def test_split_amendment_preserves_pending_reservation(scenario, mode):
    task, _ = scenario
    s = PlatformSession(task, environment.get_environment, max_calls=60, max_errors=5)
    for a in task.evaluation_criteria.actions:
        if a.name in {
            "execute_cashback_adjustment",
            "get_cashback_adjustment_status",
            "amend_cashback_investigation",
        }:
            continue
        s.call(a.name, a.arguments)
    s.call(
        "amend_cashback_investigation",
        dict(
            case_id=CASE,
            items=[dict(transaction_id=A, amount=13)],
            reserved_adjustments=[dict(permission_id=CAP, transaction_id=A, amount=12)],
            expected_revision=2,
        ),
    )
    s.call(
        "amend_cashback_investigation",
        dict(
            case_id=CASE, items=[dict(transaction_id=B, amount=53)], expected_revision=3
        ),
    )
    rows = s.env.tools.db.cases[CASE].adjustment_reservations
    assert [(r.transaction_id, r.amount) for r in rows] == [(A, 12)]

    pay = next(
        a
        for a in task.evaluation_criteria.actions
        if a.name == "execute_cashback_adjustment"
    )
    result = payload(s.call(pay.name, {**pay.arguments, "expected_revision": 4}))
    s.call("get_cashback_adjustment_status", dict(operation_id=result["operation_id"]))
    s.call("done", {})
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    assert reward.reward == 1, reward.model_dump(mode="json")
    assert not s.env.tools.db.cases[CASE].adjustment_reservations
