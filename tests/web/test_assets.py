#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from web.config import settings
from web.routes.assets import router


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Return a client for the public generated-asset routes."""
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["balloon", "star"])
async def test_color_only_asset_has_no_letter(client: AsyncClient, asset: str) -> None:
    """The existing color-only routes preserve their unlabeled rendering."""
    response = await client.get(f"/assets/{asset}/0f0")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == (f"public, max-age={settings.IMAGE_RESPONSE_CACHE_MAX_AGE}")
    assert 'fill="#00ff00"' in response.text
    assert "<text" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset", "font_size"),
    [("balloon", "80"), ("star", "64")],
)
async def test_letter_asset_uses_first_uppercase_letter(
    client: AsyncClient,
    asset: str,
    font_size: str,
) -> None:
    """Letter routes uppercase and render only the first supplied letter."""
    response = await client.get(f"/assets/{asset}/ffffff/abc")

    assert response.status_code == 200
    assert f'font-size="{font_size}"' in response.text
    assert 'y="150"' in response.text
    assert 'fill="#000000"' in response.text
    assert ">A</text>" in response.text
    assert ">ABC</text>" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["balloon", "star"])
async def test_letter_asset_uses_white_for_dark_fill(client: AsyncClient, asset: str) -> None:
    """Dark assets use white text for maximum contrast."""
    response = await client.get(f"/assets/{asset}/000000/z")

    assert response.status_code == 200
    assert 'fill="#ffffff"' in response.text
    assert ">Z</text>" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["balloon", "star"])
async def test_letter_asset_uses_wcag_contrast_at_midpoint(client: AsyncClient, asset: str) -> None:
    """A midpoint where black wins uses WCAG contrast rather than simple luma."""
    response = await client.get(f"/assets/{asset}/777777/a")

    assert response.status_code == 200
    assert 'fill="#000000"' in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["balloon", "star"])
@pytest.mark.parametrize("letter", ["1", "A1", "-", "á"])
async def test_letter_asset_rejects_non_ascii_letters(
    client: AsyncClient,
    asset: str,
    letter: str,
) -> None:
    """Segments containing anything other than ASCII letters return an error."""
    response = await client.get(f"/assets/{asset}/ffffff/{letter}")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid letter format"}


@pytest.mark.asyncio
@pytest.mark.parametrize("band", ["gold", "silver", "bronze"])
async def test_medal_asset_is_cacheable_svg(client: AsyncClient, band: str) -> None:
    """Each supported medal band is served through the shared asset route."""
    response = await client.get(f"/assets/medal/{band}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == (f"public, max-age={settings.IMAGE_RESPONSE_CACHE_MAX_AGE}")
    assert response.text.lstrip().startswith("<svg")


@pytest.mark.asyncio
async def test_medal_asset_rejects_unknown_band(client: AsyncClient) -> None:
    """Unknown medal bands return the asset router's standard HTTP 400."""
    response = await client.get("/assets/medal/platinum")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid medal band"}


def test_asset_routes_reverse_with_and_without_letters() -> None:
    """Both variants remain available through their established route names."""
    app = FastAPI()
    app.include_router(router)

    assert str(app.url_path_for("balloon", color="fff")) == "/assets/balloon/fff"
    assert str(app.url_path_for("balloon", color="fff", letter="A")) == "/assets/balloon/fff/A"
    assert str(app.url_path_for("star", color="fff")) == "/assets/star/fff"
    assert str(app.url_path_for("star", color="fff", letter="A")) == "/assets/star/fff/A"
    assert str(app.url_path_for("medal", band="gold")) == "/assets/medal/gold"
