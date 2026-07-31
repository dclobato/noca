#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public presentation SVG asset routes for the animator scoreboard.

These thin routes delegate all rendering to the framework-agnostic
``shared.services.balloon_assets`` module so the animator serves the same
balloon, star, and medal artwork the web module does, from its own origin,
without importing ``web`` or reaching it over HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from shared.services.balloon_assets import (
    normalize_hex_color,
    normalize_letter,
    render_balloon_svg,
    render_medal_svg,
    render_star_svg,
)

router = APIRouter(prefix="/assets", tags=["animator-assets"])

# Balloon/star artwork is content-addressed by color and letter, so it can be
# cached aggressively by the browser and any intermediary.
_CACHE_MAX_AGE = 3600
_CACHE_HEADERS = {"Cache-Control": f"public, max-age={_CACHE_MAX_AGE}"}


def _svg_response(svg: str) -> Response:
    """Wrap a rendered SVG string in a cacheable image response."""
    return Response(content=svg, media_type="image/svg+xml", headers=_CACHE_HEADERS)


def _color(color: str) -> str:
    """Normalize a color path segment, mapping invalid input to HTTP 400."""
    try:
        return normalize_hex_color(color)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid color format") from exc


def _letter(letter: str) -> str:
    """Normalize a letter path segment, mapping invalid input to HTTP 400."""
    try:
        return normalize_letter(letter)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid letter format") from exc


def _medal(band: str) -> str:
    """Resolve a medal band, mapping invalid input to HTTP 400."""
    try:
        return render_medal_svg(band)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid medal band") from exc


@router.get("/balloon/{color}", name="animator_balloon")
async def balloon(color: str) -> Response:
    """Return a balloon SVG without a letter."""
    return _svg_response(render_balloon_svg(_color(color)))


@router.get("/balloon/{color}/{letter}", name="animator_balloon_letter")
async def balloon_with_letter(color: str, letter: str) -> Response:
    """Return a balloon SVG containing the first supplied ASCII letter."""
    return _svg_response(render_balloon_svg(_color(color), _letter(letter)))


@router.get("/star/{color}", name="animator_star")
async def star(color: str) -> Response:
    """Return a star SVG without a letter."""
    return _svg_response(render_star_svg(_color(color)))


@router.get("/star/{color}/{letter}", name="animator_star_letter")
async def star_with_letter(color: str, letter: str) -> Response:
    """Return a star SVG containing the first supplied ASCII letter."""
    return _svg_response(render_star_svg(_color(color), _letter(letter)))


@router.get("/medal/{band}", name="animator_medal")
async def medal(band: str) -> Response:
    """Return the SVG for a supported medal band."""
    return _svg_response(_medal(band))
