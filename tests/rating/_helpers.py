#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for the Arena rating worker test suite."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
import web.models.language  # noqa: F401
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_submissions import ArenaSubmission
from arena.models.arena_users import ArenaUser
from shared.db_schema.arena import arena_problem_ratings, arena_problem_solvers
from shared.enumerations import ArenaRole
from web.models.language import Language


async def _make_user(session: AsyncSession) -> ArenaUser:
    user = ArenaUser(
        nome="Test User",
        email_normalizado=f"test-{uuid.uuid4().hex[:8]}@example.com",
        dta_nascimento=date(1998, 1, 1),
        role=ArenaRole.ARENA_USER,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_judge(session: AsyncSession) -> ArenaUser:
    """Create an ARENA_JUDGE user."""
    user = ArenaUser(
        nome="Test Judge",
        email_normalizado=f"judge-{uuid.uuid4().hex[:8]}@example.com",
        dta_nascimento=date(1998, 1, 1),
        role=ArenaRole.ARENA_JUDGE,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_admin(session: AsyncSession) -> ArenaUser:
    """Create an ARENA_ADMIN user."""
    user = ArenaUser(
        nome="Test Admin",
        email_normalizado=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        dta_nascimento=date(1998, 1, 1),
        role=ArenaRole.ARENA_ADMIN,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_language(session: AsyncSession) -> Language:
    """Create a minimal Language row for use in submission helpers."""
    language = Language(
        id=f"test-{uuid.uuid4().hex[:8]}",
        name="Test Language",
        icon="devicon-python-plain",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.txt",
        artifact_path="/sandbox/main.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_submission(
    session: AsyncSession,
    user_id: str,
    problem_id: str,
    language_id: str,
    created_at: datetime | None = None,
) -> ArenaSubmission:
    """Insert a bare-minimum arena_submissions row."""
    now = created_at or datetime.now(UTC)
    submission = ArenaSubmission(
        user_id=user_id,
        problem_id=problem_id,
        language_id=language_id,
        source_code="x",
        source_hash=uuid.uuid4().hex,
        source_size_bytes=1,
        submit_to_ai=False,
        created_at=now,
        updated_at=now,
    )
    session.add(submission)
    await session.flush()
    return submission


async def _make_problem(session: AsyncSession, user: ArenaUser) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
        title=f"Problem {uuid.uuid4().hex[:8]}",
        owner_id=user.id,
        problem_statement="<p>Test.</p>",
    )
    session.add(problem)
    await session.flush()
    return problem


async def _seed_rating_row(
    session: AsyncSession,
    problem_id: str,
    *,
    attempted: int,
    solved: int,
    total_tries: int,
) -> None:
    """Insert or update an arena_problem_ratings row with specific stats."""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    await session.execute(
        sqlite_insert(arena_problem_ratings)
        .values(
            problem_id=problem_id,
            attempted_users=attempted,
            solved_users=solved,
            total_submissions=attempted + total_tries,
            total_tries_before_solve=total_tries,
        )
        .on_conflict_do_update(
            index_elements=["problem_id"],
            set_={
                "attempted_users": attempted,
                "solved_users": solved,
                "total_tries_before_solve": total_tries,
            },
        )
    )
    await session.flush()


async def _record_solve(
    session: AsyncSession, user_id: str, problem_id: str, solved_at: datetime | None = None
) -> None:
    """Insert a row in arena_problem_solvers to mark a solved problem."""
    if solved_at is None:
        solved_at = datetime.now(UTC)
    await session.execute(
        arena_problem_solvers.insert().values(
            user_id=user_id,
            problem_id=problem_id,
            solved_at=solved_at,
        )
    )
    existing = await session.scalar(
        select(arena_problem_ratings.c.problem_id).where(arena_problem_ratings.c.problem_id == problem_id)
    )
    if existing is None:
        await session.execute(
            arena_problem_ratings.insert().values(
                problem_id=problem_id,
                rating=50,
            )
        )
    await session.flush()
