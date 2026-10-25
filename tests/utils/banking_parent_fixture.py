"""Author-only restoration of an explicit historical Bank2 dataset."""

import hashlib
import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/banking_ru"


def verify_fixture(path):
    """Fail before use if any pinned file changed or a runtime input is missing."""
    hashes = json.loads((path / "integrity.json").read_text())
    for name, expected in hashes.items():
        source = path / name
        if (
            not source.is_file()
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected
        ):
            raise ValueError(f"Historical fixture hash mismatch: {name}")


def copy_historical_data(name, destination):
    """Copy versioned inputs without opening the current benchmark dataset."""
    source = FIXTURES / name
    verify_fixture(source)
    shutil.copytree(source, destination)
    return destination


def read_parent(path):
    """Load a pinned subject overlay only after checking its original bytes."""
    verify_fixture(path)
    return json.loads((path / "parent.json").read_text())


def restore_bank2_v5(data):
    root = Path(__file__).resolve().parents[2]
    parent = read_parent(FIXTURES / "bank2_resolution_v5_2026_10_20")
    db = json.loads((data / "db.json").read_text())
    customer = "gromov_a_1187"
    cards = {k for k, v in db["cards"].items() if v["customer_id"] == customer}
    cards.update(parent["subject_db"]["cards"])
    for name, rows in list(db.items()):
        if not isinstance(rows, dict):
            continue
        db[name] = {
            k: v
            for k, v in rows.items()
            if not (
                k == customer
                or name == "cashback_state_revisions"
                and k.startswith(customer + ":")
                or name == "card_limits"
                and k in cards
                or isinstance(v, dict)
                and v.get("customer_id") == customer
            )
        }
    for name, rows in parent["subject_db"].items():
        db.setdefault(name, {}).update(deepcopy(rows))
    supplement = json.loads(
        (FIXTURES / "bank2_resolution_v5_2026_10_20/db-supplement.json").read_text()
    )
    for name, rows in supplement.items():
        db.setdefault(name, {}).update(deepcopy(rows))
    tasks = json.loads((data / "tasks.json").read_text())
    tasks = [deepcopy(parent["task"]) if t["id"] == "Bank 2" else t for t in tasks]
    answers = json.loads((data / "intake_answers.json").read_text())
    answers[customer] = parent["answers"]
    manifest = json.loads((data / "manifest.json").read_text())
    pinned = FIXTURES / "bank2_resolution_v5_2026_10_20"
    descriptors = json.loads((pinned / "source-descriptors.json").read_text())
    for path, descriptor in descriptors.items():
        target = data / "corpus" / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pinned / "corpus" / path.lstrip("/"), target)
        manifest["documents"][path] = descriptor
    for path, body in parent["source_files"].items():
        (data / "corpus" / path.lstrip("/")).write_text(body)
        manifest["documents"][path] = parent["source_descriptors"][path]
    sys.path.insert(0, str(root / "scripts/banking_ru"))
    from build_access_contract import with_contract as access
    from build_discovery_contract import with_contract as discovery
    from upgrade_bank2_multisource import sync_manifest

    access(tasks, db)
    discovery(tasks, db)
    for name, obj in [
        ("db.json", db),
        ("tasks.json", tasks),
        ("intake_answers.json", answers),
        ("manifest.json", manifest),
    ]:
        (data / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    sync_manifest(data)
