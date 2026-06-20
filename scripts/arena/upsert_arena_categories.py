#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Upsert Arena categories from a text file.

Usage:
    uv run python scripts/arena/upsert_arena_categories.py categories-en.txt

Input format:
    Arithmetic #00ff00
    Number theory
    GCD / LCM

Each non-empty line contains a category name and may end with a ``#RRGGBB``
color. Lines without a color receive a random hex color.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import NullPool

from arena.config import settings as arena_settings
from arena.database import create_engine, create_session_factory
from arena.models.arena_problems import ArenaCategory
from arena.services.admin_category_service import normalize_slug, validate_category_data
from shared.app_logging import configure_logging

logger = logging.getLogger(__name__)

_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class ParsedCategory:
    """Category data parsed from one input line."""

    name: str
    color: str


@dataclass(frozen=True)
class UpsertSummary:
    """Count of category changes made by the import."""

    created: int
    updated: int


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Upsert Arena categories from a text file.")
    parser.add_argument("path", type=Path, help="Text file with one category per line")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for reproducible colors on lines without explicit colors",
    )
    return parser.parse_args()


def _random_color(rng: random.Random) -> str:
    """Return a random ``#RRGGBB`` color."""
    return f"#{rng.randrange(0x1000000):06x}"


def parse_category_line(line: str, *, line_number: int, rng: random.Random) -> ParsedCategory | None:
    """Parse a single category input line.

    Args:
        line: Raw line from the input file.
        line_number: One-based line number for error messages.
        rng: Random generator used when a line omits the color.

    Returns:
        ParsedCategory | None: Parsed category, or None for blank lines.

    Raises:
        ValueError: If the line contains an invalid trailing color token or no name.
    """
    stripped = line.strip()
    if not stripped:
        return None

    parts = stripped.rsplit(maxsplit=1)
    color: str | None = None
    name = stripped

    if len(parts) == 2 and parts[1].startswith("#"):
        if not _COLOR_PATTERN.fullmatch(parts[1]):
            raise ValueError(f"Line {line_number}: color must be a 6-digit hex value like #6c757d.")
        name = parts[0].strip()
        color = parts[1].lower()
    elif len(parts) == 1 and parts[0].startswith("#"):
        raise ValueError(f"Line {line_number}: category name is required.")

    if not name:
        raise ValueError(f"Line {line_number}: category name is required.")
    return ParsedCategory(name=name, color=color or _random_color(rng))


def load_categories(path: Path, *, seed: int | None = None) -> list[ParsedCategory]:
    """Load parsed category records from a text file."""
    if not path.is_file():
        raise ValueError(f"Category file not found: {path}")

    rng = random.Random(seed)
    categories: list[ParsedCategory] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        parsed = parse_category_line(line, line_number=line_number, rng=rng)
        if parsed is not None:
            categories.append(parsed)
    if not categories:
        raise ValueError("Category file does not contain any categories.")
    return categories


async def upsert_categories(path: Path, *, seed: int | None = None) -> UpsertSummary:
    """Upsert all categories from ``path`` using normalized slug as the key."""
    categories = load_categories(path, seed=seed)
    engine = create_engine(arena_settings.db_url, poolclass=NullPool)
    session_factory = create_session_factory(engine)

    created = 0
    updated = 0
    try:
        async with session_factory() as session:
            for parsed in categories:
                slug = normalize_slug(parsed.name)
                existing = await session.scalar(select(ArenaCategory).where(ArenaCategory.slug == slug))
                data = await validate_category_data(
                    session,
                    name=parsed.name,
                    slug=slug,
                    color=parsed.color,
                    exclude_id=existing.id if existing is not None else None,
                )

                if existing is None:
                    category = ArenaCategory(name=data.name, slug=data.slug, color=data.color)
                    session.add(category)
                    created += 1
                    print(f"CREATE {data.slug} {data.color} {data.name}")
                else:
                    existing.name = data.name
                    existing.color = data.color
                    updated += 1
                    print(f"UPDATE {data.slug} {data.color} {data.name}")

            await session.commit()
    finally:
        await engine.dispose()

    return UpsertSummary(created=created, updated=updated)


async def _main() -> int:
    """Run the CLI command."""
    args = _parse_args()
    summary = await upsert_categories(args.path, seed=args.seed)
    print(f"SUCCESS: {summary.created} created, {summary.updated} updated")
    return 0


if __name__ == "__main__":
    configure_logging(logging_level=logging.INFO)
    try:
        raise SystemExit(asyncio.run(_main()))
    except ValueError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
