#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Prototype: bimodal problem-difficulty transform, evaluated on real data.

Read-only experiment. Reads the live ``arena_problem_ratings`` stats, reproduces
the current difficulty, then applies a logistic-gain ("contrast") transform with
a configurable pivot and confidence-gated gain to see how the distribution shifts
toward the extremes. Nothing is written back.

Run: ``uv run python scripts/prototype_bimodal_rating.py``
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from math import exp, log

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from rating.config import settings
from shared.db_schema.arena.arena_problems import arena_problem_ratings, arena_problems
from shared.services.arena_rating import (
    ALPHA,
    BETA,
    CONFIDENCE_SCALE,
    MAX_RELEVANT_TRIES,
    PRIOR_SOLVE_RATE,
    PRIOR_TRIES,
    W_SOLVE_RATE,
    W_TRIES,
)


@dataclass
class ProblemStat:
    """One problem's rating inputs pulled from the database."""

    problem_id: str
    attempted: int
    solved: int
    tries: int
    created_at: datetime


def raw_difficulty(p: ProblemStat) -> float:
    """Reproduce the live weighted ``raw`` in [0, 1] with the current priors."""
    return raw_with_prior(p, ALPHA, BETA)


def current_dint(raw: float) -> int:
    """Current linear map: raw -> internal [1, 100]."""
    return round(max(1.0, min(100.0, 1.0 + 99.0 * raw)))


def _logit(x: float, eps: float = 1e-6) -> float:
    x = min(1.0 - eps, max(eps, x))
    return log(x / (1.0 - x))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + exp(-z))


def gated_gain(n: int, k_max: float, scale: float = float(CONFIDENCE_SCALE)) -> float:
    """Confidence-gated gain: ~1 for low-data problems, ->k_max as evidence grows."""
    return 1.0 + (k_max - 1.0) * (1.0 - exp(-n / scale))


def contrast(raw: float, pivot: float, k: float) -> float:
    """Logistic gain in logit space, recentred on ``pivot``. k>1 => bimodal push."""
    return _sigmoid(k * (_logit(raw) - _logit(pivot)))


def proposed_dint(raw: float, n: int, pivot: float, k_max: float, hi: int) -> int:
    """Apply gated contrast and map onto internal [1, hi]."""
    c = contrast(raw, pivot, gated_gain(n, k_max))
    return round(max(1.0, min(float(hi), 1.0 + (hi - 1) * c)))


def histogram(values: list[float], title: str, lo: float = 0.0, hi: float = 10.0) -> str:
    """Render a 10-bucket text histogram over the display scale [lo, hi]."""
    buckets = [0] * 10
    width = (hi - lo) / 10
    for v in values:
        idx = min(9, max(0, int((v - lo) / width)))
        buckets[idx] += 1
    peak = max(buckets) or 1
    lines = [title]
    for i, count in enumerate(buckets):
        bar = "#" * round(40 * count / peak)
        lines.append(f"  [{lo + i * width:4.1f}-{lo + (i + 1) * width:4.1f}) {count:3d} {bar}")
    return "\n".join(lines)


async def load() -> list[ProblemStat]:
    """Load problems that have at least one attempt."""
    eng = create_async_engine(settings.db_url)
    stats: list[ProblemStat] = []
    async with eng.connect() as c:
        rows = await c.execute(
            select(
                arena_problem_ratings.c.problem_id,
                arena_problem_ratings.c.attempted_users,
                arena_problem_ratings.c.solved_users,
                arena_problem_ratings.c.total_tries_before_solve,
                arena_problems.c.created_at,
            )
            .join(arena_problems, arena_problems.c.id == arena_problem_ratings.c.problem_id)
            .where(arena_problem_ratings.c.attempted_users > 0)
        )
        for r in rows:
            stats.append(
                ProblemStat(r.problem_id, r.attempted_users, r.solved_users, r.total_tries_before_solve, r.created_at)
            )
    await eng.dispose()
    return stats


def raw_with_prior(p: ProblemStat, alpha: float, beta: float) -> float:
    """Same as raw_difficulty but with overridable prior weights."""
    solve_rate = (p.solved + alpha * PRIOR_SOLVE_RATE) / (p.attempted + alpha)
    avg_tries = (p.tries + beta * PRIOR_TRIES) / (p.solved + beta)
    solve_c = 1.0 - solve_rate
    tries_c = max(0.0, min(1.0, log(avg_tries) / log(MAX_RELEVANT_TRIES)))
    return W_SOLVE_RATE * solve_c + W_TRIES * tries_c


def report(stats: list[ProblemStat]) -> None:
    """Print current vs proposed distributions for a few parameter sets."""
    raws = [raw_difficulty(p) for p in stats]
    median_raw = statistics.median(raws)
    neutral = raw_difficulty(ProblemStat("", 1000, 500, 1000, datetime.now(UTC)))  # solve~0.5, tries~2
    print(f"problems with attempts: {len(stats)}")
    print(f"raw: min={min(raws):.3f} median={median_raw:.3f} max={max(raws):.3f}")
    print(f"fixed-neutral pivot (solve=0.5,tries=2): {neutral:.3f}\n")

    # --- upstream diagnostics: what does the underlying signal actually look like? ---
    print("attempted_users distribution:", sorted(p.attempted for p in stats))
    emp = [(p.solved / p.attempted) for p in stats]
    print(f"EMPIRICAL solve rate (no prior): min={min(emp):.2f} median={statistics.median(emp):.2f} max={max(emp):.2f}")
    for a, b in [(ALPHA, BETA), (5.0, 3.0), (2.0, 1.0)]:
        rr = [raw_with_prior(p, a, b) for p in stats]
        lo, med, hi = min(rr), statistics.median(rr), max(rr)
        print(f"raw with alpha={a:<4} beta={b:<4}: min={lo:.3f} median={med:.3f} max={hi:.3f} spread={hi - lo:.3f}")
    print()

    current = [current_dint(r) / 10 for r in raws]
    print(histogram(current, "CURRENT  (linear, internal 1-100, display /10)"))

    # (label, alpha, beta, pivot-mode, k_max, ungated)
    scenarios = [
        ("current prior (a20/b10), median pivot, k_max=4", 20.0, 10.0, "median", 4.0, False),
        ("weak prior (a5/b3), median pivot, k_max=4", 5.0, 3.0, "median", 4.0, False),
        ("weak prior (a5/b3), median pivot, k_max=4, UNGATED", 5.0, 3.0, "median", 4.0, True),
        ("very weak prior (a2/b1), median pivot, k_max=4, UNGATED", 2.0, 1.0, "median", 4.0, True),
    ]
    for label, a, b, mode, k_max, ungated in scenarios:
        rr = [raw_with_prior(p, a, b) for p in stats]
        pivot = statistics.median(rr) if mode == "median" else neutral
        out = []
        for p, raw in zip(stats, rr, strict=True):
            k = k_max if ungated else gated_gain(p.attempted, k_max)
            c = contrast(raw, pivot, k)
            out.append(round(max(1.0, min(100.0, 1.0 + 99.0 * c))) / 10)
        print()
        print(histogram(out, f"PROPOSED ({label})"))


async def main() -> None:
    stats = await load()
    report(stats)


if __name__ == "__main__":
    asyncio.run(main())
