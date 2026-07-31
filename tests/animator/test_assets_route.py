#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the animator presentation SVG asset endpoints.

The routes are pure (no database), so the app needs only the assets router.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from animator.routes.assets import router as assets_router

pytestmark = pytest.mark.asyncio


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(assets_router)
    return app


async def _get(path: str) -> tuple[int, str, str]:
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    return response.status_code, response.headers.get("content-type", ""), response.text


@pytest.mark.parametrize(
    "path",
    [
        "/assets/balloon/00ff00",
        "/assets/balloon/00ff00/A",
        "/assets/star/00ff00",
        "/assets/star/00ff00/A",
        "/assets/balloon/0f0",  # 3-digit shorthand
        "/assets/medal/gold",
        "/assets/medal/silver",
        "/assets/medal/bronze",
    ],
)
async def test_valid_asset_is_cacheable_svg(path: str) -> None:
    status, content_type, body = await _get(path)
    assert status == 200
    assert content_type.startswith("image/svg+xml")
    assert body.lstrip().startswith("<svg")


async def test_letter_is_embedded_uppercase() -> None:
    _, _, body = await _get("/assets/balloon/00ff00/abc")
    # Only the first letter is drawn, uppercased.
    assert ">A</text>" in body


async def test_no_letter_variant_has_no_text() -> None:
    _, _, body = await _get("/assets/balloon/00ff00")
    assert "<text" not in body


@pytest.mark.parametrize(
    "path",
    [
        "/assets/balloon/zzzzzz",
        "/assets/balloon/00ff00/1",
        "/assets/star/nothex",
        "/assets/star/00ff00/_",
        "/assets/medal/platinum",
    ],
)
async def test_invalid_input_is_rejected(path: str) -> None:
    status, _, _ = await _get(path)
    assert status == 400
