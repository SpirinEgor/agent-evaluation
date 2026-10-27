"""Offline, read-only banking documentation and repository structure checks.

Only Python's standard library is required. Does not import the runtime, run
trajectories, contact services, or certify task validity or model isolation.
"""

import ast
import json
import re
import runpy
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
DATA = Path("data/tau2/domains/banking_ru")
DOC_FILES = (
    "AGENTS.md",
    "README.md",
    "docs/banking-ru.md",
    "data/tau2/domains/banking_ru/README.md",
    "src/tau2/domains/AGENTS.md",
    "src/tau2/evaluator/AGENTS.md",
    "tests/AGENTS.md",
    "tests/fixtures/banking_ru/README.md",
)
LINK = re.compile(r"!?\[[^\]\n]*\]\(")


def inline_links(line):
    """Extract inline destinations, including balanced/escaped parentheses."""
    for match in LINK.finditer(line):
        tail = line[match.end() :]
        if tail.startswith("<"):
            end = tail.find(">")
            if end >= 0:
                yield tail[1:end]
            continue
        depth, escaped, target = 1, False, []
        for char in tail:
            if escaped:
                target.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char.isspace() and depth == 1:
                break  # Optional title follows the destination.
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            target.append(char)
        if target:
            yield "".join(target)


def prose(text):
    """Ignore fenced examples; their paths need not exist in the repository."""
    fence = None
    for line in text.splitlines():
        marker = re.match(r"\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            yield ""
            continue
        yield line if fence is None else ""


def anchors(text):
    found = set()
    for line in prose(text):
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if not heading:
            continue
        title = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", heading[1]).lower()
        slug = "".join(
            c for c in title if c in "-_ " or unicodedata.category(c)[0] in "LN"
        ).replace(" ", "-")
        candidate, count = slug, 0
        while candidate in found:
            count += 1
            candidate = f"{slug}-{count}"
        found.add(candidate)
    return found


def check_documents(root, paths):
    errors = []
    root = root.resolve()
    for path in paths:
        if not path.is_file():
            errors.append(f"Missing document: {path}")
            continue
        for number, line in enumerate(prose(path.read_text()), 1):
            # Inline code, like fenced code, can contain illustrative paths.
            line = re.sub(r"(`+).*?\1", "", line)
            for raw in inline_links(line):
                url = urlsplit(raw)
                if url.scheme or url.netloc:
                    continue  # Remote availability is deliberately not checked.
                target = (
                    (path.parent / unquote(url.path)).resolve()
                    if url.path
                    else path.resolve()
                )
                reason = None
                if Path(unquote(url.path)).is_absolute() or not target.is_relative_to(
                    root
                ):
                    reason = "nonportable link outside repository"
                elif not target.exists():
                    reason = "missing link target"
                elif url.fragment and target.suffix == ".md":
                    if unquote(url.fragment) not in anchors(target.read_text()):
                        reason = "missing heading anchor"
                if reason:
                    errors.append(f"{path}:{number}: {reason}: {raw}")
    return errors


def check_task_index(root):
    errors = []
    try:
        index_path = root / DATA / "tasks.json"
        index = json.loads(index_path.read_text())
        if not isinstance(index, dict) or set(index) != {"task_files"}:
            raise ValueError("Current tasks.json must be an ordered task_files index")
        loader = runpy.run_path(str(root / "src/tau2/domains/banking_ru/task_store.py"))
        tasks = loader["load_tasks"](index_path)
        listed = {root / DATA / name for name in index["task_files"]}
        extra = set((root / DATA / "tasks").glob("*.json")) - listed
        for path in sorted(extra):
            errors.append(f"Task file unlisted in index: {path}")
        if not tasks:
            errors.append("Task index is empty")
        return tasks, errors
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [], [f"Task index: {error}"]


def check_profiles(tasks, document):
    errors, migrated, legacy = [], set(), set()
    for task in tasks:
        criteria = task.get("evaluation_criteria") or {}
        basis = criteria.get("reward_basis")
        if (
            isinstance(basis, list)
            and len(basis) == 2
            and set(basis) == {"DB", "ACTION"}
        ):
            migrated.add(task["id"])
            valid = (
                criteria.get("banking_contract_version") == "db_action_v1"
                and criteria.get("action_reward_scope") == "full_contract"
                and criteria.get("require_successful_actions") is True
                and criteria.get("business_process_only") is True
                and criteria.get("completion_mode") == "completion_only"
                and not criteria.get("env_assertions")
            )
            if not valid:
                errors.append(f"{task['id']}: inconsistent db_action_v1 profile")
        elif (
            isinstance(basis, list)
            and len(basis) == 3
            and set(basis) == {"DB", "ENV_ASSERTION", "ACTION"}
            and not criteria.get("banking_contract_version")
        ):
            legacy.add(task["id"])
        else:
            errors.append(f"{task['id']}: undocumented evaluation profile: {basis}")
    described, legacy_count = set(), None
    for line in document.read_text().splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 4:
            continue
        if cells[2] == "DB × ACTION":
            described.update(re.findall(r"Bank \d+", cells[1]))
        if cells[2] == "DB × ENV_ASSERTION × ACTION":
            match = re.fullmatch(r"Остальные (\d+)", cells[1])
            if match:
                legacy_count = int(match[1])
    if described != migrated:
        errors.append(
            f"Evaluation docs/task mismatch: docs={sorted(described)}, tasks={sorted(migrated)}"
        )
    if legacy_count != len(legacy):
        errors.append(
            f"Legacy count mismatch: docs={legacy_count}, tasks={len(legacy)}"
        )
    return errors


def check_test_inputs(paths):
    """Flag literal forbidden input paths, not adversarial strings in test cases.

    This is a bounded static guard, not general data-flow or isolation proof.
    """
    errors = []
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        output_nodes = set()
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            name = (
                call.func.id
                if isinstance(call.func, ast.Name)
                else getattr(call.func, "attr", "")
            )
            if name in {"copytree", "copyfile"}:
                destinations = call.args[1:2] + [
                    kw.value for kw in call.keywords if kw.arg == "dst"
                ]
                output_nodes.update(
                    id(node) for dst in destinations for node in ast.walk(dst)
                )
        for node in ast.walk(tree):
            if id(node) in output_nodes:
                continue
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                args = [node]  # ROOT / "artifacts" / ... is also a literal path.
            elif isinstance(node, ast.Call):
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", "")
                )
                if name not in {
                    "Path",
                    "open",
                    "read_json",
                    "load_tasks",
                    "copytree",
                    "copyfile",
                }:
                    continue
                # Copy destinations are outputs, not fixture dependencies.
                args = node.args[:1]
            else:
                continue
            strings = [
                n.value
                for arg in args
                for n in ast.walk(arg)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            if any(
                re.search(r"(?:^|[/\\])(?:artifacts|my_wiki)(?:[/\\]|$)", value)
                for value in strings
            ):
                errors.append(
                    f"{path}:{node.lineno}: input path depends on artifacts/ or Wiki"
                )
    return errors


def main(root=ROOT):
    documents = [root / p for p in DOC_FILES] + sorted(
        (root / "docs/banking-ru").glob("*.md")
    )
    errors = check_documents(root, documents)
    tasks, issues = check_task_index(root)
    errors.extend(issues)
    evaluation = root / "docs/banking-ru/evaluation.md"
    if evaluation.is_file() and tasks:
        errors.extend(check_profiles(tasks, evaluation))
    # Loading this inventory defines groups only; its subprocess entry point is not run.
    inventory = runpy.run_path(str(root / "scripts/banking_ru/run_basic_tests.py"))
    inputs = {root / p for group in inventory["GROUPS"].values() for p in group}
    inputs.update((root / "tests/utils").glob("banking_*.py"))
    inputs.add(root / "tests/test_domains/test_banking_ru/conftest.py")
    for path in sorted(inputs):
        if not path.is_file():
            errors.append(f"Missing maintained test input: {path}")
    errors.extend(check_test_inputs(sorted(p for p in inputs if p.is_file())))
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    if errors:
        print(
            f"Banking repository check failed: {len(errors)} issue(s).", file=sys.stderr
        )
        return 1
    print(
        f"Banking repository OK: {len(documents)} documents, {len(tasks)} tasks, {len(inputs)} maintained test inputs."
    )
    print(
        "Read-only structural checks; no trajectories, models or external services invoked."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
