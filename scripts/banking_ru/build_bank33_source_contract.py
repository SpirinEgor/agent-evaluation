"""Declare the two equivalent complete Bank33 closure procedure receipts."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"
PATHS = ("/knowledge/kb_128.md", "/policies/accounts.md")


def with_contract(tasks):
    """Change only Bank33 evidence choices; preserve all dependency bounds."""
    updated = deepcopy(tasks)
    selected = [t for t in updated if t["id"] == "Bank 33"]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Bank 33")
    notes = selected[0]["description"].get("notes", "")
    marker = " Эквивалентные полные источники процедуры: kb_128 или /policies/accounts.md; точные содержание, SHA256 и получение до первой защищённой попытки обязательны."
    if marker not in notes:
        selected[0]["description"]["notes"] = notes + marker
    evidence = selected[0]["evaluation_criteria"]["result_evidence"]
    sources = {}
    for item in evidence:
        path = item["expected_fields"].get("path")
        if path in PATHS:
            candidate = {
                k: deepcopy(item[k])
                for k in ("tool_names", "record_id", "expected_fields")
            }
            if path in sources and sources[path] != candidate:
                raise ValueError(f"Inconsistent receipt for {path}")
            sources[path] = {
                k: deepcopy(item[k])
                for k in ("tool_names", "record_id", "expected_fields")
            }
    if set(sources) != set(PATHS):
        raise ValueError("Missing exact closure sources")
    for item in evidence:
        path = item["expected_fields"].get("path")
        if path in PATHS:
            item["alternatives"] = [
                deepcopy(sources[next(p for p in PATHS if p != path)])
            ]
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=DATA)
    args = parser.parse_args()
    path = args.data_dir / "tasks.json"
    before = json.loads(path.read_text())
    after = with_contract(before)
    if before != after:
        if args.check:
            raise SystemExit("Bank33 source alternatives stale")
        path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n")
    print("Bank33 exact source alternatives current")


if __name__ == "__main__":
    main()
