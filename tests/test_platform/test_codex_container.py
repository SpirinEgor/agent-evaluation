"""Banking Codex runs may never mount host paths into the model container."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts/banking_ru/codex_container.py"


def load_module():
    spec = importlib.util.spec_from_file_location("codex_container", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_command_has_no_host_mount_or_host_namespace():
    command = load_module().create_command("sha256:image", "banking-one")
    assert command[:2] == ["docker", "create"]
    assert "--cap-drop" in command and command[command.index("--cap-drop") + 1] == "ALL"
    assert "--privileged" not in command
    assert not {"--volume", "-v", "--network=host", "--pid=host", "--ipc=host"} & set(
        command
    )
    assert not any("type=bind" in arg or "docker.sock" in arg for arg in command)


def test_inspection_rejects_mounts_even_if_named_not_bind():
    module = load_module()
    base = {
        "Mounts": [],
        "HostConfig": {
            "Privileged": False,
            "NetworkMode": "bridge",
            "PidMode": "",
            "IpcMode": "private",
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
        },
    }
    module.validate_inspection(base)
    import pytest

    for mount in [
        {"Type": "bind", "Source": "/Users"},
        {"Type": "volume", "Source": "wiki"},
    ]:
        with pytest.raises(ValueError):
            module.validate_inspection({**base, "Mounts": [mount]})
    with pytest.raises(ValueError):
        module.validate_inspection(
            {**base, "HostConfig": {**base["HostConfig"], "Privileged": True}}
        )


def test_file_delivery_reaches_running_tmpfs_when_image_is_available():
    import os
    import subprocess
    import uuid

    import pytest

    image = os.getenv("BANKING_CODEX_TEST_IMAGE")
    if not image:
        pytest.skip("Explicit local Docker test image required; no model calls")
    module = load_module()
    name = "banking-copy-test-" + uuid.uuid4().hex
    try:
        subprocess.run(
            module.create_command(image, name), check=True, capture_output=True
        )
        subprocess.run(["docker", "start", name], check=True, capture_output=True)
        module.copy_bytes(name, "/workspace/canary.txt", b"runtime-file-visible")
        result = subprocess.run(
            [
                "docker",
                "exec",
                name,
                "node",
                "-e",
                "process.stdout.write(require('fs').readFileSync('/workspace/canary.txt'))",
            ],
            capture_output=True,
            check=True,
        )
        assert result.stdout == b"runtime-file-visible"
    finally:
        module.remove_container(name)
