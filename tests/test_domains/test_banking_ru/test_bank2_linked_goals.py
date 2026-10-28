"""Bank 2: payment and complete handoff, including a legal split amendment."""

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tau2.data_model.tasks import RewardType
from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.domains.banking_ru.mcp_systems import TOOL_OWNERS
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session


@pytest.mark.parametrize("mode", ["half", "full"])
@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("split", 1),
        ("early", 0),
        ("no_submit", 0),
        ("unreceived_version", 0),
        ("conflict_recovered", 1),
        ("repeat", 1),
        ("suboptimal", 0),
        ("previous_offer", 0),
        ("recovered_budget", 1),
    ],
)
def test_bank2_payment_and_review_whole_grade(mode, route, want):
    task = next(t for t in get_tasks() if t.id == "Bank 2")
    actions = deepcopy(task.evaluation_criteria.actions)
    if route in {"suboptimal", "previous_offer"}:
        total, first, components, offer = (
            (34, 14, ["B", "D", "F"], "offer_610735")
            if route == "suboptimal"
            else (24, 11, [], "offer_610723")
        )
        payouts = [
            {"transaction_id": "txn_804219", "amount": first},
            {"transaction_id": "txn_804227", "amount": total - first},
        ]
        items = [
            {"transaction_id": "txn_804219", "amount": 25 - first},
            {"transaction_id": "txn_804227", "amount": 53 - (total - first)},
        ]
        for action in actions:
            if action.action_id == "bank2:v5:consent":
                action.arguments["request"].update(
                    offer_id=offer,
                    component_ids=components,
                    payouts=payouts,
                    total_amount=total,
                    resulting_claims=items
                    + [{"transaction_id": "txn_804205", "amount": 7}],
                )
            elif action.name == "execute_reward_resolution":
                action.arguments.update(
                    offer_id=offer,
                    component_ids=components,
                    payouts=payouts,
                    total_amount=total,
                )
            elif action.name == "amend_cashback_investigation":
                action.arguments["items"] = items
    if route == "split":
        for action in actions:
            if action.name == "file_read":
                action.name, action.arguments = (
                    "corpus_command",
                    {"command": "cat " + action.arguments["path"]},
                )
            elif action.name == "execute_reward_resolution":
                action.arguments["component_ids"].reverse()
    assert actions[-1].name == "submit_cashback_review"
    submit = actions.pop()
    s = PlatformSession(
        task,
        get_environment,
        mcp_layout="directory" if route in {"reference", "split"} else "single",
    )

    def call(name, args):
        if s.directory is not None:
            owner = TOOL_OWNERS[name]
            assert not s.call(
                "bank_directory",
                {"operation": "open_tool", "system_id": owner, "tool_name": name},
            ).is_error
            return s.call(
                "bank_call", {"system_id": owner, "tool_name": name, "arguments": args}
            )
        return s.call(name, args)

    def payload(result):
        assert not result.is_error
        return json.loads(result.content[0].text)

    case_id = submit.arguments["case_id"]
    if route == "reference":
        s.replay_actions(task.evaluation_criteria.actions)
    elif route == "recovered_budget":
        consent_index = next(
            i for i, a in enumerate(actions) if a.action_id == "bank2:v5:consent"
        )
        s.replay_actions(actions[:consent_index])
        consent, pay = actions[consent_index : consent_index + 2]
        bad = deepcopy(consent.arguments)
        bad["request"].update(
            component_ids=["B", "D", "E", "G"],
            total_amount=42,
            payouts=[
                {"transaction_id": "txn_804219", "amount": 18},
                {"transaction_id": "txn_804227", "amount": 24},
            ],
            resulting_claims=[
                {"transaction_id": "txn_804219", "amount": 7},
                {"transaction_id": "txn_804227", "amount": 29},
                {"transaction_id": "txn_804205", "amount": 7},
            ],
        )
        receipt = payload(call(consent.name, bad))
        bad_pay = {
            **pay.arguments,
            **{
                k: bad["request"][k]
                for k in ("component_ids", "total_amount", "payouts")
            },
            "confirmation_id": receipt["confirmation_id"],
        }
        rejected = call(pay.name, bad_pay)
        assert rejected.is_error and "бюджет" in rejected.content[0].text
        receipt = payload(call(consent.name, consent.arguments))
        payload(
            call(
                pay.name,
                {**pay.arguments, "confirmation_id": receipt["confirmation_id"]},
            )
        )
        s.replay_actions(actions[consent_index + 2 :])
        case = json.loads(s.log[-1][1].content)
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": case["version"]})
        )
    elif route == "early":
        pay = next(
            i for i, a in enumerate(actions) if a.name == "execute_reward_resolution"
        )
        s.replay_actions(actions[:pay])
        cases = payload(
            call("get_cashback_investigations", {"customer_id": "gromov_a_1187"})
        )
        case = next(c for c in cases if c["id"] == case_id)
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": case["version"]})
        )
        for action in actions[pay:]:
            assert call(action.name, action.arguments).is_error
    elif route in {"split", "unreceived_version"}:
        amend = actions.pop()
        assert amend.name == "amend_cashback_investigation"
        s.replay_actions(actions)
        if route == "split":
            first = payload(
                call(
                    amend.name,
                    {**amend.arguments, "items": amend.arguments["items"][:1]},
                )
            )
            last = payload(
                call(
                    amend.name,
                    {
                        **amend.arguments,
                        "items": amend.arguments["items"][1:],
                        "expected_revision": first["state_revision"],
                    },
                )
            )
        else:
            # Author-only counterexample: execution happened, but the agent has
            # not received the response from which it guesses the new version.
            delayed = s.begin_call(amend.name, amend.arguments, None)
            last = payload(s.call(amend.name, amend.arguments, attempt=delayed))
        payload(
            call(submit.name, {"case_id": case_id, "expected_version": last["version"]})
        )
        if route == "unreceived_version":
            s.result_sent(delayed)
    else:
        s.replay_actions(actions)
        case = json.loads(s.log[-1][1].content)
        args = {"case_id": case_id, "expected_version": case["version"]}
        if route != "no_submit":
            if route == "conflict_recovered":
                assert (
                    payload(call(submit.name, {**args, "expected_version": 0}))[
                        "status"
                    ]
                    == "version_conflict"
                )
            payload(call(submit.name, args))
            if route == "repeat":
                payload(call(submit.name, args))
    assert not call("done", {}).is_error
    reward = s.grade() if mode == "half" else grade_full_session(s).reward_info
    assert reward.final_reward == want, reward.info
    assert set(reward.reward_breakdown) == {RewardType.DB, RewardType.ACTION}
    if route == "unreceived_version":
        assert reward.reward_breakdown == {RewardType.DB: 1, RewardType.ACTION: 0}
        assert any(
            "review_version_not_received" in str(f)
            for f in reward.info["action"]["result_argument_bindings"]
        )
    if route in {"early", "no_submit"}:
        assert reward.reward_breakdown[RewardType.DB] == 0
    if route in {"suboptimal", "previous_offer"}:
        assert reward.reward_breakdown == {RewardType.DB: 0, RewardType.ACTION: 1}, (
            reward.info
        )
    if want:
        case = s.env.tools.db.cases[case_id]
        assert case.review_status == "registered"
        assert case.responsible_unit == "rewards_review"
        assert case.review_amount == case.amount == 50
        expected = {"txn_804219": 10, "txn_804227": 33, "txn_804205": 7}
        assert {r.transaction_id: r.amount for r in case.review_items} == expected
        assert {r.transaction_id: r.amount for r in case.reward_items} == expected

    if directory := os.environ.get("BANK2_LINKED_EVIDENCE"):
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{route}-{mode}.json").write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "task_sha256": hashlib.sha256(
                        task.model_dump_json().encode()
                    ).hexdigest(),
                    "route": route,
                    "mode": mode,
                    "expected": want,
                    "final_reward": reward.final_reward,
                    "components": reward.reward_breakdown,
                    "info": reward.info,
                    "case": s.env.tools.db.cases[case_id].model_dump(mode="json"),
                    "calls": [
                        {
                            k: e.get(k)
                            for k in (
                                "canonical_name",
                                "original_name",
                                "request_order",
                                "response_order",
                                "error",
                            )
                        }
                        for e in s.mcp_events
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


def test_bank2_component_menu_has_one_hand_calculated_optimum():
    from itertools import combinations

    from tau2.domains.banking_ru.reward_resolution import selected_amounts

    task = next(t for t in get_tasks() if t.id == "Bank 2")
    session = PlatformSession(task, get_environment)
    offer = session.env.tools.db.reward_resolution_offers["offer_610735"]
    legal = []
    for length in range(1, len(offer.components) + 1):
        for subset in combinations([p.id for p in offer.components], length):
            try:
                amount, allocation = selected_amounts(offer, list(subset))
            except ValueError:
                continue
            legal.append((amount, subset, allocation))
    assert len(legal) == 19
    assert max(legal) == (
        3500,
        ("B", "D", "E"),
        {"txn_804219": 1500, "txn_804227": 2000},
    )
    assert sum(amount == 3500 for amount, _, _ in legal) == 1
    assert selected_amounts(offer, ["B", "E", "H"])[0] == 2900
    assert selected_amounts(offer, ["B", "D", "F"])[0] == 3400
    assert "offer_610735" not in get_environment().tools.db.reward_resolution_offers
