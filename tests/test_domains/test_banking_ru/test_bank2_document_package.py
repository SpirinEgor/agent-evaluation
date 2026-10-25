"""Targeted prototype controls; pass an explicit already-validated financial parent."""

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
def packages(tmp_path_factory):
    parent = os.environ.get("BANK2_PACKAGE_PARENT")
    if not parent:
        pytest.skip("Author prototype requires explicit validated financial parent")
    from upgrade_bank2_document_packages import build

    out = tmp_path_factory.mktemp("packages")
    return {
        variant: build(Path(parent), out / variant, variant)
        for variant in os.environ.get(
            "BANK2_PACKAGE_VARIANTS", "email,fallback,unknown"
        ).split(",")
    }


@pytest.mark.parametrize(
    "variant,route,want",
    [
        ("email", "split", 1),
        ("email", "stale_between_splits", 0),
        ("email", "reference", 1),
        ("email", "alternative", 1),
        ("email", "noop", 0),
        ("email", "omit_metadata_alternate_key", 0),
        ("email", "wrong_contact", 0),
        ("email", "omit_delivery", 0),
        ("email", "stale_document", 0),
        ("email", "internal_document", 0),
        ("fallback", "skip_confirmation", 0),
        ("fallback", "reference", 1),
        ("fallback", "alternative", 1),
        ("fallback", "unverified_contact", 0),
        ("unknown", "split", 1),
        ("unknown", "reference", 1),
        ("unknown", "safe_repeat", 1),
        ("unknown", "unreceived_outcome", 0),
    ],
)
def test_document_packages(packages, monkeypatch, variant, route, want, tmp_path):
    from upgrade_bank2_document_packages import CASE, DOC, NEW, OLD, C

    from tau2.domains.banking_ru import environment, file_pilot, utils
    from tau2.platform.session import PlatformSession
    from tests.utils.banking_full_grade import grade_full_session

    data = packages[variant]
    for module in [environment, file_pilot, utils]:
        for name, value in [
            ("BANKING_RU_DATA_DIR", data),
            ("BANKING_RU_DB_PATH", data / "db.json"),
            ("BANKING_RU_TASK_SET_PATH", data / "tasks.json"),
        ]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    task = next(t for t in environment.get_tasks() if t.id == "Bank 2")
    session = PlatformSession(
        task, environment.get_environment, max_calls=100, max_errors=5
    )
    early = None
    last = None
    for action in [] if route == "noop" else task.evaluation_criteria.actions:
        args = deepcopy(action.arguments)
        if action.action_id == "bank2:v4:amend" and route in [
            "split",
            "stale_between_splits",
        ]:
            first = deepcopy(args)
            first["items"] = [
                row for row in first["items"] if row["transaction_id"] != "txn_804227"
            ]
            result = session.call(action.name, first)
            assert not result.is_error
            args["expected_revision"] = int(
                json.loads(result.content[0].text)["state_revision"]
            )
            if route == "stale_between_splits":
                assert not session.call(
                    "get_case_documents", dict(case_id=CASE)
                ).is_error
        if (
            route in ["split", "stale_between_splits"]
            and action.name == "deliver_case_document"
        ):
            args.pop("expected_version", None)
        if (
            route == "stale_between_splits"
            and action.action_id == "bank2:package:metadata"
        ):
            continue
        if (
            route in ["alternative", "omit_metadata_alternate_key"]
            and action.name == "deliver_case_document"
        ):
            args.pop("expected_version", None)
            args["idempotency_key"] = "alternative-package"
        if route == "skip_confirmation" and action.action_id == "bank2:package:confirm":
            continue
        if (
            route == "omit_metadata_alternate_key"
            and action.action_id == "bank2:package:metadata"
        ):
            continue
        if action.action_id == "bank2:v4:amend" and route == "stale_document":
            early = session.call("get_case_documents", dict(case_id=CASE))
            assert not early.is_error
        if route == "omit_delivery" and action.action_id in [
            "bank2:package:deliver",
            "bank2:package:receipt",
        ]:
            continue
        if (
            route == "unreceived_outcome"
            and action.action_id == "bank2:package:receipt"
        ):
            continue
        if route == "safe_repeat" and action.action_id == "bank2:package:receipt":
            args = dict(
                document_id=DOC,
                contact_id=NEW,
                idempotency_key="bank2-package",
                expected_version="2",
            )
            last = session.call("deliver_case_document", args)
            assert not last.is_error
            continue
        if route == "alternative" and action.action_id == "bank2:package:metadata":
            last = session.call("get_documents", dict(customer_id=C))
            assert not last.is_error
            continue
        if action.action_id == "bank2:package:deliver":
            if route == "stale_document":
                args.update(document_id=DOC + ":version:1", expected_version="1")
            if route == "internal_document":
                args.update(
                    document_id=CASE + ":internal_diagnostics", expected_version="1"
                )
            if route == "unverified_contact":
                args["contact_id"] = NEW
            if route == "wrong_contact":
                args["contact_id"] = OLD
            last = session.call(action.name, args)
            if route in ["stale_document", "internal_document", "unverified_contact"]:
                assert last.is_error
                break
        else:
            if route == "wrong_contact" and action.action_id == "bank2:package:receipt":
                args["contact_id"] = OLD
            last = session.call(action.name, args)
        assert not last.is_error, (action.action_id, last)
    session.call("done", {})
    half, full = session.grade(), grade_full_session(session).reward_info
    out = Path(os.environ.get("BANK2_PACKAGE_EVIDENCE", str(tmp_path)))
    out.mkdir(parents=True, exist_ok=True)
    with gzip.open(out / (variant + "-" + route + ".json.gz"), "wt") as stream:
        json.dump(
            dict(
                half=half.model_dump(mode="json"),
                full=full.model_dump(mode="json"),
                simulation=session.to_simulation_run().model_dump(mode="json"),
                errors=session.errors,
            ),
            stream,
            ensure_ascii=False,
        )
    assert half.reward == full.reward == want, (half.reward, full.reward)
    if want:
        assert session.env.tools.db.cases[CASE].amount == 27.25
        receipts = list(session.env.tools.db.document_deliveries.values())
        assert len(receipts) == 1
        assert receipts[0].contact_id == (OLD if variant == "fallback" else NEW)
        assert receipts[0].version == ("3" if route == "split" else "2")
