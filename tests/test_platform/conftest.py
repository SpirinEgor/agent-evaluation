import asyncio
from pathlib import Path

import pytest

pytest.importorskip("mcp")

import httpx  # noqa: E402

from tau2.platform.service import PlatformSettings, build_app  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> PlatformSettings:
    return PlatformSettings(
        domain="banking_ru",
        split="base",
        reveal_checks=True,
        results_dir=tmp_path,
        mcp_layout="single",
    )


@pytest.fixture
def app(settings):
    return build_app(settings)


def run_with_app(app, coro_factory):
    """Запустить корутину против приложения без сокетов: lifespan входим вручную,
    HTTP идёт через ASGITransport. Возвращает результат корутины."""

    async def _run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://platform",
                timeout=30,
            ) as http:
                return await coro_factory(http)

    return asyncio.run(_run())
