"""Preserve committed historical run records against the CI base commit."""

import argparse
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNS = "docs/experiments/runs/"
PREVIOUS_RUNS = "docs/banking-ru/runs/"


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def check_history(base):
    names = git("ls-tree", "-r", "--name-only", "-z", base, "--", RUNS, PREVIOUS_RUNS)
    for name in filter(None, names.decode().split("\0")):
        expected = git("show", f"{base}:{name}")
        if name.startswith(PREVIOUS_RUNS):
            name = RUNS + name.removeprefix(PREVIOUS_RUNS)
        path = ROOT / name
        if not path.is_file() or path.read_bytes() != expected:
            raise ValueError(f"Historical run record changed: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Compare records against this Git ref")
    args = parser.parse_args()
    check_history(args.base)


if __name__ == "__main__":
    main()
