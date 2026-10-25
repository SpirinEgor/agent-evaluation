"""Compatibility entry point for the maintained Bank40 two-basis migration.

The former one-refund recipe is obsolete and must never restore old data.
"""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from upgrade_bank40_two_basis import migrate

DATA = Path(__file__).resolve().parents[2] / "data/tau2/domains/banking_ru"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory(prefix="bank40-migration-check-") as temporary:
            snapshot = Path(temporary) / "banking_ru"
            shutil.copytree(args.data_dir, snapshot)
            migrate(snapshot)
            for name in [
                "tasks.json",
                "db.json",
                "intake_answers.json",
                "manifest.json",
            ]:
                before = json.loads((args.data_dir / name).read_text())
                after = json.loads((snapshot / name).read_text())
                if before != after:
                    raise SystemExit(f"Bank40 two-basis migration stale: {name}")
    else:
        migrate(args.data_dir)
    print("Bank40 two-basis migration current; common sources rebuilt separately")


if __name__ == "__main__":
    main()
