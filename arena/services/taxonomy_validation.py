#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Field rules shared by Arena's flat, colored taxonomies.

Categories and collections are different axes -- a problem has many categories
and at most one collection -- but their name, slug, and badge-color fields obey
exactly the same rules. They live here so the two cannot drift apart on stop
words, slug shape, or color format.

The stop-word list is mirrored in ``arena/static/js/category-slug.js`` for the
browser-side slug preview; keep the two in sync.
"""

from __future__ import annotations

import colorsys
import random
import re
import unicodedata

MAX_FIELD_LENGTH = 128
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Stop words removed before building a slug so that prepositions and articles
# do not inflate URL length.  The set covers common Portuguese function words
# plus their direct English equivalents (useful for mixed-language names).
SLUG_STOP_WORDS: frozenset[str] = frozenset(
    {
        # Portuguese articles
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        # Portuguese prepositions & contractions
        "de",
        "do",
        "da",
        "dos",
        "das",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "por",
        "para",
        "com",
        "pelo",
        "pela",
        "pelos",
        "pelas",
        # Portuguese conjunctions / pronouns
        "e",
        "ou",
        "se",
        # English articles / prepositions / conjunctions
        "the",
        "an",
        "and",
        "or",
        "of",
        "in",
        "on",
        "for",
        "to",
        "from",
        "with",
        "by",
        "at",
        # English copula
        "is",
        "are",
    }
)


def normalize_slug(value: str) -> str:
    """Normalize text into the same lowercase hyphen slug used by contest forms.

    Diacritics are stripped, stop words (Portuguese + English) are removed, and
    the remaining tokens are joined with hyphens.  This keeps slugs concise while
    preserving all semantically meaningful words.

    Args:
        value: Raw slug or human-readable name.

    Returns:
        str: URL-safe, stop-word-free slug.
    """
    normalized = unicodedata.normalize("NFD", value.lower())
    without_marks = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    tokens = re.sub(r"[^a-z0-9]+", " ", without_marks).split()
    words = [t for t in tokens if t not in SLUG_STOP_WORDS]
    return "-".join(words)


def validate_required_text(value: str, field_name: str) -> str:
    """Strip and validate a required 128-character taxonomy field.

    Args:
        value: Raw field value.
        field_name: Human-readable field name used in the error message.

    Returns:
        str: The stripped value.

    Raises:
        ValueError: If the value is blank or too long.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} is required.")
    if len(stripped) > MAX_FIELD_LENGTH:
        raise ValueError(f"{field_name} must be at most {MAX_FIELD_LENGTH} characters.")
    return stripped


def validate_slug(raw_slug: str) -> str:
    """Normalize a raw slug and enforce its shape.

    Args:
        raw_slug: Raw slug from a form.

    Returns:
        str: The normalized slug.

    Raises:
        ValueError: If the slug is blank, too long, or malformed.
    """
    normalized = normalize_slug(validate_required_text(raw_slug, "Slug"))
    if len(normalized) > MAX_FIELD_LENGTH:
        raise ValueError(f"Slug must be at most {MAX_FIELD_LENGTH} characters.")
    if not SLUG_PATTERN.fullmatch(normalized):
        raise ValueError("Slug must contain lowercase letters, numbers, and single hyphens only.")
    return normalized


def validate_color(raw_color: str) -> str:
    """Normalize a raw badge color and enforce the 6-digit hex format.

    Args:
        raw_color: Raw color from a form.

    Returns:
        str: The lowercased ``#rrggbb`` color.

    Raises:
        ValueError: If the color is blank or not 6-digit hex.
    """
    normalized = validate_required_text(raw_color, "Color").lower()
    if not COLOR_PATTERN.fullmatch(normalized):
        raise ValueError("Color must be a 6-digit hex value like #6c757d.")
    return normalized


def random_badge_color() -> str:
    """Return a random, visually pleasing hex color for a new taxonomy row.

    Generates a color in HSL space with a random hue and constrained
    saturation/lightness so the result is always vivid and readable as a
    badge background.

    Returns:
        str: A hex color string such as ``"#4a9ef2"``.
    """
    hue = random.random()
    saturation = random.uniform(0.55, 0.75)
    lightness = random.uniform(0.40, 0.58)
    red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
    return f"#{int(red * 255):02x}{int(green * 255):02x}{int(blue * 255):02x}"
