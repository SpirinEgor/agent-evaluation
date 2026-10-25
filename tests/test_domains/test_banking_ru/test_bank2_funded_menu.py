"""Whole-grader controls for jointly funded compatibility menus, accepting all feasible maxima."""

import gzip
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.domains.banking_ru import environment, file_pilot, utils
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session
from tests.utils.banking_parent_fixture import copy_historical_data, restore_bank2_v5

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/banking_ru"))
C, A, B, E, CASE = (
    "gromov_a_1187",
    "txn_804219",
    "txn_804227",
    "txn_804205",
    "case_610903",
)


@pytest.fixture(scope="session")
def compatible_data(tmp_path_factory):
    from upgrade_bank2_compatibility import migrate as compatibility
    from upgrade_bank2_coupled_menu import migrate as coupled
    from upgrade_bank2_feasible_offers import migrate as feasible
    from upgrade_bank2_funded_menu import migrate
    from upgrade_bank2_history import migrate as history
    from upgrade_bank2_large_selection import migrate as large
    from upgrade_bank2_posting_order import migrate as posting
    from upgrade_bank2_promotion_sources import migrate as source
    from upgrade_bank2_selection import migrate as selection

    p = tmp_path_factory.mktemp("compatible") / "data"
    copy_historical_data("fees_rewards_parent_2026_10_14", p)
    restore_bank2_v5(p)
    for step in [
        history,
        feasible,
        posting,
        selection,
        large,
        source,
        compatibility,
        coupled,
        migrate,
    ]:
        step(p)
    return p


@pytest.fixture
def compatible(compatible_data, monkeypatch):
    for mod in [environment, file_pilot, utils]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", compatible_data),
            ("BANKING_RU_DB_PATH", compatible_data / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", compatible_data / "tasks.json"),
        ]:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, value)
    return next(t for t in environment.get_tasks() if t.id == "Bank 2")


def received(result):
    assert not result.is_error, result
    try:
        return json.loads(result.content[0].text)
    except json.JSONDecodeError:
        return result.content[0].text


@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("constructed", 1),
        ("reverse_repeat", 1),
        ("different_pages", 1),
        ("split", 1),
        ("previous_menu", 0),
        ("conflict_then_correct", 0),
        ("omit_unused", 1),
        ("omit_selected", 0),
        ("document_terms", 1),
        ("noop", 0),
        ("suboptimal", 0),
        ("conflict", 0),
        ("missing_page", 0),
        ("late_page", 0),
        ("changed_consent", 0),
        ("bank_over", 0),
        ("partner_over", 0),
    ],
)
def test_whole_funded(compatible, route, want, tmp_path):
    from upgrade_bank2_funded_menu import OFFER, components

    s = PlatformSession(
        compatible, environment.get_environment, max_calls=100, max_errors=5
    )
    initial = s.env.tools.db.accounts["acc_1187"].balance
    packet = confirmation = None
    deferred = []
    for action in [] if route == "noop" else compatible.evaluation_criteria.actions:
        if action.name == "done":
            continue
        args = deepcopy(action.arguments)
        if route == "document_terms" and action.name == "get_promotions":
            continue
        if (
            action.name == "get_reward_resolution_components"
            and args["offer_id"] == OFFER
        ):
            if route in ["omit_unused", "omit_selected"]:
                if args["offset"] == 0:
                    chosen = next(
                        a.arguments["component_ids"]
                        for a in compatible.evaluation_criteria.actions
                        if a.name == "execute_reward_resolution"
                    )
                    rows = s.env.tools.db.reward_resolution_offers[OFFER].components
                    skipped = next(
                        i
                        for i, row in enumerate(rows)
                        if (row.id in chosen) == (route == "omit_selected")
                    )
                    for start, end in [(0, skipped), (skipped + 1, len(rows))]:
                        for offset in range(start, end, 100):
                            received(
                                s.call(
                                    action.name,
                                    {
                                        **args,
                                        "offset": offset,
                                        "limit": min(100, end - offset),
                                    },
                                )
                            )
                continue
            if route in ["missing_page", "late_page"] and args["offset"] == 0:
                deferred.append((action.name, args))
                continue
            if route == "different_pages":
                if args["offset"] == 0:
                    for offset in range(0, 1428, 50):
                        received(
                            s.call(action.name, {**args, "offset": offset, "limit": 50})
                        )
                continue
        if (
            action.name == "ask_client"
            and args.get("request", {}).get("type") == "reward_resolution_confirmation"
        ):
            packet = args["request"]
            good_packet = deepcopy(packet)
            if route == "constructed":
                assert sorted(packet["component_ids"]) != components()[2]
                packet["component_ids"] = components()[2]
            if route == "reverse_repeat":
                packet["component_ids"].reverse()
            if route == "suboptimal":
                packet["component_ids"].pop()
                packet.update(
                    total_amount=57.00,
                    payouts=[
                        dict(transaction_id=A, amount=14.25),
                        dict(transaction_id=B, amount=42.75),
                    ],
                    resulting_claims=[
                        dict(transaction_id=A, amount=10.12),
                        dict(transaction_id=B, amount=10.25),
                        dict(transaction_id=E, amount=7),
                    ],
                )
            if route in ["conflict", "conflict_then_correct"]:
                offer = s.env.tools.db.reward_resolution_offers[OFFER]
                byid = {p.id: p for p in offer.components}
                selected = packet["component_ids"]
                left = next(
                    p
                    for p in offer.components
                    if p.id in selected and p.incompatible_with
                )
                bad = byid[left.incompatible_with[0]]
                selected[:] = [
                    bad.id if byid[x].exclusive_group == bad.exclusive_group else x
                    for x in selected
                ]
                assert len(set(selected)) == 476
            if route in ["bank_over", "partner_over"]:
                rows = s.env.tools.db.reward_resolution_offers[OFFER].components
                byid = {p.id: p for p in rows}
                selected = packet["component_ids"]
                for old in selected:
                    candidates = [
                        p
                        for p in rows
                        if p.exclusive_group == byid[old].exclusive_group
                        and not p.incompatible_with
                    ]
                    bad = next(
                        (
                            p
                            for p in candidates
                            if (
                                p.bank_cost > byid[old].bank_cost
                                if route == "bank_over"
                                else p.partner_cost > byid[old].partner_cost
                            )
                        ),
                        None,
                    )
                    if bad:
                        selected[selected.index(old)] = bad.id
                        break
                else:
                    raise AssertionError("No isolated budget violation witness")
            if route == "previous_menu":
                from bank2_resolution_oracle import calculate

                raw = json.loads(utils.BANKING_RU_DB_PATH.read_text())
                old = calculate(raw)["offers"]["offer_660241"]
                packet.update(
                    offer_id="offer_660241",
                    component_ids=old["component_ids"],
                    total_amount=56.88,
                    payouts=[
                        dict(transaction_id=A, amount=14.22),
                        dict(transaction_id=B, amount=42.66),
                    ],
                    resulting_claims=[
                        dict(transaction_id=A, amount=10.15),
                        dict(transaction_id=B, amount=10.34),
                        dict(transaction_id=E, amount=7),
                    ],
                )
            confirmation = received(s.call(action.name, args))["confirmation_id"]
        elif action.name == "execute_reward_resolution":
            for key in ["offer_id", "component_ids", "total_amount", "payouts"]:
                args[key] = deepcopy(packet[key])
            args["confirmation_id"] = confirmation
            if route == "changed_consent":
                args["component_ids"] = args["component_ids"][:-1]
            result = s.call(action.name, args)
            if route == "conflict_then_correct":
                assert (
                    result.is_error and not s.env.tools.db.reward_resolution_executions
                )
                packet = good_packet
                confirmation = received(
                    s.call("ask_client", dict(customer_id=C, request=packet))
                )["confirmation_id"]
                for key in ["offer_id", "component_ids", "total_amount", "payouts"]:
                    args[key] = deepcopy(packet[key])
                args["confirmation_id"] = confirmation
                result = s.call(action.name, args)
            if route in ["conflict", "changed_consent", "bank_over", "partner_over"]:
                assert (
                    result.is_error and not s.env.tools.db.reward_resolution_executions
                )
                break
            received(result)
            if route == "late_page":
                for name, delayed in deferred:
                    received(s.call(name, delayed))
            if route == "reverse_repeat":
                args["component_ids"].reverse()
                received(s.call(action.name, args))
        elif action.name == "amend_cashback_investigation":
            args["items"] = [
                x for x in packet["resulting_claims"] if x["transaction_id"] in [A, B]
            ]
            if route == "split":
                for i, item in enumerate(reversed(args["items"])):
                    received(
                        s.call(
                            action.name,
                            {**args, "items": [item], "expected_revision": 3 + i},
                        )
                    )
            else:
                received(s.call(action.name, args))
        else:
            received(s.call(action.name, args))
    s.call("done", {})
    half, full = s.grade(), grade_full_session(s).reward_info
    out = Path(os.environ.get("BANK2_FUNDED_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / (route + ".json.gz"), "wt", encoding="utf-8") as f:
        json.dump(
            dict(
                half=half.model_dump(mode="json"),
                full=full.model_dump(mode="json"),
                simulation=s.to_simulation_run().model_dump(mode="json"),
                errors=s.errors,
            ),
            f,
            ensure_ascii=False,
        )
    assert half.reward == full.reward == want, (half, full)
    if want or route == "conflict_then_correct":
        case = s.env.tools.db.cases[CASE]
        assert case.amount == 27.25
        assert {x.transaction_id: x.amount for x in case.reward_items} == {
            A: 10.09,
            B: 10.16,
            E: 7,
        }
        assert round(s.env.tools.db.accounts["acc_1187"].balance - initial, 2) == 64.12
    if want or route in ["suboptimal", "previous_menu"]:
        assert s.errors == 0
        assert s.env.tools.assert_reward_resolution_attempts(C, CASE)
