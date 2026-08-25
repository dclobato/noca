#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Regenerate Arena problem-category colours as a maximally distinct palette.

Categories created by ``upsert_arena_categories.py`` get a random colour, so a
catalogue of them ends up with pairs that are impossible to tell apart in a
badge. This script replaces every colour at once with a set chosen to maximise
the *smallest* perceptual distance between any two of them.

How the palette is built:

- Candidates are generated in OKLCH (a perceptually uniform space), keeping only
  colours that are in the sRGB gamut and reach at least 4.5:1 contrast against
  black or white -- the two options ``ArenaCategory.foreground_color`` picks from.
- Contest-origin tags (ICPC, OBI, ...) are drawn from a low-chroma pool. They are
  metadata rather than technique, so muting them both groups them visually and
  frees hue space for the far more numerous technique tags.
- Selection is farthest-point growth plus local search on the minimum CIEDE2000
  distance of the whole set.
- Assignment of colours to slugs is then permuted to maximise the minimum
  distance between categories that actually share a problem, because two similar
  badges only ever collide when they are shown side by side.

Requires ``coloraide``, which is not a workspace dependency:

    uv run --with coloraide python scripts/arena/generate_category_colors.py
    uv run --with coloraide python scripts/arena/generate_category_colors.py \
        --apply --rollback undo_category_colors.sql
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import itertools
import logging
import math
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from coloraide import Color  # type: ignore[import-not-found]
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from arena.config import settings as arena_settings
from arena.database import create_engine, create_session_factory
from shared.app_logging import configure_logging
from shared.db_schema.arena import (
    arena_problem_categories,
    arena_problem_category_map,
)

logger = logging.getLogger(__name__)

#: Slugs that name where a problem came from rather than what it teaches.
ORIGIN_SLUGS = frozenset({"icpc", "ioi", "obi", "ncpc", "maratona-sbc", "interif"})

#: Smallest WCAG contrast ratio a badge must reach against black or white.
MIN_CONTRAST = 4.5

MUTED_LIGHTNESS = (0.34, 0.42, 0.50, 0.72, 0.80)
MUTED_CHROMA = (0.02, 0.035, 0.05)
VIVID_LIGHTNESS = (0.55, 0.62, 0.70, 0.78, 0.86)
VIVID_CHROMA = (0.10, 0.13, 0.16, 0.19, 0.22)

#: Independent hill-climbs run for the colour-to-slug assignment; the best wins.
RESTARTS = 8


@dataclass(frozen=True)
class PaletteReport:
    """Quality measurements for one generated palette.

    Attributes:
        min_distance: Smallest CIEDE2000 distance between any two colours.
        min_shared_distance: Smallest distance between two categories that share
            a problem, or None when no two categories ever co-occur.
        min_contrast: Worst text contrast ratio across the palette.
    """

    min_distance: float
    min_shared_distance: float | None
    min_contrast: float


@functools.cache
def _distance(first: str, second: str) -> float:
    """Return the CIEDE2000 distance between two hex colours.

    Memoised: the selection search re-compares the same candidate pairs
    thousands of times, and CIEDE2000 is expensive enough to dominate the run.
    """
    return float(Color(first).delta_e(second, method="2000"))


@functools.cache
def _contrast(hex_color: str) -> float:
    """Return the better of the black/white contrast ratios for a hex colour."""
    color = Color(hex_color)
    return float(max(color.contrast("black"), color.contrast("white")))


def _candidate_pool(lightness: tuple[float, ...], chroma: tuple[float, ...], hue_step: int) -> list[str]:
    """Return legible, in-gamut sRGB hex candidates over an OKLCH grid.

    Args:
        lightness: OKLCH lightness values to sample.
        chroma: OKLCH chroma values to sample.
        hue_step: Hue increment in degrees.

    Returns:
        list[str]: Sorted, de-duplicated ``#rrggbb`` candidates.
    """
    found: set[str] = set()
    for light in lightness:
        for chroma_value in chroma:
            for hue in range(0, 360, hue_step):
                color = Color("oklch", [light, chroma_value, hue])
                if not color.in_gamut("srgb"):
                    continue
                srgb = color.convert("srgb")
                hex_color = str(srgb.to_string(hex=True))
                if _contrast(hex_color) < MIN_CONTRAST:
                    continue
                found.add(hex_color)
    return sorted(found)


def _select(candidates: list[str], count: int, locked: list[str]) -> list[str]:
    """Grow a set of ``count`` candidates that stay far from each other.

    Args:
        candidates: Pool to draw from.
        count: How many colours to pick.
        locked: Already-chosen colours the new ones must also avoid.

    Returns:
        list[str]: The chosen colours.
    """
    picked: list[str] = []
    while len(picked) < count:
        reference = picked + locked
        if reference:
            picked.append(max(candidates, key=lambda c: min(_distance(c, o) for o in reference)))
        else:
            picked.append(candidates[len(candidates) // 2])
    return picked


def _refine(picked: list[str], candidates: list[str], locked: list[str]) -> list[str]:
    """Swap out whichever colour limits the set, while that keeps improving it."""

    def worst(members: list[str]) -> float:
        return min(_distance(a, b) for a, b in itertools.combinations(members + locked, 2))

    best = worst(picked)
    for _ in range(6):
        improved = False
        for index in range(len(picked)):
            others = picked[:index] + picked[index + 1 :]
            reference = others + locked
            replacement = max(candidates, key=lambda c: min(_distance(c, o) for o in reference))
            trial = others + [replacement]
            if worst(trial) > best + 1e-9:
                picked, best, improved = trial, worst(trial), True
        if not improved:
            break
    return picked


async def _co_occurrence(session: AsyncSession) -> Counter[tuple[str, str]]:
    """Count how often each unordered pair of slugs shares a problem."""
    rows = await session.execute(
        select(arena_problem_category_map.c.problem_id, arena_problem_categories.c.slug).join(
            arena_problem_categories,
            arena_problem_categories.c.id == arena_problem_category_map.c.category_id,
        )
    )
    per_problem: dict[str, list[str]] = {}
    for problem_id, slug in rows:
        per_problem.setdefault(problem_id, []).append(slug)

    counts: Counter[tuple[str, str]] = Counter()
    for slugs in per_problem.values():
        for pair in itertools.combinations(sorted(slugs), 2):
            counts[pair] += 1
    return counts


def _assign(
    families: list[tuple[list[str], list[str]]],
    shared: Counter[tuple[str, str]],
    seed: int,
) -> dict[str, str]:
    """Map colours onto slugs so co-occurring categories land far apart.

    Each family is a ``(slugs, colours)`` pair and colours never move between
    families, which is what keeps the contest-origin tags muted. Scoring is
    global, though: a technique tag and an origin tag share a problem all the
    time, so a per-family search would leave exactly those pairs unchecked.

    Starts from the best rotation of each family's hue-sorted colours, then
    hill-climbs by swapping two slugs within one family. Only pairs that share a
    problem are scored; the palette itself already bounds every other pair.

    Args:
        families: Groups of slugs and the colours reserved for them.
        shared: Co-occurrence counts keyed by sorted slug pair.
        seed: Seed for the swap search, so runs are reproducible.

    Returns:
        dict[str, str]: The slug to hex-colour mapping.
    """

    def score(mapping: dict[str, str]) -> float:
        values = [_distance(mapping[a], mapping[b]) for a, b in shared if a in mapping and b in mapping]
        return min(values) if values else 0.0

    mapping: dict[str, str] = {}
    for slugs, colors in families:
        ordered = sorted(colors, key=lambda c: Color(c).convert("oklch")["hue"] or 0.0)
        total = len(slugs)
        strides = [s for s in range(1, max(total, 2)) if math.gcd(s, total) == 1] or [1]
        best_rotation = max(
            ({slugs[i]: ordered[(i * stride) % total] for i in range(total)} for stride in strides),
            key=lambda candidate: score(mapping | candidate),
        )
        mapping.update(best_rotation)

    swappable = [slugs for slugs, _ in families if len(slugs) > 1]
    best_mapping, best_score = dict(mapping), score(mapping)
    for restart in range(RESTARTS):
        rng = random.Random(seed + restart)
        current, current_score = dict(mapping), score(mapping)
        for _ in range(20000):
            first, second = rng.sample(rng.choice(swappable), 2)
            current[first], current[second] = current[second], current[first]
            trial = score(current)
            if trial > current_score:
                current_score = trial
            else:
                current[first], current[second] = current[second], current[first]
        if current_score > best_score:
            best_mapping, best_score = dict(current), current_score
    return best_mapping


def build_palette(slugs: list[str], shared: Counter[tuple[str, str]], seed: int) -> dict[str, str]:
    """Generate a colour for every slug, muting the contest-origin tags."""
    origin = sorted(s for s in slugs if s in ORIGIN_SLUGS)
    technique = sorted(s for s in slugs if s not in ORIGIN_SLUGS)

    muted = _candidate_pool(MUTED_LIGHTNESS, MUTED_CHROMA, hue_step=10)
    vivid = _candidate_pool(VIVID_LIGHTNESS, VIVID_CHROMA, hue_step=4)

    origin_colors = _refine(_select(muted, len(origin), []), muted, [])
    technique_colors = _refine(_select(vivid, len(technique), origin_colors), vivid, origin_colors)

    return _assign([(technique, technique_colors), (origin, origin_colors)], shared, seed)


def measure(palette: dict[str, str], shared: Counter[tuple[str, str]]) -> PaletteReport:
    """Measure how separable a palette is, overall and where it matters."""
    distances = [_distance(a, b) for a, b in itertools.combinations(palette.values(), 2)]
    shared_distances = [_distance(palette[a], palette[b]) for a, b in shared if a in palette and b in palette]
    return PaletteReport(
        min_distance=min(distances),
        min_shared_distance=min(shared_distances) if shared_distances else None,
        min_contrast=min(_contrast(c) for c in palette.values()),
    )


async def _write_rollback(session: AsyncSession, path: Path) -> None:
    """Dump the current colours as UPDATE statements that undo an apply."""
    rows = await session.execute(
        select(arena_problem_categories.c.slug, arena_problem_categories.c.color).order_by(
            arena_problem_categories.c.slug
        )
    )
    lines = ["-- rollback of scripts/arena/generate_category_colors.py --apply"]
    lines += [f"UPDATE arena_problem_categories SET color = '{color}' WHERE slug = '{slug}';" for slug, color in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def recolor(*, apply: bool, rollback: Path, seed: int) -> int:
    """Generate the palette and, when asked, write it to the database.

    Args:
        apply: Write the new colours. When false nothing is committed.
        rollback: Where to dump the undo script before writing.
        seed: Seed for the assignment search.

    Returns:
        int: Number of categories whose colour changed (0 for a dry run).
    """
    engine = create_engine(arena_settings.db_url, poolclass=NullPool)
    session_factory = create_session_factory(engine)
    changed = 0
    try:
        async with session_factory() as session:
            rows = await session.execute(select(arena_problem_categories.c.slug, arena_problem_categories.c.color))
            current: dict[str, str] = {slug: color for slug, color in rows}
            if not current:
                raise ValueError("No Arena categories found; nothing to recolour.")

            shared = await _co_occurrence(session)
            palette = build_palette(sorted(current), shared, seed)
            report = measure(palette, shared)

            print(f"min CIEDE2000 (todo o conjunto):     {report.min_distance:.1f}")
            if report.min_shared_distance is not None:
                print(f"min CIEDE2000 (mesmo problema):     {report.min_shared_distance:.1f}")
            print(f"contraste minimo do texto:          {report.min_contrast:.2f}:1")
            print(f"cores distintas:                    {len(set(palette.values()))}/{len(palette)}")

            for slug in sorted(palette):
                marker = " " if palette[slug] == current[slug] else "*"
                print(f"{marker} {slug:<22} {current[slug]} -> {palette[slug]}")
                changed += marker == "*"

            if not apply:
                print("\n(dry run -- nada foi gravado; use --apply)")
                return 0

            await _write_rollback(session, rollback)
            for slug, color in palette.items():
                if color != current[slug]:
                    await session.execute(
                        update(arena_problem_categories)
                        .where(arena_problem_categories.c.slug == slug)
                        .values(color=color, updated_at=func.now())
                    )
            await session.commit()
            print(f"\n{changed} categorias atualizadas; rollback em {rollback}")
    finally:
        await engine.dispose()
    return changed


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Recolour Arena problem categories.")
    parser.add_argument("--apply", action="store_true", help="Write the palette to the database")
    parser.add_argument(
        "--rollback",
        type=Path,
        default=Path("undo_category_colors.sql"),
        help="Where to dump the undo script before applying (default: %(default)s)",
    )
    parser.add_argument("--seed", type=int, default=7, help="Seed for the assignment search")
    return parser.parse_args()


async def _main() -> int:
    """Run the CLI command."""
    args = _parse_args()
    await recolor(apply=args.apply, rollback=args.rollback, seed=args.seed)
    return 0


if __name__ == "__main__":
    configure_logging(logging_level=logging.INFO)
    try:
        raise SystemExit(asyncio.run(_main()))
    except ValueError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
