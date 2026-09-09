#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""PostgreSQL coverage for the per-pair solver reconciliation lock.

The lock in ``autojudge/db/_arena_solver.py`` is a transaction-scoped advisory
lock, and the implementation returns early under SQLite, which has no advisory
locks and needs none. Every other test in the suite therefore runs the
reconciliation with the lock compiled out, so the behavior it exists for is only
observable here.

What it exists for: two judgments for the same ``(user, problem)`` pair settling
together. Without serialization both read a live AC set with no solver row, both
decide to insert, and the ``(problem_id, user_id)`` primary key turns the loser
into an aborted transaction that loses its verdict write. The default worker
concurrency is four slots and a bulk rejudge queues every submission of a
problem at once, so this is routine rather than theoretical.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from _autojudge_db_seeds import _make_arena_user, _make_language
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.pool import NullPool

from arena.config import settings
from arena.database import create_engine
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionJudgment
from autojudge.db import open_db
from shared.db_schema.arena import (
    arena_problem_solvers,
    arena_problems,
    arena_submission_judgments,
    arena_submissions,
    arena_users,
)
from shared.enumerations import JudgmentStatus, ProblemValidatorType, Verdict
from tests.conftest import skip_unless_schema_at_head
from web.models.language import Language


class _SeededPair:
    """Identifiers for one committed ``(user, problem)`` pair with two live ACs.

    Attributes:
        user_id: The seeded Arena user.
        problem_id: The seeded Arena problem.
    """

    def __init__(self, user_id: str, problem_id: str) -> None:
        self.user_id = user_id
        self.problem_id = problem_id


@pytest_asyncio.fixture
async def seeded_pair() -> AsyncIterator[tuple[AsyncEngine, _SeededPair]]:
    """Commit one pair with two Accepted submissions to a real PostgreSQL database.

    Follows the suite's "try, then skip" contract for real-service fixtures: the
    default credentials point at a database that does not exist locally, so an
    unreachable server skips instead of failing.

    The rows are committed rather than held in a rolled-back transaction,
    because the behavior under test is two *separate* connections contending,
    which a single open transaction cannot express. Seeding goes through the
    same ORM helpers the SQLite tests use, so this fixture cannot drift from the
    real column set. Everything seeded is removed again in teardown.

    Yields:
        tuple: The engine and the seeded pair's identifiers.
    """
    engine = create_engine(settings.db_url, poolclass=NullPool)
    safe_url = engine.url.render_as_string(hide_password=True)
    suffix = uuid.uuid4().hex[:8]
    ids: dict[str, str] = {}
    submission_ids: list[str] = []
    try:
        try:
            connection = await engine.connect()
        except Exception as exc:  # noqa: BLE001 -- drivers raise unwrapped errors here
            pytest.skip(f"PostgreSQL at {safe_url} is unavailable for tests: {exc}")
        await skip_unless_schema_at_head(connection, safe_url)
        await connection.close()

        now = datetime.now(UTC)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            lang = _make_language(session, lang_id=f"solver-lock-{suffix}")
            user = _make_arena_user(session)
            await session.flush()
            problem = ArenaProblem(
                arena_number=2_100_000_000 + int(suffix[:6], 16) % 1_000_000,
                title=f"Solver Lock {suffix}",
                owner_id=user.id,
                problem_statement="<p>Echo.</p>",
                validator_type=ProblemValidatorType.STANDARD,
            )
            session.add(problem)
            await session.flush()
            for index in range(2):
                submission = ArenaSubmission(
                    user_id=user.id,
                    problem_id=problem.id,
                    language_id=lang.id,
                    source_code=f"solution {index}",
                    source_hash=f"{index:064d}",
                    source_size_bytes=10,
                    created_at=now + timedelta(seconds=index),
                )
                session.add(submission)
                await session.flush()
                submission_ids.append(submission.id)
                session.add(
                    ArenaSubmissionJudgment(
                        submission_id=submission.id,
                        status=JudgmentStatus.DONE.value,
                        autojudge_verdict=Verdict.AC.value,
                        final_verdict=Verdict.AC.value,
                        finished_at=now + timedelta(seconds=30 + index),
                    )
                )
            ids = {"user": user.id, "problem": problem.id, "language": lang.id}
            await session.commit()

        yield engine, _SeededPair(ids["user"], ids["problem"])
    finally:
        try:
            if ids:
                async with engine.begin() as conn:
                    await conn.execute(
                        delete(arena_problem_solvers).where(arena_problem_solvers.c.problem_id == ids["problem"])
                    )
                    await conn.execute(
                        delete(arena_submission_judgments).where(
                            arena_submission_judgments.c.submission_id.in_(submission_ids)
                        )
                    )
                    await conn.execute(delete(arena_submissions).where(arena_submissions.c.id.in_(submission_ids)))
                    await conn.execute(delete(arena_problems).where(arena_problems.c.id == ids["problem"]))
                    await conn.execute(delete(arena_users).where(arena_users.c.id == ids["user"]))
                    await conn.execute(delete(Language.__table__).where(Language.__table__.c.id == ids["language"]))
        finally:
            await engine.dispose()


@pytest.mark.real_db
async def test_concurrent_reconciliation_of_one_pair_does_not_race(
    seeded_pair: tuple[AsyncEngine, _SeededPair],
) -> None:
    """Two judgments settling together on one pair must produce exactly one row.

    Both connections observe a live AC and no solver row, so both would insert
    without the advisory lock and the second would abort on the primary key.
    With it, the second blocks, then reads the row the first committed and finds
    nothing left to change.
    """
    engine, pair = seeded_pair

    async def reconcile() -> None:
        async with open_db(engine) as db:
            await db.reconcile_arena_solver(pair.user_id, pair.problem_id, verdict_is_ac=True)
            await db._conn.commit()

    results = await asyncio.gather(reconcile(), reconcile(), return_exceptions=True)
    assert [r for r in results if isinstance(r, BaseException)] == []

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(arena_problem_solvers.c.solved_at).where(
                    arena_problem_solvers.c.problem_id == pair.problem_id,
                    arena_problem_solvers.c.user_id == pair.user_id,
                )
            )
        ).all()
    assert len(rows) == 1


@pytest.mark.real_db
async def test_concurrent_reconciliation_converges_on_the_first_live_ac(
    seeded_pair: tuple[AsyncEngine, _SeededPair],
) -> None:
    """Whichever connection wins, the stored anchor is the earliest submission's.

    The rule is derived from committed rows rather than from which result
    arrived last, so the outcome must not depend on the interleaving.
    """
    engine, pair = seeded_pair

    async def reconcile() -> None:
        async with open_db(engine) as db:
            await db.reconcile_arena_solver(pair.user_id, pair.problem_id, verdict_is_ac=True)
            await db._conn.commit()

    await asyncio.gather(reconcile(), reconcile(), reconcile(), reconcile())

    async with engine.connect() as conn:
        stored = await conn.scalar(
            select(arena_problem_solvers.c.solved_at).where(
                arena_problem_solvers.c.problem_id == pair.problem_id,
                arena_problem_solvers.c.user_id == pair.user_id,
            )
        )
        earliest = await conn.scalar(
            select(arena_submission_judgments.c.finished_at)
            .select_from(
                arena_submissions.join(
                    arena_submission_judgments,
                    arena_submission_judgments.c.submission_id == arena_submissions.c.id,
                )
            )
            .where(arena_submissions.c.problem_id == pair.problem_id)
            .order_by(arena_submissions.c.created_at)
            .limit(1)
        )
    assert stored == earliest
