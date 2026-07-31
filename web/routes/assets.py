#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from shared.services.balloon_assets import (
    normalize_hex_color,
    normalize_letter,
    render_balloon_svg,
    render_medal_svg,
    render_star_svg,
)
from web.config import settings

router = APIRouter(prefix="/assets", tags=["assets"])

_CACHE_HEADERS = {"Cache-Control": f"public, max-age={settings.IMAGE_RESPONSE_CACHE_MAX_AGE}"}


def _svg_response(svg: str) -> Response:
    """Wrap an SVG document in a cacheable image response."""
    return Response(content=svg, media_type="image/svg+xml", headers=_CACHE_HEADERS)


def _normalize_asset_parameters(color: str, letter: str | None = None) -> tuple[str, str | None]:
    """Normalize route parameters and translate validation errors to HTTP 400."""
    try:
        fill_color = normalize_hex_color(color)
        normalized_letter = normalize_letter(letter) if letter is not None else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return fill_color, normalized_letter


@router.get("/balloon/{color}")
async def balloon(color: str) -> Response:
    """Return a balloon SVG without a letter."""
    fill_color, _ = _normalize_asset_parameters(color)
    return _svg_response(render_balloon_svg(fill_color))


@router.get("/balloon/{color}/{letter}", name="balloon")
async def balloon_with_letter(color: str, letter: str) -> Response:
    """Return a balloon SVG containing the first supplied ASCII letter."""
    fill_color, normalized_letter = _normalize_asset_parameters(color, letter)
    return _svg_response(render_balloon_svg(fill_color, normalized_letter))


@router.get("/star/{color}")
async def star(color: str) -> Response:
    """Return a star SVG without a letter."""
    fill_color, _ = _normalize_asset_parameters(color)
    return _svg_response(render_star_svg(fill_color))


@router.get("/star/{color}/{letter}", name="star")
async def star_with_letter(color: str, letter: str) -> Response:
    """Return a star SVG containing the first supplied ASCII letter."""
    fill_color, normalized_letter = _normalize_asset_parameters(color, letter)
    return _svg_response(render_star_svg(fill_color, normalized_letter))


@router.get("/medal/{band}", name="medal")
async def medal(band: str) -> Response:
    """Return the SVG for a supported medal band."""
    try:
        return _svg_response(render_medal_svg(band))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
