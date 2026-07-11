#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena problem-difficulty, user-score, and affiliation rating logic.

Pure, loop-free rating algorithms shared by the Arena module (help page and
footer) and the standalone rating worker. The periodic background loops that
drive these functions live in ``rating.loops``.

  1. Problem difficulty (1–100 internal, displayed as 0.1–10.0) — a Bayesian
     solve-rate + avg-tries model produces a raw estimate, then a logistic-gain
     "contrast" transform repels it away from the centre toward the easy/hard
     extremes (a deliberately bimodal distribution). The contrast pivots on the
     population median raw (so the split stays balanced). Both the contrast slope
     *and* each problem's effective pivot are gated by that problem's own attempt
     count: the slope ramps from identity toward its maximum, and the pivot ramps
     from the neutral centre toward the population median. Low-data problems
     therefore stay near the centre — neither pushed to the extremes nor measured
     against a skewed population — and only well-attempted problems are pushed to
     the easy/hard ends. Pulls toward medium difficulty when data is scarce;
     diverges toward 1 or 100 as evidence accumulates.

  2. User score (0–∞) — Exponential-growth points per solved problem.
     Problems at display-difficulty 10.0 pay ~28× more than problems at
     display-difficulty 0.1, so hard problems dominate the leaderboard.

  3. Affiliation rating (0–∞) — Geometrically weighted average of member user scores.
     Members are sorted by score descending; weight i = (1/f) * (1-1/f)^i so the
     top member contributes the most and each successive member contributes less.
     Factor f is configurable via NOCA_RATING_AFFILIATION_FACTOR (default 5).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import exp, log

from relative_datetime import DateTimeUtils
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_affiliation_rating_history as _arena_affiliation_rating_history
from shared.db_schema.arena import arena_affiliations as _arena_affiliations
from shared.db_schema.arena import arena_problem_rating_history as _arena_problem_rating_history
from shared.db_schema.arena import arena_problem_ratings as _arena_problem_rating
from shared.db_schema.arena import arena_problem_solvers as _arena_problem_solvers
from shared.db_schema.arena import arena_problems as _arena_problems
from shared.db_schema.arena import arena_submissions as _arena_submissions
from shared.db_schema.arena import arena_user_rating_history as _arena_user_rating_history
from shared.db_schema.arena import arena_users as _arena_users
from shared.services.arena_difficulty_histogram import persist_difficulty_histogram
from shared.services.arena_query_helpers import counts_toward_problem_rating

# ---------------------------------------------------------------------------
# Algorithm constants — centralised so they can be tuned without touching formulas
# ---------------------------------------------------------------------------

ALPHA: float = 10.0  # prior weight for solve-rate; lower than before so empirical data diverges sooner
BETA: float = 10.0  # prior weight for avg-tries   (new problem → pulled toward 2 tries)
PRIOR_SOLVE_RATE: float = 0.50
PRIOR_TRIES: float = 2.0
MAX_RELEVANT_TRIES: float = 10.0  # tries at or above this saturate the component at 1.0
W_SOLVE_RATE: float = 0.80  # weight of solve-rate component in difficulty
W_TRIES: float = 0.20  # weight of avg-tries component in difficulty
BASE_POINTS: float = 10.0  # points awarded for difficulty-1 problem
GROWTH: float = 1.45  # exponential growth per difficulty unit
CONFIDENCE_SCALE: int = 10  # saturation scale for rating confidence: C(n) = round(100*(1-e^(-n/k)))

# Bimodal contrast: a logistic gain in logit space repels difficulty away from the
# pivot toward the [1, 100] extremes. Gain 1 = identity (no reshaping); gain > 1
# steepens the curve around the pivot, hollowing out the centre. The gain is gated
# by attempt count so noisy low-data problems are not flung to the extremes.
CONTRAST_GAIN_MAX: float = 4.0  # maximum logit-space slope for well-attempted problems
CONTRAST_GAIN_SCALE: float = 25.0  # attempts at which the gain reaches ~63 % of its span
PIVOT_MIN_ATTEMPTS: int = 10  # min attempts for a problem to inform the population pivot

# Per-problem pivot blending: a low-data problem is measured against a pivot ramped from the
# neutral centre toward the population median by its own attempt count, mirroring how the
# contrast gain is gated. This stops a problem with little evidence from inheriting an
# easy/hard rating purely because the well-attempted population is skewed. Larger scale =
# more attempts needed before the population pivot is fully trusted.
PIVOT_BLEND_SCALE: float = 8.0  # attempts at which pivot trust reaches ~63 % of the population median

# Raw difficulty of a perfectly average problem (solve_rate = 0.5, avg_tries = PRIOR_TRIES).
# Used as the contrast pivot when no problem has enough data yet, so an unknown problem maps
# to the scale centre.
_NEUTRAL_PIVOT: float = W_SOLVE_RATE * (1.0 - PRIOR_SOLVE_RATE) + W_TRIES * (log(PRIOR_TRIES) / log(MAX_RELEVANT_TRIES))

NextRatingUpdateCallback = Callable[[datetime | None], None]

# Valkey keys under which the rating worker publishes scheduler metadata.
# The next-update key stores an ISO8601 timestamp and is absent while a cycle
# is actively running. The interval/factor keys store display-ready metadata
# read by every Arena instance.
NEXT_RATING_UPDATE_KEY = "arena:rating:next_update"
RATING_INTERVAL_TEXT_KEY = "arena:rating:interval_text"
RATING_AFFILIATION_FACTOR_KEY = "arena:rating:affiliation_factor"


# ---------------------------------------------------------------------------
# Core math helpers
# ---------------------------------------------------------------------------


def _raw_difficulty(
    *,
    attempted_users: int,
    solved_users: int,
    total_tries_before_solve: int,
) -> float:
    """Return the raw weighted difficulty estimate in [0, 1] before contrast.

    Combines the Bayesian solve-rate and Bayesian average-tries signals into a
    single weighted value. This is the evidence-based central estimate;
    ``_apply_contrast`` reshapes it toward the extremes.

    Args:
        attempted_users: Unique users who submitted at least once.
        solved_users: Unique users who reached AC.
        total_tries_before_solve: Sum of per-user attempt counts up to first AC.

    Returns:
        float: Raw difficulty in [0, 1].
    """
    solve_rate = (solved_users + ALPHA * PRIOR_SOLVE_RATE) / (attempted_users + ALPHA)
    avg_tries = (total_tries_before_solve + BETA * PRIOR_TRIES) / (solved_users + BETA)

    solve_component = 1.0 - solve_rate
    tries_component = max(0.0, min(1.0, log(avg_tries) / log(MAX_RELEVANT_TRIES)))

    return W_SOLVE_RATE * solve_component + W_TRIES * tries_component


def _contrast_gain(attempted_users: int) -> float:
    """Return the evidence-gated logit-space gain for the contrast transform.

    Ranges from 1.0 (no reshaping, for a problem with no attempts) up to
    ``CONTRAST_GAIN_MAX`` as attempts accumulate, so the bimodal push only
    applies once there is enough data to justify it.

    Args:
        attempted_users: Unique users who submitted at least once.

    Returns:
        float: Gain factor in [1.0, CONTRAST_GAIN_MAX].
    """
    return 1.0 + (CONTRAST_GAIN_MAX - 1.0) * (1.0 - exp(-attempted_users / CONTRAST_GAIN_SCALE))


def _effective_pivot(population_pivot: float, attempted_users: int) -> float:
    """Return the per-problem contrast pivot, ramped by the problem's own attempt count.

    Blends ``_NEUTRAL_PIVOT`` (at zero attempts, so an unknown problem maps to the
    scale centre) toward ``population_pivot`` as the problem accumulates attempts.
    This mirrors how ``_contrast_gain`` gates the contrast slope, so a low-evidence
    problem is not pushed toward the easy/hard extremes merely because the
    well-attempted population happens to be skewed.

    At zero attempts the result is exactly ``_NEUTRAL_PIVOT``; it approaches
    ``population_pivot`` as attempts grow.

    Args:
        population_pivot: Population median raw difficulty — the fully-trusted pivot.
        attempted_users: Unique users who submitted at least once.

    Returns:
        float: Pivot between ``_NEUTRAL_PIVOT`` and ``population_pivot``.
    """
    trust = 1.0 - exp(-attempted_users / PIVOT_BLEND_SCALE)
    return _NEUTRAL_PIVOT + trust * (population_pivot - _NEUTRAL_PIVOT)


def _apply_contrast(raw: float, pivot: float, attempted_users: int) -> int:
    """Reshape a raw difficulty toward the extremes and map onto internal [1, 100].

    Applies a logistic gain in logit space, recentred on ``pivot``: values above
    the pivot are pushed up, values below are pushed down, and the steepness grows
    with attempt count. A problem exactly at the pivot maps to the scale centre.

    Args:
        raw: Raw difficulty in [0, 1] from ``_raw_difficulty``.
        pivot: Difficulty that maps to the scale centre — the population median
            raw, or ``_NEUTRAL_PIVOT`` when no problem has enough data.
        attempted_users: Unique attempters, used to gate the contrast gain.

    Returns:
        int: Difficulty score in [1, 100].
    """
    eps = 1e-6
    raw = min(1.0 - eps, max(eps, raw))
    pivot = min(1.0 - eps, max(eps, pivot))
    z = log(raw / (1.0 - raw)) - log(pivot / (1.0 - pivot))
    contrasted = 1.0 / (1.0 + exp(-_contrast_gain(attempted_users) * z))
    difficulty = max(1.0, min(100.0, 1.0 + 99.0 * contrasted))
    return round(difficulty)


def _points_for_difficulty(difficulty: int) -> float:
    """Return the points awarded for solving a problem at the given internal difficulty.

    The exponent maps the internal [1, 100] scale onto the same exponential curve
    as the old [1, 10] scale: internal 1 → GROWTH^0 (= BASE_POINTS, easiest) and
    internal 100 → GROWTH^9 (hardest), preserving identical point values for all
    equivalent display-scale difficulty levels.

    Args:
        difficulty: Internal integer difficulty in [1, 100].

    Returns:
        float: Exponential point value; difficulty-1 ≈ 10, difficulty-100 ≈ 283.
    """
    clamped = max(1, min(100, difficulty))
    return float(BASE_POINTS * (GROWTH ** ((clamped - 1) / 11.0)))


def format_next_rating_update(next_update: datetime | None) -> str | None:
    """Return a relative phrase for the next rating recomputation.

    Args:
        next_update: Scheduled UTC-aware datetime for the next problem rating cycle.

    Returns:
        Human-readable duration such as ``"2 hours"``, or ``None`` when the
        scheduler has not published a next update yet.
    """
    if next_update is None:
        return None

    if next_update.tzinfo is None:
        next_update = next_update.replace(tzinfo=UTC)

    if next_update <= datetime.now(UTC):
        return "less than a minute"

    relative_time, _direction = DateTimeUtils.relative_datetime(next_update)
    return str(relative_time)


def format_rating_interval(seconds: int | None) -> str | None:
    """Format the rating worker interval for display.

    Args:
        seconds: Interval length in seconds, or ``None`` when unpublished.

    Returns:
        Human-readable interval using days, hours, minutes, and seconds.
    """
    if seconds is None or seconds <= 0:
        return None

    units = (
        ("day", 86400),
        ("hour", 3600),
        ("minute", 60),
        ("second", 1),
    )
    parts: list[str] = []
    remaining = seconds
    for label, unit_seconds in units:
        value, remaining = divmod(remaining, unit_seconds)
        if value == 0:
            continue
        suffix = "" if value == 1 else "s"
        parts.append(f"{value} {label}{suffix}")

    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _publish_next_rating_update(
    callback: NextRatingUpdateCallback | None,
    next_update: datetime | None,
) -> None:
    """Publish the next scheduler deadline if a callback was provided.

    Args:
        callback: Optional receiver for the next scheduled rating update.
        next_update: Datetime to expose, or ``None`` when no deadline is active.
    """
    if callback is not None:
        callback(next_update)


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

_HISTORY_RETENTION_DAYS = 730  # 24 months


async def _should_record_history(
    session: AsyncSession,
    *,
    history_table: object,
    entity_col: object,
    entity_id: str,
    new_rating: int,
) -> bool:
    """Return True when a new history row should be written for this entity.

    Skips the insert when the most-recent history entry already carries the same
    rating value AND will not be removed by the upcoming 24-month cleanup.  This
    avoids storing duplicate rows for stable ratings while preserving at least one
    entry per entity across the retention window.

    Args:
        session: Active async database session.
        history_table: SQLAlchemy Core Table for the relevant history table.
        entity_col: The FK column on that table (e.g. ``c.problem_id``).
        entity_id: UUID of the entity being evaluated.
        new_rating: Rating value that would be inserted.

    Returns:
        bool: True if a new row should be inserted, False if it can be skipped.
    """
    from sqlalchemy import Column, Table  # local import to avoid circular at module level

    tbl: Table = history_table  # type: ignore[assignment]
    col: Column[str] = entity_col  # type: ignore[assignment]
    last = (
        await session.execute(
            select(tbl.c.rating, tbl.c.computed_at).where(col == entity_id).order_by(tbl.c.computed_at.desc()).limit(1)
        )
    ).first()

    if last is None:
        return True
    if last.rating != new_rating:
        return True
    cutoff = datetime.now(UTC) - timedelta(days=_HISTORY_RETENTION_DAYS)
    return bool(last.computed_at < cutoff)


# ---------------------------------------------------------------------------
# Per-entity rating functions (keyword-only to prevent positional swaps)
# ---------------------------------------------------------------------------


async def _recompute_stats_for_problem(session: AsyncSession, problem_id: str) -> None:
    """Rewrite arena_problem_ratings stats from raw tables, excluding the owner.

    Called at the start of each full rating cycle so that the problem owner's
    own submissions are never factored into problem difficulty. User roles do
    not affect the counting rule.

    Args:
        session: Active async database session.
        problem_id: UUID of the Arena problem to refresh.
    """
    owner_id = await session.scalar(select(_arena_problems.c.owner_id).where(_arena_problems.c.id == problem_id))

    attempted = (
        await session.scalar(
            select(func.count(func.distinct(_arena_submissions.c.user_id))).where(
                _arena_submissions.c.problem_id == problem_id,
                counts_toward_problem_rating(_arena_submissions.c.user_id, owner_id),
            )
        )
        or 0
    )

    solved = (
        await session.scalar(
            select(func.count())
            .select_from(_arena_problem_solvers)
            .where(
                _arena_problem_solvers.c.problem_id == problem_id,
                counts_toward_problem_rating(_arena_problem_solvers.c.user_id, owner_id),
            )
        )
        or 0
    )

    # For each counted solver, count their submissions up to (and including) their first AC.
    tries_per_solver = (
        select(func.count(_arena_submissions.c.id))
        .where(
            _arena_submissions.c.problem_id == problem_id,
            _arena_submissions.c.user_id == _arena_problem_solvers.c.user_id,
            _arena_submissions.c.created_at <= _arena_problem_solvers.c.solved_at,
        )
        .correlate(_arena_problem_solvers)
        .scalar_subquery()
    )
    total_tries = (
        await session.scalar(
            select(func.coalesce(func.sum(tries_per_solver), 0))
            .select_from(_arena_problem_solvers)
            .where(
                _arena_problem_solvers.c.problem_id == problem_id,
                counts_toward_problem_rating(_arena_problem_solvers.c.user_id, owner_id),
            )
        )
        or 0
    )

    await session.execute(
        update(_arena_problem_rating)
        .where(_arena_problem_rating.c.problem_id == problem_id)
        .values(
            attempted_users=attempted,
            solved_users=solved,
            total_tries_before_solve=total_tries,
        )
    )


async def _ensure_and_load_stats(session: AsyncSession, problem_id: str) -> tuple[int, int, int]:
    """Ensure a rating row exists and return the problem's rating inputs.

    Inserts a defaults row when the problem has never been attempted.

    Args:
        session: Active async database session.
        problem_id: UUID of the Arena problem.

    Returns:
        tuple: (attempted_users, solved_users, total_tries_before_solve).
    """
    existing = await session.scalar(
        select(_arena_problem_rating.c.problem_id).where(_arena_problem_rating.c.problem_id == problem_id)
    )
    if existing is None:
        await session.execute(_arena_problem_rating.insert().values(problem_id=problem_id))
        await session.flush()

    row = (
        await session.execute(
            select(
                _arena_problem_rating.c.attempted_users,
                _arena_problem_rating.c.solved_users,
                _arena_problem_rating.c.total_tries_before_solve,
            ).where(_arena_problem_rating.c.problem_id == problem_id)
        )
    ).one()
    return row.attempted_users, row.solved_users, row.total_tries_before_solve


async def _persist_problem_rating(session: AsyncSession, problem_id: str, difficulty: int, now: datetime) -> None:
    """Persist a computed problem difficulty and append a history row if changed.

    Args:
        session: Active async database session.
        problem_id: UUID of the Arena problem.
        difficulty: Internal difficulty in [1, 100].
        now: Timestamp recorded as the update/computation time.
    """
    await session.execute(
        update(_arena_problem_rating)
        .where(_arena_problem_rating.c.problem_id == problem_id)
        .values(rating=difficulty, dta_rating_update=now)
    )
    if await _should_record_history(
        session,
        history_table=_arena_problem_rating_history,
        entity_col=_arena_problem_rating_history.c.problem_id,
        entity_id=problem_id,
        new_rating=difficulty,
    ):
        await session.execute(
            _arena_problem_rating_history.insert().values(problem_id=problem_id, rating=difficulty, computed_at=now)
        )


async def rate_problem(*, session: AsyncSession, problem_id: str, pivot: float | None = None) -> None:
    """Compute and persist the difficulty rating for a single Arena problem.

    Ensures an ``arena_problem_ratings`` row exists (inserting defaults when the
    problem has never been attempted), computes the raw Bayesian difficulty,
    applies the bimodal contrast transform, and updates ``rating`` +
    ``dta_rating_update``.

    Does **not** commit; caller is responsible for the transaction.

    Args:
        session: Active async database session.
        problem_id: UUID of the Arena problem to rate.
        pivot: Population median raw used as the fully-trusted contrast pivot.
            Defaults to ``_NEUTRAL_PIVOT`` when the problem is rated in isolation.
            The pivot actually applied is ramped from ``_NEUTRAL_PIVOT`` toward
            this value by the problem's attempt count (see ``_effective_pivot``),
            so a low-evidence problem still maps near the scale centre.
    """
    attempted, solved, tries = await _ensure_and_load_stats(session, problem_id)
    raw = _raw_difficulty(
        attempted_users=attempted,
        solved_users=solved,
        total_tries_before_solve=tries,
    )
    population_pivot = _NEUTRAL_PIVOT if pivot is None else pivot
    difficulty = _apply_contrast(raw, _effective_pivot(population_pivot, attempted), attempted)
    await _persist_problem_rating(session, problem_id, difficulty, datetime.now(UTC))


async def rate_all_problems(session: AsyncSession) -> int:
    """Rate every Arena problem with a population-balanced bimodal contrast.

    Runs in two passes over all ``arena_problems`` rows (so problems without a
    rating row are created with default stats and rated too): the first pass
    computes each problem's raw difficulty and derives the contrast pivot from the
    median raw of sufficiently-attempted problems; the second applies the gated
    contrast and persists, with each problem's pivot ramped from the neutral
    centre toward the population median by its own attempt count. Centring the
    contrast on the population median keeps the easy/hard split balanced, while the
    per-problem pivot ramp keeps low-evidence problems near the scale centre.

    Args:
        session: Active async database session.

    Returns:
        int: Number of problems rated.
    """
    problem_ids = list(await session.scalars(select(_arena_problems.c.id)))

    raws: dict[str, float] = {}
    attempts: dict[str, int] = {}
    for pid in problem_ids:
        await _recompute_stats_for_problem(session, pid)
        attempted, solved, tries = await _ensure_and_load_stats(session, pid)
        raws[pid] = _raw_difficulty(
            attempted_users=attempted,
            solved_users=solved,
            total_tries_before_solve=tries,
        )
        attempts[pid] = attempted

    confident = [raws[pid] for pid in problem_ids if attempts[pid] >= PIVOT_MIN_ATTEMPTS]
    pivot = statistics.median(confident) if confident else _NEUTRAL_PIVOT

    now = datetime.now(UTC)
    difficulties: list[int] = []
    for pid in problem_ids:
        # Each problem's pivot ramps from the neutral centre toward the population
        # median by its own attempt count, so low-data problems map near the scale
        # centre (display 5.0) instead of inheriting a rating from population skew.
        # At zero attempts this returns exactly _NEUTRAL_PIVOT.
        difficulty = _apply_contrast(raws[pid], _effective_pivot(pivot, attempts[pid]), attempts[pid])
        await _persist_problem_rating(session, pid, difficulty, now)
        difficulties.append(difficulty)

    await persist_difficulty_histogram(session, difficulties, now)

    cutoff = datetime.now(UTC) - timedelta(days=730)
    await session.execute(
        delete(_arena_problem_rating_history).where(_arena_problem_rating_history.c.computed_at < cutoff)
    )
    return len(problem_ids)


async def rate_user(*, session: AsyncSession, user_id: str) -> None:
    """Compute and persist the rating score for a single Arena user.

    Reads all problems solved by the user from ``arena_problem_solvers`` except
    problems owned by that same user, sums exponential point values based on
    current problem difficulties, and updates ``user_rating``,
    ``solved_problems``, and ``dta_rating_update``.

    Users with no solved problems receive score 0 and solved_problems 0.
    Does **not** commit; caller is responsible for the transaction.

    Args:
        session: Active async database session.
        user_id: UUID of the Arena user to rate.
    """
    rows = (
        await session.execute(
            select(_arena_problem_rating.c.rating)
            .join(
                _arena_problem_solvers,
                _arena_problem_rating.c.problem_id == _arena_problem_solvers.c.problem_id,
            )
            .join(_arena_problems, _arena_problems.c.id == _arena_problem_solvers.c.problem_id)
            .where(
                _arena_problem_solvers.c.user_id == user_id,
                _arena_problems.c.owner_id != user_id,
            )
        )
    ).all()

    score = sum(_points_for_difficulty(row.rating) for row in rows)
    solved_count = len(rows)

    now = datetime.now(UTC)
    rounded_score = round(score)
    await session.execute(
        update(_arena_users)
        .where(_arena_users.c.id == user_id)
        .values(
            user_rating=rounded_score,
            solved_problems=solved_count,
            dta_rating_update=now,
        )
    )
    if await _should_record_history(
        session,
        history_table=_arena_user_rating_history,
        entity_col=_arena_user_rating_history.c.user_id,
        entity_id=user_id,
        new_rating=rounded_score,
    ):
        await session.execute(
            _arena_user_rating_history.insert().values(user_id=user_id, rating=rounded_score, computed_at=now)
        )


async def rate_all_users(session: AsyncSession) -> int:
    """Rate every Arena user, regardless of role.

    Args:
        session: Active async database session.

    Returns:
        int: Number of users rated.
    """
    user_ids = list(await session.scalars(select(_arena_users.c.id)))
    for uid in user_ids:
        await rate_user(session=session, user_id=uid)
    cutoff = datetime.now(UTC) - timedelta(days=730)
    await session.execute(delete(_arena_user_rating_history).where(_arena_user_rating_history.c.computed_at < cutoff))
    return len(user_ids)


# ---------------------------------------------------------------------------
# Affiliation rating
# ---------------------------------------------------------------------------


def _compute_affiliation_rating(scores: list[int], f: float) -> int:
    """Apply the geometric weighting formula to a sorted list of member scores.

    S = (1/f) * Σ(i=0…n-1) (1 − 1/f)^i * s_i   (scores sorted descending)

    For n→∞ weights sum to 1; for finite n the result is bounded by the top score.

    Args:
        scores: Member user_rating values (any order; sorted internally).
        f: Geometric decay factor (≥ 2). Larger f = slower decay.

    Returns:
        int: Rounded affiliation rating (0 for an empty group).
    """
    if not scores:
        return 0
    w = 1.0 / f
    decay = 1.0 - w
    return round(sum(w * (decay**i) * s for i, s in enumerate(sorted(scores, reverse=True))))


async def rate_affiliation(*, session: AsyncSession, affiliation_id: str, f: float) -> None:
    """Compute and persist the rating for a single Arena affiliation.

    Reads all non-null ``user_rating`` values from members of this affiliation,
    applies ``_compute_affiliation_rating``, and updates ``rating`` +
    ``dta_rating_update``.

    Does **not** commit; caller is responsible for the transaction.

    Args:
        session: Active async database session.
        affiliation_id: UUID of the affiliation to rate.
        f: Geometric decay factor forwarded to the formula.
    """
    rows = (
        await session.execute(
            select(_arena_users.c.user_rating).where(
                _arena_users.c.affiliation_id == affiliation_id,
                _arena_users.c.user_rating.is_not(None),
                _arena_users.c.ranking_visible.is_(True),
            )
        )
    ).all()

    scores = [row.user_rating for row in rows if row.user_rating is not None]
    rating = _compute_affiliation_rating(scores, f)

    now = datetime.now(UTC)
    await session.execute(
        update(_arena_affiliations)
        .where(_arena_affiliations.c.id == affiliation_id)
        .values(rating=rating, dta_rating_update=now)
    )
    if await _should_record_history(
        session,
        history_table=_arena_affiliation_rating_history,
        entity_col=_arena_affiliation_rating_history.c.affiliation_id,
        entity_id=affiliation_id,
        new_rating=rating,
    ):
        await session.execute(
            _arena_affiliation_rating_history.insert().values(
                affiliation_id=affiliation_id, rating=rating, computed_at=now
            )
        )


async def rate_all_affiliations(session: AsyncSession, f: float) -> int:
    """Rate every Arena affiliation.

    Args:
        session: Active async database session.
        f: Geometric decay factor forwarded to ``rate_affiliation``.

    Returns:
        int: Number of affiliations rated.
    """
    affiliation_ids = list(
        await session.scalars(
            select(_arena_affiliations.c.id).where(_arena_affiliations.c.exclude_from_ranking.is_(False))
        )
    )
    for aff_id in affiliation_ids:
        await rate_affiliation(session=session, affiliation_id=aff_id, f=f)
    cutoff = datetime.now(UTC) - timedelta(days=730)
    await session.execute(
        delete(_arena_affiliation_rating_history).where(_arena_affiliation_rating_history.c.computed_at < cutoff)
    )
    return len(affiliation_ids)
