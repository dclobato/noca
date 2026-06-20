#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for compute_all_problem_statistics (shared.services.arena_stats)."""

from __future__ import annotations

import uuid

import pytest
from _helpers import _make_admin, _make_problem, _make_user
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import languages
from shared.db_schema.arena import (
    arena_problem_statistics,
    arena_submission_judgments,
    arena_submissions,
)
from shared.services.arena_stats import HISTOGRAM_BINS, compute_all_problem_statistics


async def _make_language(session: AsyncSession, name: str) -> str:
    lang_id = f"lang-{uuid.uuid4().hex[:6]}"
    await session.execute(
        insert(languages).values(
            id=lang_id,
            name=name,
            icon="devicon-test",
            compile_image="noca/test:compile",
            run_image="noca/test:run",
            compile_cmd=["true"],
            run_cmd=["true"],
            source_filename="sol.txt",
            artifact_path="/sandbox/sol.txt",
            artifact_is_source=True,
            compile_timeout_s=10.0,
            active=True,
        )
    )
    await session.flush()
    return lang_id


async def _make_submission(
    session: AsyncSession,
    *,
    user_id: str,
    problem_id: str,
    language_id: str,
    verdict: str | None,
    wall_ms: int | None = None,
    memory_kb: int | None = None,
    status: str = "DONE",
) -> None:
    sub_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_submissions).values(
            id=sub_id,
            user_id=user_id,
            problem_id=problem_id,
            language_id=language_id,
            source_code="x",
            source_hash="a" * 64,
            source_size_bytes=1,
        )
    )
    await session.execute(
        insert(arena_submission_judgments).values(
            id=str(uuid.uuid4()),
            submission_id=sub_id,
            status=status,
            final_verdict=verdict,
            autojudge_verdict=verdict,
            max_wall_time_ms=wall_ms,
            max_memory_kb=memory_kb,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_compute_statistics_distributions_and_ac_aggregates(session: AsyncSession) -> None:
    """Verdict/language counts cover counted submissions; time/memory cover AC only."""
    author = await _make_user(session)
    user = await _make_user(session)
    problem = await _make_problem(session, author)  # default time_limit_ms = 1000
    py = await _make_language(session, "Python 3")
    cpp = await _make_language(session, "C++")

    # Python: 2 AC (100ms, 300ms), 1 WA
    await _make_submission(
        session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=py,
        verdict="AC",
        wall_ms=100,
        memory_kb=2000,
    )
    await _make_submission(
        session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=py,
        verdict="AC",
        wall_ms=300,
        memory_kb=4000,
    )
    await _make_submission(
        session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=py,
        verdict="WA",
    )
    # C++: 1 AC (50ms)
    await _make_submission(
        session,
        user_id=user.id,
        problem_id=problem.id,
        language_id=cpp,
        verdict="AC",
        wall_ms=50,
        memory_kb=1000,
    )

    count = await compute_all_problem_statistics(session)
    await session.commit()
    assert count == 1

    data = await session.scalar(
        select(arena_problem_statistics.c.data).where(arena_problem_statistics.c.problem_id == problem.id)
    )
    assert data is not None

    # Distributions cover all 4 judged submissions.
    assert data["total_submissions"] == 4
    verdicts = {v["verdict"]: v["count"] for v in data["verdicts"]}
    assert verdicts == {"AC": 3, "WA": 1}
    langs = {lang["language_id"]: lang["count"] for lang in data["languages"]}
    assert langs == {py: 3, cpp: 1}

    # Time stats: AC only. Python AC walls = [100, 300] → avg 200.
    time_by_lang = {row["language_id"]: row for row in data["time_stats"]}
    assert time_by_lang[py]["count"] == 2
    assert time_by_lang[py]["avg_ms"] == 200.0
    assert time_by_lang[py]["stddev_ms"] == 100.0
    assert time_by_lang[cpp]["count"] == 1
    assert time_by_lang[cpp]["avg_ms"] == 50.0
    assert time_by_lang[cpp]["stddev_ms"] == 0.0

    # Memory stats: AC only.
    mem_by_lang = {row["language_id"]: row for row in data["memory_stats"]}
    assert mem_by_lang[py]["avg_kb"] == 3000.0

    # Histogram: 20 bins over [0, 1000]; bin counts sum to AC count per language.
    assert data["histogram_bins"] == HISTOGRAM_BINS
    assert data["time_limit_ms"] == 1000
    hist_by_lang = {row["language_id"]: row["counts"] for row in data["wall_time_histogram"]}
    assert len(hist_by_lang[py]) == HISTOGRAM_BINS
    assert sum(hist_by_lang[py]) == 2
    assert sum(hist_by_lang[cpp]) == 1
    # 100ms → bin 2 (100/1000*20), 300ms → bin 6.
    assert hist_by_lang[py][2] == 1
    assert hist_by_lang[py][6] == 1


@pytest.mark.asyncio
async def test_compute_statistics_excludes_admin_and_author(session: AsyncSession) -> None:
    """Statistics must exclude ARENA_ADMIN and the problem owner; judges/users count."""
    author = await _make_user(session)
    user = await _make_user(session)
    admin = await _make_admin(session)
    problem = await _make_problem(session, author)
    py = await _make_language(session, "Python 3")

    # Counted: a regular user's AC submission.
    await _make_submission(
        session, user_id=user.id, problem_id=problem.id, language_id=py, verdict="AC", wall_ms=100, memory_kb=2000
    )
    # Excluded: the author's own submission.
    await _make_submission(session, user_id=author.id, problem_id=problem.id, language_id=py, verdict="WA")
    # Excluded: an admin's submission.
    await _make_submission(session, user_id=admin.id, problem_id=problem.id, language_id=py, verdict="WA")

    count = await compute_all_problem_statistics(session)
    await session.commit()
    assert count == 1

    data = await session.scalar(
        select(arena_problem_statistics.c.data).where(arena_problem_statistics.c.problem_id == problem.id)
    )
    assert data is not None
    # Only the regular user's single AC submission is counted.
    assert data["total_submissions"] == 1
    assert {v["verdict"]: v["count"] for v in data["verdicts"]} == {"AC": 1}


@pytest.mark.asyncio
async def test_compute_statistics_skips_problems_without_judged_submissions(
    session: AsyncSession,
) -> None:
    """A problem with no judged submissions gets no statistics row."""
    user = await _make_user(session)
    await _make_problem(session, user)

    count = await compute_all_problem_statistics(session)
    await session.commit()

    assert count == 0
    rows = (await session.execute(select(arena_problem_statistics.c.problem_id))).all()
    assert rows == []
