#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/assets", tags=["assets"])

_BALLOON_TEMPLATE_PATH = Path(__file__).parent.parent / "assets" / "balloontemplate.svg"
_TEMPLATE = _BALLOON_TEMPLATE_PATH.read_text(encoding="utf-8")

_STAR_TEMPLATE_PATH = Path(__file__).parent.parent / "assets" / "startemplate.svg"
_TEMPLATE_STAR = _STAR_TEMPLATE_PATH.read_text(encoding="utf-8")


def _normalize_hex_color(color: str) -> str:
    value = color.strip()
    if value.startswith("#"):
        value = value[1:]

    if re.fullmatch(r"[0-9a-fA-F]{3}", value):
        value = "".join(ch * 2 for ch in value)
    elif not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise HTTPException(status_code=400, detail="Invalid color format")

    return f"#{value.lower()}"


@lru_cache(maxsize=64)
def _render_balloon_svg(fill_color: str) -> str:
    r = int(fill_color[1:3], 16)
    g = int(fill_color[3:5], 16)
    b = int(fill_color[5:7], 16)

    lum = 0.299 * r + 0.587 * g + 0.114 * b
    line_color = "#000000" if lum > 128 else "#ffffff"

    return _TEMPLATE.format(
        fill_color=fill_color,
        line_color=line_color,
    )


@lru_cache(maxsize=64)
def _render_star_svg(fill_color: str) -> str:
    r = int(fill_color[1:3], 16)
    g = int(fill_color[3:5], 16)
    b = int(fill_color[5:7], 16)

    lum = 0.299 * r + 0.587 * g + 0.114 * b
    line_color = "#000000" if lum > 128 else "#ffffff"

    return _TEMPLATE_STAR.format(
        fill_color=fill_color,
        line_color=line_color,
    )


@router.get("/balloon/{color}")
async def balloon(color: str) -> Response:
    fill_color = _normalize_hex_color(color.strip())
    svg = _render_balloon_svg(fill_color)
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/star/{color}")
async def star(color: str) -> Response:
    fill_color = _normalize_hex_color(color.strip())
    svg = _render_star_svg(fill_color)
    return Response(content=svg, media_type="image/svg+xml")
