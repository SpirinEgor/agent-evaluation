import pytest

pytest.importorskip("mcp")


def test_platform_package_and_constants_exist():
    import tau2.platform  # noqa: F401
    from tau2.config import PLATFORM_DATA_DIR, PLATFORM_PORT

    assert PLATFORM_PORT == 8010
    assert PLATFORM_DATA_DIR.name == "platform"
