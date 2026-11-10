"""Build a standalone SDK source bundle; never run a task or call a model."""

import argparse
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

from tau2.platform.runtime_payloads import TOKEN, PayloadStore

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_COMMIT = "914007cb79f3e3d5f39626a0209d6c1a2b6d7794"
SDK_PATH = "platform/external_contracts/platform-sdk-contract"


def build_bundle(platform_repo: Path, output: Path, payload_store: Path | None = None):
    if output.exists():
        raise ValueError(
            "Output must be a new directory; existing artifacts are preserved"
        )
    data = ROOT / "data/tau2/domains/banking_ru"
    payload_ids = set()
    for path in data.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".md"}:
            payload_ids.update(TOKEN.findall(path.read_text()))
    if payload_ids:
        if payload_store is None:
            raise ValueError("Private runtime payload store is required")
        if output.resolve().is_relative_to(ROOT):
            raise ValueError(
                "Bundle with private payloads must be outside the developer repository"
            )
        store = PayloadStore(payload_store)
        # Validate everything before creating the bundle. No contents in errors.
        for key in payload_ids:
            store.get(key)
    sdk = subprocess.run(
        ["git", "archive", PLATFORM_COMMIT, SDK_PATH],
        cwd=platform_repo,
        check=True,
        capture_output=True,
    ).stdout
    requirements = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--extra",
            "platform",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    output.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in (
        "benchmark.yaml",
        "runtime.yaml",
        "Dockerfile",
        "platform_benchmark.py",
        "platform_tasks.py",
        "platform_evaluator.py",
    ):
        shutil.copy2(ROOT / "integrations/eval_platform" / name, output / name)
    (output / "requirements.lock").write_bytes(requirements)
    with tarfile.open(fileobj=io.BytesIO(sdk)) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            target = (
                output
                / "vendor/platform-sdk-contract"
                / Path(member.name).relative_to(SDK_PATH)
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    # Execution carries the banking runtime, not gold, collectors or grading code.
    source = ROOT / "src/tau2"
    run_src = output / "execute/src/tau2"
    shutil.copytree(source, run_src, ignore=ignore)
    shutil.rmtree(run_src / "evaluator")
    for name in ("tasks.py", "evaluator.py"):
        (run_src / "platform/sdk" / name).unlink()
    # The upstream convenience __init__ eagerly imports graders. Internal modules
    # need no such exports in the execution image; source repo API stays intact.
    (run_src / "__init__.py").write_text('"""Banking SDK execution package."""\n')
    run_data = output / "execute/data/tau2/domains/banking_ru"
    run_data.mkdir(parents=True)
    for name in ("db.json", "manifest.json", "intake_answers.json"):
        shutil.copy2(data / name, run_data / name)
    shutil.copytree(data / "corpus", run_data / "corpus", ignore=ignore)
    shutil.copytree(source, output / "evaluate/src/tau2", ignore=ignore)
    shutil.copytree(
        data, output / "evaluate/data/tau2/domains/banking_ru", ignore=ignore
    )
    private = output / "private_payloads"
    private.mkdir(mode=0o700)
    for key in payload_ids:
        # get() validated regular files, UTF-8 and content hashes above.
        (private / key).write_text(store.get(key))
        (private / key).chmod(0o600)
    (output / "platform-source.txt").write_text(PLATFORM_COMMIT + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--payload-store", type=Path)
    args = parser.parse_args()
    build_bundle(args.platform_repo, args.output, args.payload_store)
    print(f"Banking SDK bundle: {args.output}")


if __name__ == "__main__":
    main()
