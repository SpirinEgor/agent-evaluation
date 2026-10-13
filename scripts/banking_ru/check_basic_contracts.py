"""Read-only checks mandated by the banking author/auditor contract."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDERS = (
    "confidentiality",
    "access",
    "identity",
    "discovery",
    "pending_limit_messages",
    "numeric_limit_messages",
    "card_outcome_messages",
)


def main():
    failed = []
    for name in BUILDERS:
        path = f"scripts/banking_ru/build_{name}_contract.py"
        result = subprocess.run(
            [sys.executable, path, "--check"], cwd=ROOT, check=False
        )
        if result.returncode:
            failed.append(path)
    if failed:
        print("Failed checks: " + ", ".join(failed), file=sys.stderr)
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
