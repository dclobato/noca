#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena problem-difficulty rating (rate_problem / rate_all_problems)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from _helpers import (
    _make_admin,
    _make_judge,
    _make_language,
    _make_problem,
    _make_submission,
    _make_user,
    _record_solve,
    _seed_rating_row,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_problem_ratings, arena_rating_cycle_state
from shared.enumerations import ArenaExpectedDifficulty
from shared.services.arena_difficulty_display import MIN_ATTEMPTS_FOR_DISPLAY
from shared.services.arena_difficulty_histogram import BIN_COUNT, build_difficulty_histogram
from shared.services.arena_rating import (
    _NEUTRAL_PIVOT,
    CONTRAST_GAIN_MAX,
    PRIOR_SOLVE_RATE,
    _apply_contrast,
    _contrast_gain,
    _effective_pivot,
    _raw_difficulty,
    prior_solve_rate_for_difficulty,
    rate_all_problems,
    rate_problem,
)

# ---------------------------------------------------------------------------
# rate_problem tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_problem_creates_row_for_never_attempted(session: AsyncSession) -> None:
    """rate_problem must create a rating row when none exists and set dta_rating_update."""
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    await rate_problem(session=session, problem_id=problem.id)

    row = (
        await session.execute(
            select(
                arena_problem_ratings.c.rating,
                arena_problem_ratings.c.dta_rating_update,
            ).where(arena_problem_ratings.c.problem_id == problem.id)
        )
    ).one()
    assert 1 <= row.rating <= 100
    assert row.dta_rating_update is not None


@pytest.mark.asyncio
async def test_rate_problem_zero_attempts_near_prior(session: AsyncSession) -> None:
    """A problem with no attempts should land near the midpoint prior."""
    user = await _make_user(session)
    problem = await _make_problem(session, user)

    await rate_problem(session=session, problem_id=problem.id)

    row = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == problem.id)
    )
    assert row is not None
    # raw = neutral pivot (solve_rate=0.5, avg_tries=2) ≈ 0.4602.
    # gain(0 attempts)=1, and raw==pivot → contrast=0.5 → round(1+99*0.5)=50.
    assert row == 50


@pytest.mark.asyncio
async def test_rate_problem_easy_problem(session: AsyncSession) -> None:
    """A problem where everyone solves it on the first try should score near 1."""
    user = await _make_user(session)
    problem = await _make_problem(session, user)
    await _seed_rating_row(session, problem.id, attempted=100, solved=100, total_tries=100)

    await rate_problem(session=session, problem_id=problem.id)

    row = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == problem.id)
    )
    assert row is not None
    assert row <= 30


@pytest.mark.asyncio
async def test_rate_problem_hard_problem(session: AsyncSession) -> None:
    """A problem where almost nobody solves it after many tries should score near 10."""
    user = await _make_user(session)
    problem = await _make_problem(session, user)
    await _seed_rating_row(session, problem.id, attempted=200, solved=2, total_tries=800)

    await rate_problem(session=session, problem_id=problem.id)

    row = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == problem.id)
    )
    assert row is not None
    assert row >= 80


@pytest.mark.asyncio
async def test_rate_problem_difficulty_always_in_bounds(session: AsyncSession) -> None:
    """Difficulty must stay in [1, 10] for extreme inputs."""
    user = await _make_user(session)

    # Extreme easy: 10000 attempts, all AC on first try
    p_easy = await _make_problem(session, user)
    await _seed_rating_row(session, p_easy.id, attempted=10000, solved=10000, total_tries=10000)
    await rate_problem(session=session, problem_id=p_easy.id)
    easy_rating = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == p_easy.id)
    )
    assert easy_rating is not None
    assert 1 <= easy_rating <= 100

    # Extreme hard: 10000 attempts, nobody solved, 100 tries each
    p_hard = await _make_problem(session, user)
    await _seed_rating_row(session, p_hard.id, attempted=10000, solved=0, total_tries=0)
    await rate_problem(session=session, problem_id=p_hard.id)
    hard_rating = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == p_hard.id)
    )
    assert hard_rating is not None
    assert 1 <= hard_rating <= 100


# ---------------------------------------------------------------------------
# rate_all_problems tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_all_problems_covers_never_attempted(session: AsyncSession) -> None:
    """rate_all_problems must touch every arena_problems row including those without ratings."""
    user = await _make_user(session)
    p1 = await _make_problem(session, user)
    p2 = await _make_problem(session, user)
    # p1 has no rating row; p2 has an existing one
    await _seed_rating_row(session, p2.id, attempted=50, solved=25, total_tries=60)

    count = await rate_all_problems(session)

    assert count == 2
    for pid in (p1.id, p2.id):
        row = await session.scalar(
            select(arena_problem_ratings.c.dta_rating_update).where(arena_problem_ratings.c.problem_id == pid)
        )
        assert row is not None, f"dta_rating_update not set for problem {pid}"


@pytest.mark.asyncio
async def test_rate_all_problems_persists_difficulty_histogram(session: AsyncSession) -> None:
    """rate_all_problems snapshots measured problems only and counts the unmeasured rest."""
    owner = await _make_user(session)
    language = await _make_language(session)
    await _make_problem(session, owner)  # never attempted: unmeasured
    measured = await _make_problem(session, owner)
    nearly = await _make_problem(session, owner)
    # Stats are recomputed from real submissions at the start of the cycle, so
    # evidence has to come from submitters rather than a seeded rating row.
    for _ in range(MIN_ATTEMPTS_FOR_DISPLAY):
        solver = await _make_user(session)
        await _make_submission(session, solver.id, measured.id, language.id)
    for _ in range(MIN_ATTEMPTS_FOR_DISPLAY - 1):
        solver = await _make_user(session)
        await _make_submission(session, solver.id, nearly.id, language.id)

    count = await rate_all_problems(session)

    row = (
        await session.execute(
            select(arena_rating_cycle_state.c.data, arena_rating_cycle_state.c.computed_at).where(
                arena_rating_cycle_state.c.id == "singleton"
            )
        )
    ).one()
    assert row.data is not None
    assert row.computed_at is not None
    assert count == 3
    assert row.data["bins"] == BIN_COUNT
    assert len(row.data["counts"]) == BIN_COUNT
    assert row.data["total_problems"] == 1
    assert sum(row.data["counts"]) == 1
    assert row.data["unmeasured_problems"] == 2
    assert row.data["min_attempts"] == MIN_ATTEMPTS_FOR_DISPLAY


@pytest.mark.asyncio
async def test_rate_all_problems_counts_submissions_that_predate_the_rating_row(session: AsyncSession) -> None:
    """A problem attempted before it ever had a rating row is measured on its first cycle.

    The stats recompute is an UPDATE; if the defaults row were inserted only
    afterwards, that first cycle would report zero attempts and hide the problem
    behind the display gate for a whole interval.
    """
    owner = await _make_user(session)
    language = await _make_language(session)
    problem = await _make_problem(session, owner)
    for _ in range(3):
        attempter = await _make_user(session)
        await _make_submission(session, attempter.id, problem.id, language.id)

    await rate_all_problems(session)

    attempted = await session.scalar(
        select(arena_problem_ratings.c.attempted_users).where(arena_problem_ratings.c.problem_id == problem.id)
    )
    assert attempted == 3


@pytest.mark.asyncio
async def test_rate_all_problems_counts_staff_but_excludes_owner(session: AsyncSession) -> None:
    """Problem difficulty inputs should count all roles except the problem owner."""
    owner = await _make_user(session)
    admin = await _make_admin(session)
    judge = await _make_judge(session)
    problem = await _make_problem(session, owner)
    language = await _make_language(session)
    solved_at = datetime.now(UTC)

    await _make_submission(session, admin.id, problem.id, language.id, created_at=solved_at - timedelta(seconds=3))
    await _record_solve(session, admin.id, problem.id, solved_at=solved_at)
    await _make_submission(session, judge.id, problem.id, language.id, created_at=solved_at - timedelta(seconds=2))
    await _record_solve(session, judge.id, problem.id, solved_at=solved_at)
    await _make_submission(session, owner.id, problem.id, language.id, created_at=solved_at - timedelta(seconds=1))
    await _record_solve(session, owner.id, problem.id, solved_at=solved_at)

    await rate_all_problems(session)

    row = (
        await session.execute(
            select(
                arena_problem_ratings.c.attempted_users,
                arena_problem_ratings.c.solved_users,
                arena_problem_ratings.c.total_tries_before_solve,
            ).where(arena_problem_ratings.c.problem_id == problem.id)
        )
    ).one()
    assert row.attempted_users == 2
    assert row.solved_users == 2
    assert row.total_tries_before_solve == 2


# ---------------------------------------------------------------------------
# Difficulty histogram bucketing
# ---------------------------------------------------------------------------


def test_build_difficulty_histogram_buckets_edges() -> None:
    """Display difficulty 0.1 falls in bin 0; display difficulty 10.0 falls in the last bin."""
    payload = build_difficulty_histogram([1, 100])

    assert payload["bins"] == BIN_COUNT
    assert payload["total_problems"] == 2
    assert len(payload["counts"]) == BIN_COUNT
    assert payload["counts"][0] == 1
    assert payload["counts"][BIN_COUNT - 1] == 1


def test_build_difficulty_histogram_empty() -> None:
    """An empty difficulty list yields an all-zero histogram with total_problems 0."""
    payload = build_difficulty_histogram([])

    assert payload["total_problems"] == 0
    assert payload["counts"] == [0] * BIN_COUNT
    assert payload["unmeasured_problems"] == 0
    assert payload["min_attempts"] == MIN_ATTEMPTS_FOR_DISPLAY


def test_build_difficulty_histogram_records_unmeasured_count() -> None:
    """The excluded low-evidence problems are reported alongside the measured bins."""
    payload = build_difficulty_histogram([50], unmeasured_problems=7)

    assert payload["total_problems"] == 1
    assert payload["unmeasured_problems"] == 7


# ---------------------------------------------------------------------------
# Author-declared expected difficulty as the solve-rate prior
# ---------------------------------------------------------------------------


def _zero_attempt_rating(prior: float) -> int:
    raw = _raw_difficulty(attempted_users=0, solved_users=0, total_tries_before_solve=0, prior_solve_rate=prior)
    return _apply_contrast(raw, _effective_pivot(_NEUTRAL_PIVOT, 0), 0)


@pytest.mark.parametrize("anchor", list(ArenaExpectedDifficulty))
def test_declared_anchor_is_reproduced_exactly_at_zero_attempts(anchor: ArenaExpectedDifficulty) -> None:
    """A fresh problem rates exactly its declared anchor, not the flat centre."""
    assert _zero_attempt_rating(prior_solve_rate_for_difficulty(anchor.value)) == anchor.value


def test_prior_is_monotonic_in_the_declaration_and_bounded() -> None:
    """Harder declarations mean lower prior solve rates, within the clamp."""
    priors = [prior_solve_rate_for_difficulty(d) for d in range(1, 101)]
    assert priors == sorted(priors, reverse=True)
    assert all(0.01 <= p <= 0.99 for p in priors)


def test_no_declaration_reproduces_the_flat_prior_bit_for_bit() -> None:
    """Omitting the prior keyword is exactly PRIOR_SOLVE_RATE, so existing ratings do not move."""
    for attempted, solved, tries in ((0, 0, 0), (3, 1, 2), (50, 25, 60)):
        default = _raw_difficulty(attempted_users=attempted, solved_users=solved, total_tries_before_solve=tries)
        explicit = _raw_difficulty(
            attempted_users=attempted,
            solved_users=solved,
            total_tries_before_solve=tries,
            prior_solve_rate=PRIOR_SOLVE_RATE,
        )
        assert default == explicit


def test_evidence_overrides_a_wrong_declaration_as_attempts_accumulate() -> None:
    """Declared Hard, but everyone solves first try: the rating falls monotonically."""
    prior = prior_solve_rate_for_difficulty(ArenaExpectedDifficulty.HARD.value)
    ratings = []
    for attempted in (0, 3, 10, 25, 50):
        raw = _raw_difficulty(
            attempted_users=attempted,
            solved_users=attempted,
            total_tries_before_solve=attempted,
            prior_solve_rate=prior,
        )
        ratings.append(_apply_contrast(raw, _effective_pivot(_NEUTRAL_PIVOT, attempted), attempted))
    assert ratings[0] == ArenaExpectedDifficulty.HARD.value
    assert ratings == sorted(ratings, reverse=True)
    assert ratings[-1] < 20


@pytest.mark.asyncio
async def test_rate_all_problems_seeds_from_the_declared_difficulty(session: AsyncSession) -> None:
    """Two never-attempted problems rate at their declarations; an undeclared one at the centre."""
    owner = await _make_user(session)
    easy = await _make_problem(session, owner, expected_difficulty=ArenaExpectedDifficulty.EASY.value)
    hard = await _make_problem(session, owner, expected_difficulty=ArenaExpectedDifficulty.HARD.value)
    plain = await _make_problem(session, owner)

    await rate_all_problems(session)

    ratings = {
        pid: await session.scalar(
            select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == pid)
        )
        for pid in (easy.id, hard.id, plain.id)
    }
    assert ratings[easy.id] == ArenaExpectedDifficulty.EASY.value
    assert ratings[hard.id] == ArenaExpectedDifficulty.HARD.value
    assert ratings[plain.id] == 50


@pytest.mark.asyncio
async def test_rate_problem_seeds_from_the_declared_difficulty(session: AsyncSession) -> None:
    """The single-problem path reads the declaration too."""
    owner = await _make_user(session)
    problem = await _make_problem(session, owner, expected_difficulty=ArenaExpectedDifficulty.CHALLENGING.value)

    await rate_problem(session=session, problem_id=problem.id)

    rating = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == problem.id)
    )
    assert rating == ArenaExpectedDifficulty.CHALLENGING.value


# ---------------------------------------------------------------------------
# Raw difficulty (Bayesian solve-rate + avg-tries)
# ---------------------------------------------------------------------------


def test_raw_difficulty_weighted_components() -> None:
    """Raw difficulty is the weighted sum of the solve-rate and tries components."""
    raw = _raw_difficulty(attempted_users=10, solved_users=2, total_tries_before_solve=4)
    # solve_rate=(2+10*0.5)/(10+10)=0.35 → solve_c=0.65
    # avg_tries=(4+10*2)/(2+10)=2 → tries_c=ln(2)/ln(10)≈0.30103
    # raw=0.8*0.65+0.2*0.30103 = 0.58021
    assert raw == pytest.approx(0.58021, abs=1e-4)


def test_raw_difficulty_lower_solve_rate_is_harder() -> None:
    """Fewer solvers (lower solve rate) yields a higher raw difficulty."""
    easy = _raw_difficulty(attempted_users=20, solved_users=18, total_tries_before_solve=18)
    hard = _raw_difficulty(attempted_users=20, solved_users=2, total_tries_before_solve=8)
    assert hard > easy


def test_raw_difficulty_independent_of_attempt_count_at_fixed_rate() -> None:
    """Raw difficulty depends only on the rates, not on absolute population size."""
    small = _raw_difficulty(attempted_users=10, solved_users=5, total_tries_before_solve=10)
    large = _raw_difficulty(attempted_users=1000, solved_users=500, total_tries_before_solve=1000)
    # Same solve rate and avg tries → larger sample only sharpens via the prior,
    # so the larger population is at least as hard but never wildly different.
    assert large == pytest.approx(small, abs=0.1)


# ---------------------------------------------------------------------------
# Bimodal contrast transform
# ---------------------------------------------------------------------------


def test_contrast_pivot_maps_to_centre() -> None:
    """A raw equal to the pivot maps to the scale centre regardless of gain."""
    for n in (0, 5, 100, 10_000):
        assert _apply_contrast(_NEUTRAL_PIVOT, _NEUTRAL_PIVOT, n) == 50


def test_contrast_gain_gated_by_attempts() -> None:
    """Gain starts at 1 with no data and rises monotonically toward the max."""
    assert _contrast_gain(0) == pytest.approx(1.0)
    assert _contrast_gain(10) < _contrast_gain(50) < _contrast_gain(1000)
    assert _contrast_gain(10_000) == pytest.approx(CONTRAST_GAIN_MAX, abs=1e-3)


def test_contrast_pushes_away_from_pivot_with_data() -> None:
    """With evidence, a harder-than-pivot raw is pushed further toward the hard end."""
    raw = 0.70
    pushed = _apply_contrast(raw, _NEUTRAL_PIVOT, 1000)  # high gain
    linear = _apply_contrast(raw, _NEUTRAL_PIVOT, 0)  # gain == 1 (no reshaping)
    assert pushed > linear > 50


# ---------------------------------------------------------------------------
# Per-problem pivot blending (low-attempt problems pulled toward the centre)
# ---------------------------------------------------------------------------


def test_effective_pivot_zero_attempts_is_neutral() -> None:
    """With no attempts the effective pivot is exactly the neutral pivot."""
    assert _effective_pivot(0.20, 0) == pytest.approx(_NEUTRAL_PIVOT)


def test_effective_pivot_ramps_toward_population_with_attempts() -> None:
    """The pivot moves monotonically from neutral toward the population median."""
    pop = 0.20  # an "easy" population pivot, below neutral
    p_low = _effective_pivot(pop, 2)
    p_mid = _effective_pivot(pop, 10)
    p_high = _effective_pivot(pop, 1000)
    assert _NEUTRAL_PIVOT > p_low > p_mid > p_high
    assert p_high == pytest.approx(pop, abs=1e-3)


def test_effective_pivot_noop_when_population_equals_neutral() -> None:
    """When the population pivot is already neutral, attempts cannot move it."""
    for n in (0, 1, 25, 1000):
        assert _effective_pivot(_NEUTRAL_PIVOT, n) == pytest.approx(_NEUTRAL_PIVOT)


def test_low_attempt_problem_stays_near_centre_despite_skewed_pivot() -> None:
    """A 1-attempt problem maps near the centre even against an easy population pivot."""
    raw = _raw_difficulty(attempted_users=1, solved_users=1, total_tries_before_solve=1)
    skewed_pop = 0.21  # easy-skewed population median
    centred = _apply_contrast(raw, _effective_pivot(skewed_pop, 1), 1)
    unguarded = _apply_contrast(raw, skewed_pop, 1)  # measured directly against the skew
    assert unguarded > 70  # old behaviour pushed it to the hard end
    assert 40 <= centred <= 60  # new behaviour keeps it near the scale centre


# ---------------------------------------------------------------------------
# Problem-owner exclusion from problem stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_all_problems_counts_all_roles_but_excludes_owner(session: AsyncSession) -> None:
    """rate_all_problems counts every role but excludes the problem owner."""
    author = await _make_user(session)
    problem = await _make_problem(session, author)
    lang = await _make_language(session)
    t0 = datetime.now(UTC)

    # One regular user submits twice and solves on the second try (counts).
    user = await _make_user(session)
    await _make_submission(session, user.id, problem.id, lang.id, created_at=t0)
    await _make_submission(session, user.id, problem.id, lang.id, created_at=t0 + timedelta(seconds=1))
    await _record_solve(session, user.id, problem.id, solved_at=t0 + timedelta(seconds=2))

    # A judge submits five times and solves (now counts).
    judge = await _make_judge(session)
    for i in range(5):
        await _make_submission(session, judge.id, problem.id, lang.id, created_at=t0 + timedelta(seconds=i))
    await _record_solve(session, judge.id, problem.id, solved_at=t0 + timedelta(seconds=5))

    # An admin submits many times and solves (counts).
    admin = await _make_admin(session)
    for i in range(5):
        await _make_submission(session, admin.id, problem.id, lang.id, created_at=t0 + timedelta(seconds=i))
    await _record_solve(session, admin.id, problem.id, solved_at=t0 + timedelta(seconds=5))

    # The author submits and solves their own problem (excluded).
    for i in range(3):
        await _make_submission(session, author.id, problem.id, lang.id, created_at=t0 + timedelta(seconds=i))
    await _record_solve(session, author.id, problem.id, solved_at=t0 + timedelta(seconds=3))

    await rate_all_problems(session)

    row = (
        await session.execute(
            select(
                arena_problem_ratings.c.attempted_users,
                arena_problem_ratings.c.solved_users,
                arena_problem_ratings.c.total_tries_before_solve,
            ).where(arena_problem_ratings.c.problem_id == problem.id)
        )
    ).one()

    assert row.attempted_users == 3  # user + judge + admin (author excluded)
    assert row.solved_users == 3  # user + judge + admin
    # user: 2 tries before solve; judge/admin: 5 submissions each all <= solve_time.
    assert row.total_tries_before_solve == 12


@pytest.mark.asyncio
async def test_rate_all_problems_author_only_yields_zero_stats(session: AsyncSession) -> None:
    """A problem touched only by its owner must compute with zero-evidence stats."""
    author = await _make_user(session)
    problem = await _make_problem(session, author)
    lang = await _make_language(session)

    for _i in range(2):
        await _make_submission(session, author.id, problem.id, lang.id)

    await rate_all_problems(session)

    row = (
        await session.execute(
            select(
                arena_problem_ratings.c.attempted_users,
                arena_problem_ratings.c.solved_users,
                arena_problem_ratings.c.rating,
            ).where(arena_problem_ratings.c.problem_id == problem.id)
        )
    ).one()

    assert row.attempted_users == 0
    assert row.solved_users == 0
    # Zero-evidence → neutral difficulty (≈ 50)
    assert row.rating == 50


@pytest.mark.asyncio
async def test_difficulty_still_clamped_to_100(session: AsyncSession) -> None:
    """An extreme hard problem still clamps to 100."""
    user = await _make_user(session)
    problem = await _make_problem(session, user)
    await _seed_rating_row(session, problem.id, attempted=200, solved=0, total_tries=0)

    await rate_problem(session=session, problem_id=problem.id)

    row = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == problem.id)
    )
    assert row is not None
    assert 1 <= row <= 100
