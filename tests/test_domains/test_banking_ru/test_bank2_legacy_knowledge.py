"""Whole-grader controls for changing presentation, not the Bank2 business task."""

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
def legacy_data(tmp_path_factory):
    from upgrade_bank2_legacy_knowledge import build

    parent = os.environ.get("BANK2_LEGACY_PARENT")
    if not parent:
        pytest.skip("Explicit frozen financial/workflow parent required")
    dest = tmp_path_factory.mktemp("legacy") / "data"
    build(Path(parent), dest)
    return dest


@pytest.mark.parametrize(
    "route,want",
    [
        ("reference", 1),
        ("alternative", 1),
        ("document_promotion_alternative", 1),
        ("search_sources", 1),
        ("search_missing_reissue_rule", 0),
        ("noop", 0),
        ("omit_rate_source", 0),
    ],
)
def test_whole_legacy_knowledge(legacy_data, monkeypatch, route, want, tmp_path):
    from tau2.domains.banking_ru import environment, file_pilot, utils
    from tau2.platform.session import PlatformSession
    from tests.utils.banking_full_grade import grade_full_session

    for module in [environment, file_pilot, utils]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", legacy_data),
            ("BANKING_RU_DB_PATH", legacy_data / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", legacy_data / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    task = next(t for t in environment.get_tasks() if t.id == "Bank 2")
    session = PlatformSession(
        task, environment.get_environment, max_calls=100, max_errors=5
    )
    mapping = json.loads((legacy_data / "presentation-map.json").read_text())
    actions = list(task.evaluation_criteria.actions)
    if route == "alternative":
        # Reverse independent reads within each consecutive source block.
        start = 0
        while start < len(actions):
            end = start
            while end < len(actions) and actions[end].name == "file_read":
                end += 1
            if end > start:
                actions[start:end] = reversed(actions[start:end])
                start = end
            else:
                start += 1
    for a in [] if route == "noop" else actions:
        args = deepcopy(a.arguments)
        if route == "document_promotion_alternative" and a.name == "get_promotions":
            continue
        if (
            route in ["search_sources", "search_missing_reissue_rule"]
            and a.name == "file_read"
            and args["path"]
            in {
                mapping["paths"]["/knowledge/" + x + ".md"]
                for x in [
                    "reward_product_scope",
                    "reward_posting_period",
                    "reward_card_reissue",
                ]
            }
        ):
            pattern = (
                "^#"
                if route == "search_missing_reissue_rule"
                and args["path"]
                == mapping["paths"]["/knowledge/reward_card_reissue.md"]
                else ".*"
            )
            result = session.call(
                "file_search", dict(pattern=pattern, root=args["path"], limit=200)
            )
            assert not result.is_error
            continue
        if (
            route == "omit_rate_source"
            and a.name == "file_read"
            and args["path"] == mapping["paths"]["/knowledge/reward_rate_history.md"]
        ):
            continue
        if route == "alternative" and a.name == "deliver_case_document":
            args.pop("expected_version", None)
            args["idempotency_key"] = "another-allowed-key"
        if route == "alternative" and a.name == "get_case_documents":
            result = session.call("get_documents", dict(customer_id="gromov_a_1187"))
        else:
            result = session.call(a.name, args)
        assert not result.is_error, (a.action_id, result)
        if a.name == "file_read":
            assert set(json.loads(result.content[0].text)) == {"id", "title", "content"}
    session.call("done", {})
    half = session.grade()
    full = grade_full_session(session).reward_info
    out = Path(os.environ.get("BANK2_LEGACY_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / (route + ".json.gz"), "wt") as f:
        json.dump(
            dict(
                half=half.model_dump(mode="json"),
                full=full.model_dump(mode="json"),
                simulation=session.to_simulation_run().model_dump(mode="json"),
            ),
            f,
            ensure_ascii=False,
        )
    assert half.reward == full.reward == want, (half.reward, full.reward)
