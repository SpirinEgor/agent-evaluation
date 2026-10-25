"""Addressed whole-grader controls for the approved client workflow prototype."""

import gzip
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/banking_ru"))


@pytest.fixture(scope="session")
def candidate(tmp_path_factory):
    parent = os.environ.get("BANK2_DISCOVERY_PARENT")
    if not parent:
        pytest.skip("Frozen prototype parent required")
    from upgrade_bank2_discovery_workflow import build

    dest = tmp_path_factory.mktemp("discovery") / "data"
    build(Path(parent), dest)
    return dest


@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("corpus_alternative", 1),
        ("early_consent_renewed", 1),
        ("noop", 0),
        ("skip_bank_acceptance", 0),
        ("unnecessary_then_recover", 0),
        ("skip_client_action", 0),
        ("stale_consent", 0),
        ("late_category", 0),
        ("late_contract", 0),
        ("wrongcase_then_recover", 0),
    ],
)
def test_whole_discovery(candidate, monkeypatch, route, want, tmp_path):
    from tau2.domains.banking_ru import environment, file_pilot, utils
    from tau2.domains.banking_ru.discovery_workflow import SPECIAL_TOOLS
    from tau2.platform.session import PlatformSession
    from tests.utils.banking_full_grade import grade_full_session

    for module in [environment, file_pilot, utils]:
        for field, value in [
            ("BANKING_RU_DATA_DIR", candidate),
            ("BANKING_RU_DB_PATH", candidate / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", candidate / "tasks.json"),
        ]:
            if hasattr(module, field):
                monkeypatch.setattr(module, field, value)
    task = next(t for t in environment.get_tasks() if t.id == "Bank 2")
    s = PlatformSession(task, environment.get_environment, max_calls=180, max_errors=10)
    unlocked = set()
    early = None
    fresh_consent = None
    late = []

    def call(name, args):
        if name in SPECIAL_TOOLS:
            if name not in unlocked:
                r = s.call("unlock_discoverable_agent_tool", dict(name=name))
                assert not r.is_error
                unlocked.add(name)
            return s.call(
                "call_discoverable_agent_tool", dict(name=name, arguments=args)
            )
        return s.call(name, args)

    for a in [] if route == "noop" else task.evaluation_criteria.actions:
        args = deepcopy(a.arguments)
        if route in ["late_category", "late_contract"] and a.action_id in (
            [
                "bank2:v4:purchases",
                "bank2:v4:related",
            ]
            if route == "late_category"
            else ["bank2:v4:contract"]
        ):
            late.append(a)
            continue
        if (
            route in ["late_category", "late_contract"]
            and a.action_id == "bank2:client:register"
        ):
            for delayed in late:
                assert not call(delayed.name, deepcopy(delayed.arguments)).is_error
        if route == "skip_bank_acceptance" and a.name == "get_reward_statement_status":
            continue
        if route == "skip_client_action" and a.action_id in [
            "bank2:client:submit",
            "bank2:client:register",
            "bank2:client:accepted",
        ]:
            continue
        if (
            route in ["unnecessary_then_recover", "wrongcase_then_recover"]
            and a.action_id == "bank2:client:submit"
        ):
            wrong = deepcopy(args)
            wrong["request"]["arguments"]["transaction_ids"] = (
                ["txn_804219"]
                if route == "unnecessary_then_recover"
                else ["txn_804227"]
            )
            if route == "wrongcase_then_recover":
                wrong["request"]["arguments"]["case_id"] = "case-standard-other-period"
            assert call(a.name, wrong).is_error
        if (
            route in ["early_consent_renewed", "stale_consent"]
            and a.name == "refresh_cashback_settlement"
        ):
            consent = next(
                x
                for x in task.evaluation_criteria.actions
                if x.action_id == "bank2:v5:consent"
            )
            old = deepcopy(consent.arguments)
            old["request"]["state_revision"] = 1
            for item in old["request"]["resulting_claims"]:
                if item["transaction_id"] == "txn_804227":
                    item["amount"] += 7
            for observed in task.evaluation_criteria.actions:
                if observed.name in [
                    "file_read",
                    "get_reward_resolution_offers",
                    "get_reward_resolution_components",
                ]:
                    assert not call(
                        observed.name, deepcopy(observed.arguments)
                    ).is_error
            early = call("ask_client", old)
            assert not early.is_error
        if route == "stale_consent" and a.action_id == "bank2:v5:consent":
            continue
        if route == "early_consent_renewed" and a.name == "execute_reward_resolution":
            args["confirmation_id"] = fresh_consent
        if route == "stale_consent" and a.name == "execute_reward_resolution":
            args["confirmation_id"] = json.loads(early.content[0].text)[
                "confirmation_id"
            ]
        if route == "corpus_alternative" and a.name == "file_read":
            result = call("corpus_command", dict(command="cat " + args["path"]))
        else:
            result = call(a.name, args)
        if a.action_id == "bank2:v5:consent":
            fresh_consent = json.loads(result.content[0].text).get("confirmation_id")
        if result.is_error:
            assert route in [
                "skip_bank_acceptance",
                "skip_client_action",
                "stale_consent",
            ], (a.action_id, result)
            break
    s.call("done", {})
    half = s.grade()
    full = grade_full_session(s).reward_info
    out = Path(os.environ.get("BANK2_DISCOVERY_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / (route + ".json.gz"), "wt") as f:
        json.dump(
            dict(
                half=half.model_dump(mode="json"),
                full=full.model_dump(mode="json"),
                simulation=s.to_simulation_run().model_dump(mode="json"),
                mcp_events=s.mcp_events,
            ),
            f,
            ensure_ascii=False,
        )
    assert half.reward == full.reward == want, (half.reward, full.reward)
