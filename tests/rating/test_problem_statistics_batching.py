#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Batching and streaming behaviour of compute_all_problem_statistics."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import pytest
from _helpers import _make_problem, _make_user
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

import shared.services.arena_problem_stats as stats_module
from shared.db_schema import languages
from shared.db_schema.arena import (
    arena_problem_solvers,
    arena_problem_statistics,
    arena_submission_judgments,
    arena_submissions,
)
from shared.services.arena_problem_stats import compute_all_problem_statistics


async def _make_language(session: AsyncSession) -> str:
    lang_id = f"lang-{uuid.uuid4().hex[:6]}"
    await session.execute(
        insert(languages).values(
            id=lang_id,
            name="Test",
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
    session: AsyncSession, *, user_id: str, problem_id: str, language_id: str, verdict: str = "AC"
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
            status="DONE",
            final_verdict=verdict,
            autojudge_verdict=verdict,
            max_wall_time_ms=10,
            max_memory_kb=100,
        )
    )
    await session.flush()


async def _seed_problems(session: AsyncSession, count: int) -> list[str]:
    """Seed ``count`` problems, each with one non-owner AC submission."""
    author = await _make_user(session)
    solver = await _make_user(session)
    language_id = await _make_language(session)
    problem_ids: list[str] = []
    for _ in range(count):
        problem = await _make_problem(session, author)
        await _make_submission(session, user_id=solver.id, problem_id=problem.id, language_id=language_id)
        problem_ids.append(problem.id)
    return problem_ids


async def _stored_problem_ids(session: AsyncSession) -> list[str]:
    return list((await session.execute(select(arena_problem_statistics.c.problem_id))).scalars())


@pytest.mark.asyncio
async def test_batches_cover_every_problem_with_submissions(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five problems in batches of two all get a snapshot; an idle problem gets none."""
    monkeypatch.setattr(stats_module, "PROBLEM_STATS_BATCH_SIZE", 2)
    problem_ids = await _seed_problems(session, 5)
    idle_problem = (await _make_problem(session, await _make_user(session))).id

    written = await compute_all_problem_statistics(session)

    assert written == 5
    stored = await _stored_problem_ids(session)
    assert sorted(stored) == sorted(problem_ids)
    assert idle_problem not in stored


@pytest.mark.asyncio
async def test_stream_partitions_keep_every_row(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """A partition size of one must not drop rows at partition boundaries."""
    monkeypatch.setattr(stats_module, "_STREAM_PARTITION_SIZE", 1)
    author = await _make_user(session)
    user = await _make_user(session)
    language_id = await _make_language(session)
    problem_id = (await _make_problem(session, author)).id
    for verdict in ("WA", "WA", "AC"):
        await _make_submission(
            session, user_id=user.id, problem_id=problem_id, language_id=language_id, verdict=verdict
        )

    assert await compute_all_problem_statistics(session) == 1
    data = (
        await session.execute(
            select(arena_problem_statistics.c.data).where(arena_problem_statistics.c.problem_id == problem_id)
        )
    ).scalar_one()
    assert data["total_submissions"] == 3


@pytest.mark.asyncio
async def test_unmatched_solvers_are_reported_once_across_batches(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Solver rows without a matching first-AC submission produce one aggregated warning."""
    monkeypatch.setattr(stats_module, "PROBLEM_STATS_BATCH_SIZE", 1)
    problem_ids = await _seed_problems(session, 2)
    solver = await _make_user(session)
    for problem_id in problem_ids:
        await session.execute(
            insert(arena_problem_solvers).values(
                problem_id=problem_id,
                user_id=solver.id,
                solved_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    await session.flush()

    with caplog.at_level(logging.WARNING, logger="shared.services.arena_problem_stats"):
        await compute_all_problem_statistics(session)

    warnings = [record for record in caplog.records if record.name == "shared.services.arena_problem_stats"]
    assert len(warnings) == 1
    assert "2 Arena solver row(s)" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_rerun_replaces_snapshots_without_duplicates(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running the rebuild twice leaves exactly one row per problem."""
    monkeypatch.setattr(stats_module, "PROBLEM_STATS_BATCH_SIZE", 2)
    problem_ids = await _seed_problems(session, 3)

    assert await compute_all_problem_statistics(session) == 3
    assert await compute_all_problem_statistics(session) == 3

    assert sorted(await _stored_problem_ids(session)) == sorted(problem_ids)


@pytest.mark.asyncio
async def test_failure_mid_rebuild_rolls_back_to_previous_snapshots(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure in a later batch leaves the previous snapshots intact once rolled back."""
    monkeypatch.setattr(stats_module, "PROBLEM_STATS_BATCH_SIZE", 1)
    problem_ids = await _seed_problems(session, 3)
    assert await compute_all_problem_statistics(session) == 3

    calls = 0
    original = stats_module.build_problem_statistics_payload

    def _explode_on_second(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("boom")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(stats_module, "build_problem_statistics_payload", _explode_on_second)

    with pytest.raises(RuntimeError):
        async with session.begin_nested():
            await compute_all_problem_statistics(session)

    assert sorted(await _stored_problem_ids(session)) == sorted(problem_ids)
