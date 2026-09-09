#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""PostgreSQL coverage for the ``arena_user_badges.submission_id`` foreign key.

The `ON DELETE SET NULL` action is what keeps a deleted submission from taking
the badge it earned with it. SQLite does not enforce foreign-key actions unless
`PRAGMA foreign_keys` is on, which the suite's default session does not set, so
this behavior can only be observed against a real PostgreSQL database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from arena.config import settings
from arena.database import create_engine
from shared.db_schema import languages
from shared.db_schema.arena import (
    arena_problems,
    arena_submission_judgments,
    arena_submissions,
    arena_user_badges,
    arena_users,
)
from shared.enumerations import ArenaBadge, JudgmentStatus, ProblemValidatorType, Verdict
from shared.services.arena_badge_data import award_badge
from tests.conftest import skip_unless_schema_at_head

_WHEN = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def postgres_badge_session() -> AsyncIterator[AsyncSession]:
    """Yield a rolled-back session bound to a real PostgreSQL database.

    Follows the suite's "try, then skip" contract: an unreachable server skips
    instead of failing, and CI supplies real ``NOCA_DB_*`` values.
    """
    engine = create_engine(settings.db_url, poolclass=NullPool)
    # Never interpolate settings.db_url into output: it carries the password in
    # clear text, and skip reasons reach CI logs and JUnit artifacts.
    safe_url = engine.url.render_as_string(hide_password=True)
    try:
        try:
            connection = await engine.connect()
        except Exception as exc:
            pytest.skip(f"PostgreSQL at {safe_url} is unavailable for tests: {exc}")
        await skip_unless_schema_at_head(connection, safe_url)
        try:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                yield session
            await transaction.rollback()
        finally:
            await connection.close()
    finally:
        await engine.dispose()


async def _seed_ac(session: AsyncSession) -> tuple[str, str]:
    """Insert a language, user, problem, and one Accepted submission.

    Everything is created inside the fixture's transaction and rolled back, so
    the real foreign keys are enforced without leaving rows behind.

    Returns:
        Tuple of (user id, submission id).
    """
    user_id = str(uuid.uuid4())
    await session.execute(
        arena_users.insert().values(
            id=user_id,
            nome="Anchor User",
            email_normalizado=f"{user_id}@test.example",
            password_hash="x",
        )
    )
    language_id = f"badge-anchor-{uuid.uuid4().hex[:8]}"
    await session.execute(
        languages.insert().values(
            id=language_id,
            name="Python 3.14",
            icon="python",
            compile_image="noca/judge-python3:compile",
            run_image="noca/judge-python3:run",
            compile_cmd=["python3", "-m", "py_compile", "/sandbox/source.py"],
            run_cmd=["python3", "-u", "/sandbox/source.py"],
            source_filename="source.py",
            artifact_path="/sandbox/source.py",
            artifact_is_source=True,
            compile_timeout_s=10.0,
        )
    )
    problem_id = str(uuid.uuid4())
    await session.execute(
        arena_problems.insert().values(
            id=problem_id,
            arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
            title=f"Anchor Problem {uuid.uuid4().hex[:8]}",
            owner_id=user_id,
            problem_statement="<p>Echo.</p>",
            validator_type=ProblemValidatorType.STANDARD,
        )
    )
    submission_id = str(uuid.uuid4())
    await session.execute(
        arena_submissions.insert().values(
            id=submission_id,
            user_id=user_id,
            problem_id=problem_id,
            language_id=language_id,
            source_code="int main(){}",
            source_hash=submission_id,
            source_size_bytes=12,
            created_at=_WHEN,
        )
    )
    await session.execute(
        arena_submission_judgments.insert().values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=JudgmentStatus.DONE.value,
            autojudge_verdict=Verdict.AC.value,
            final_verdict=Verdict.AC.value,
            finished_at=_WHEN,
            created_at=_WHEN,
        )
    )
    return user_id, submission_id


@pytest.mark.asyncio
async def test_deleting_the_awarding_submission_clears_the_anchor_but_keeps_the_badge(
    postgres_badge_session: AsyncSession,
) -> None:
    """``ON DELETE SET NULL`` must never take the badge away with the submission."""
    session = postgres_badge_session
    user_id, submission_id = await _seed_ac(session)
    await award_badge(session, user_id, ArenaBadge.HELLO_WORLD, submission_id)
    await session.flush()

    await session.execute(
        delete(arena_submission_judgments).where(arena_submission_judgments.c.submission_id == submission_id)
    )
    await session.execute(delete(arena_submissions).where(arena_submissions.c.id == submission_id))
    await session.flush()

    row = (
        await session.execute(
            select(arena_user_badges.c.badge, arena_user_badges.c.submission_id).where(
                arena_user_badges.c.user_id == user_id
            )
        )
    ).one()
    assert row.badge == ArenaBadge.HELLO_WORLD.value
    assert row.submission_id is None
