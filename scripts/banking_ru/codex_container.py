"""Externally isolate full-access Codex; never expose host files to apply_patch."""

import json
import subprocess
from pathlib import Path

DEFAULT_IMAGE = "banking-codex-isolated:0.162.0-alpha.17.2"


def create_command(image: str, name: str) -> list[str]:
    """Create a fresh container with only empty volatile mounts."""
    return [
        "docker",
        "create",
        "--name",
        name,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "256",
        "--memory",
        "3g",
        "--cpus",
        "2",
        "--network",
        "bridge",
        "--tmpfs",
        "/root/.codex:rw,noexec,nosuid,size=128m",
        "--tmpfs",
        "/workspace:rw,nosuid,size=128m",
        "--tmpfs",
        "/tmp:rw,nosuid,size=256m",
        image,
        "sleep",
        "infinity",
    ]


def validate_inspection(inspected: dict) -> None:
    """Fail closed on a host mount, privileged mode, or shared host namespace."""
    if any(m.get("Type") != "tmpfs" for m in inspected.get("Mounts", [])):
        raise ValueError("Model container must have no host or volume mounts")
    config = inspected["HostConfig"]
    if config.get("Privileged") or config.get("NetworkMode") != "bridge":
        raise ValueError("Privileged/host-network model container is forbidden")
    if config.get("PidMode") == "host" or config.get("IpcMode") == "host":
        raise ValueError("Host PID/IPC namespace is forbidden")
    if "ALL" not in config.get("CapDrop", []):
        raise ValueError("All container capabilities must be dropped")
    if not any(
        s.startswith("no-new-privileges") for s in config.get("SecurityOpt", [])
    ):
        raise ValueError("no-new-privileges must be enabled")


def inspect_container(name: str) -> dict:
    """Return and validate Docker's effective isolation state."""
    result = subprocess.run(
        ["docker", "inspect", name], check=True, capture_output=True, text=True
    )
    inspected = json.loads(result.stdout)[0]
    validate_inspection(inspected)
    return inspected


def copy_bytes(name: str, destination: str, content: bytes) -> None:
    """Copy one explicit runtime file without a host mount or secret argv."""
    # docker cp targets the backing filesystem and is not a reliable write into
    # a running tmpfs. Write through a process in the container's mount namespace.
    subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            name,
            "node",
            "-e",
            "const fs=require('fs');fs.writeFileSync(process.argv[1],fs.readFileSync(0),{mode:0o600});",
            destination,
        ],
        input=content,
        check=True,
        capture_output=True,
    )


def prepare_container(image: str, name: str, policy: str) -> dict:
    """Start one isolated CLI runtime, copying only auth and bank startup."""
    subprocess.run(create_command(image, name), check=True, capture_output=True)
    try:
        inspected = inspect_container(name)
        subprocess.run(["docker", "start", name], check=True, capture_output=True)
        # Auth is a runtime credential copy in volatile storage, never an image
        # layer, host mount, model prompt, command argument or saved artifact.
        copy_bytes(
            name,
            "/root/.codex/auth.json",
            (Path.home() / ".codex/auth.json").read_bytes(),
        )
        copy_bytes(name, "/workspace/AGENTS.md", policy.encode())
        return inspected
    except Exception:
        remove_container(name)
        raise


def remove_container(name: str) -> None:
    """Remove only this attempt's container, including volatile auth storage."""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
