#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Framework-agnostic contest presentation SVG assets.

This is the cross-module source of truth for the balloon, star, and medal
artwork. It has no web-framework dependency: invalid input raises ``ValueError``
and each caller maps that to its own HTTP error. Web and animator expose thin
routes that delegate here.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

_ASSETS_DIR = Path(__file__).parent / "assets"
_BALLOON_TEMPLATE = (_ASSETS_DIR / "balloontemplate.svg").read_text(encoding="utf-8")
_STAR_TEMPLATE = (_ASSETS_DIR / "startemplate.svg").read_text(encoding="utf-8")
_MEDAL_SVGS = {band: (_ASSETS_DIR / f"{band}.svg").read_text(encoding="utf-8") for band in ("gold", "silver", "bronze")}


def normalize_hex_color(color: str) -> str:
    """Normalize a three- or six-digit hexadecimal color.

    Args:
        color: A hex color, with or without a leading ``#`` (3 or 6 digits).

    Returns:
        The normalized lowercase ``#rrggbb`` string.

    Raises:
        ValueError: If the value is not a valid 3- or 6-digit hex color.
    """
    value = color.strip()
    if value.startswith("#"):
        value = value[1:]

    if re.fullmatch(r"[0-9a-fA-F]{3}", value):
        value = "".join(ch * 2 for ch in value)
    elif not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("Invalid color format")

    return f"#{value.lower()}"


def normalize_letter(letter: str) -> str:
    """Validate an ASCII letter path segment and return its first letter.

    Args:
        letter: One or more ASCII letters.

    Returns:
        The first letter, uppercased.

    Raises:
        ValueError: If the value contains anything other than ASCII letters.
    """
    if not re.fullmatch(r"[A-Za-z]+", letter):
        raise ValueError("Invalid letter format")

    return letter[0].upper()


def render_medal_svg(band: str) -> str:
    """Return the SVG document for a medal band.

    Args:
        band: One of ``gold``, ``silver``, or ``bronze``.

    Returns:
        The corresponding medal SVG document.

    Raises:
        ValueError: If ``band`` is not a supported medal band.
    """
    try:
        return _MEDAL_SVGS[band]
    except KeyError as exc:
        raise ValueError("Invalid medal band") from exc


def _relative_luminance(fill_color: str) -> float:
    """Calculate the WCAG relative luminance of a normalized hex color."""

    def _linearize(channel: int) -> float:
        """Convert an sRGB channel to its linear-light value."""
        value = channel / 255
        if value <= 0.04045:
            return value / 12.92
        return math.pow((value + 0.055) / 1.055, 2.4)

    red = _linearize(int(fill_color[1:3], 16))
    green = _linearize(int(fill_color[3:5], 16))
    blue = _linearize(int(fill_color[5:7], 16))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _text_color(fill_color: str) -> str:
    """Return black or white, whichever contrasts most with the fill."""
    luminance = _relative_luminance(fill_color)
    black_contrast = (luminance + 0.05) / 0.05
    white_contrast = 1.05 / (luminance + 0.05)
    return "#000000" if black_contrast >= white_contrast else "#ffffff"


def _letter_element(letter: str | None, fill_color: str, font_size: int) -> str:
    """Build the optional centered SVG text element."""
    if letter is None:
        return ""

    return (
        "  <text\n"
        '    x="120"\n'
        '    y="150"\n'
        '    text-anchor="middle"\n'
        '    dominant-baseline="middle"\n'
        '    font-family="Arial, Helvetica, sans-serif"\n'
        f'    font-size="{font_size}"\n'
        '    font-weight="700"\n'
        f'    fill="{_text_color(fill_color)}"\n'
        f"  >{letter}</text>"
    )


@lru_cache(maxsize=64)
def render_balloon_svg(fill_color: str, letter: str | None = None) -> str:
    """Render a balloon SVG with an optional centered letter.

    Args:
        fill_color: A normalized ``#rrggbb`` color (see ``normalize_hex_color``).
        letter: An already-normalized single uppercase letter, or ``None``.

    Returns:
        The rendered SVG document as a string.
    """
    r = int(fill_color[1:3], 16)
    g = int(fill_color[3:5], 16)
    b = int(fill_color[5:7], 16)

    lum = 0.299 * r + 0.587 * g + 0.114 * b
    line_color = "#000000" if lum > 128 else "#ffffff"

    return _BALLOON_TEMPLATE.format(
        fill_color=fill_color,
        line_color=line_color,
        letter_element=_letter_element(letter, fill_color, 80),
    )


@lru_cache(maxsize=64)
def render_star_svg(fill_color: str, letter: str | None = None) -> str:
    """Render a star SVG with an optional centered letter.

    Args:
        fill_color: A normalized ``#rrggbb`` color (see ``normalize_hex_color``).
        letter: An already-normalized single uppercase letter, or ``None``.

    Returns:
        The rendered SVG document as a string.
    """
    return _STAR_TEMPLATE.format(
        fill_color=fill_color,
        letter_element=_letter_element(letter, fill_color, 64),
    )
