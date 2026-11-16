"""Bank 31 must identify the customer named in its opening through the wire API."""

import json
import re
from dataclasses import asdict

import httpx

from tests.test_platform import test_platform_sdk as fixture


def test_bank31_opening_matches_crm_and_reference():
    from platform_harness.server import ON_ACTION_PATH, ON_START_PATH, WireService

    task = next(t for t in fixture.collect() if t.id == "Bank 31")
    bench = fixture.benchmark(task)
    journal = []
    with fixture.wire(WireService(bench, contract_version=2)) as url:
        with httpx.Client(base_url=url, timeout=60) as client:
            start = client.post(ON_START_PATH)
            start.raise_for_status()
            opening = start.json()["opening"]
            assert "Виктор Титов" in opening
            phone = re.search(r"\+7[\d ()-]+", opening)[0].rstrip(". ")
            plan = fixture.path(task)
            content = None
            found = None
            verified = False
            while True:
                try:
                    action = plan.send(content)
                except StopIteration:
                    break
                inner = action.arguments.get("arguments", {})
                tool = action.arguments.get("tool_name")
                if action.name == "bank_call" and tool == "find_customer":
                    # Take the search input from the actual agent-visible ticket.
                    assert set(inner) == {"phone"}
                    inner["phone"] = phone
                if action.name == "bank_call" and tool == "verify_identity":
                    assert found is not None
                    assert inner["customer_id"] == found["id"]
                response = client.post(
                    ON_ACTION_PATH,
                    json={"action": {"kind": "tool_call", **asdict(action)}},
                )
                response.raise_for_status()
                content = response.json()["content"]
                if action.name == "bank_call" and tool == "find_customer":
                    rows = json.JSONDecoder().raw_decode(content)[0]
                    assert len(rows) == 1
                    found = rows[0]
                    assert found == {
                        "id": "titov_v_8890",
                        "full_name": "Титов Виктор Николаевич",
                    }
                if action.name == "bank_call" and tool == "verify_identity":
                    assert "подтверждена" in content
                    verified = True
                journal.append(
                    fixture.sdk.ActionRecord(
                        len(journal) + 1, len(journal) + 1, action, content
                    )
                )
    assert verified
    result, _, _ = fixture.grade(task, bench, journal)
    assert result["DB"] == result["ACTION"] == 1
    assert bench.session.errors == 0
