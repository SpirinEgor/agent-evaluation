import hashlib
import json
import sys
from pathlib import Path

from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession
from tests.utils.banking_full_grade import grade_full_session

archive = Path(sys.argv[1])
output = Path(sys.argv[2])
raw = json.loads(archive.read_text())
calls = [c for m in raw["messages"] for c in m.get("tool_calls") or []]
task = next(t for t in get_tasks() if t.id == "Bank 40")
s = PlatformSession(task, get_environment, max_calls=250, max_errors=100)
for c in calls:
    s.call(c["name"], c["arguments"])
if not any(c["name"] == "done" for c in calls):
    s.call("done", {})
half = s.grade()
full = grade_full_session(s).reward_info
payload = {
    "archive": str(archive),
    "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "arguments_translated": False,
    "call_count": len(calls),
    "errors": s.errors,
    "half": half.model_dump(mode="json"),
    "full": full.model_dump(mode="json"),
}
output.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
print(
    json.dumps(
        {
            "half": half.final_reward,
            "full": full.final_reward,
            "errors": s.errors,
            "calls": len(calls),
        }
    )
)
assert half.final_reward == full.final_reward == 1
