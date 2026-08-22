#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Seed the finished IX InterIF 2026 local contest for animator testing.

Usage:
    uv run python scripts/web/seed_interif_2026.py
    uv run python scripts/web/seed_interif_2026.py --uberadmin my-uberadmin
    uv run python scripts/web/seed_interif_2026.py --remove

The command requires an existing UberAdmin to own the seeded records. It
creates contest administrator ``admin`` and judge ``judgeif``, both with
password ``StrongPasswd1!``, and refuses to overwrite an existing contest.
The remove mode deletes the contest identified by this seed's fixed slug and its
dedicated fixture language.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from scripts.web.interif_2026_history import (
    seed_clarifications,
    seed_completed_balloon_tasks,
)
from shared.app_logging import configure_logging
from shared.db_schema import contest_languages as contest_languages_table
from shared.enumerations import RoleEnum
from tests.fixtures.interif_2026 import (
    InterIF2026ContestFixture,
    load_interif_2026_contest,
)
from web.config import settings
from web.database import create_engine, create_session_factory
from web.models.clarification import Clarification
from web.models.contest import Contest, Task
from web.models.language import Language
from web.models.problem import Problem
from web.models.site import Site
from web.models.submission import Submission
from web.models.users import UberAdmin, User
from web.services.problem_service import delete_problem_statement, save_md_statement

logger = logging.getLogger(__name__)

CONTEST_SLUG = "ix-interif-2026-fase-local"
FIXTURE_LANGUAGE_ID = "interif-2026-fixture"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "StrongPasswd1!"
JUDGE_USERNAME = "judgeif"
JUDGE_PASSWORD = "StrongPasswd1!"
PROBLEM_STATEMENT_STUB = """# {title}

This is a placeholder statement for the IX InterIF 2026 historical contest fixture.
"""


class InterIF2026SeedError(RuntimeError):
    """Raised when the real-contest seed cannot be created safely."""


@dataclass(frozen=True, slots=True)
class SeedResult:
    """Created contest entities and administrator credentials."""

    fixture: InterIF2026ContestFixture
    admin: User
    judge: User
    clarification_count: int
    balloon_task_count: int


def write_problem_statement_stubs(problems: tuple[Problem, ...], statement_dir: Path) -> None:
    """Write a minimal Markdown statement for every seeded problem.

    Args:
        problems: Seeded contest problems that need backup-compatible statements.
        statement_dir: Configured problem statement storage directory.
    """
    for problem in problems:
        save_md_statement(
            problem.id,
            PROBLEM_STATEMENT_STUB.format(title=problem.title),
            statement_dir,
        )


async def seed_interif_2026(
    session: AsyncSession,
    uberadmin: UberAdmin,
    *,
    statement_dir: Path = settings.PROBLEM_STATEMENT_DIR,
) -> SeedResult:
    """Create the finished contest and its contest administrator.

    Args:
        session: Database session used for the atomic seed transaction.
        uberadmin: Existing UberAdmin that owns the generated records.
        statement_dir: Directory where problem statement stubs are stored.

    Returns:
        The complete contest fixture and created administrator.

    Raises:
        InterIF2026SeedError: If the contest already exists.
    """
    existing = await session.scalar(select(Contest).where(Contest.login_slug == CONTEST_SLUG))
    if existing is not None:
        raise InterIF2026SeedError(f"Contest slug {CONTEST_SLUG!r} already exists; no data was changed.")

    fixture = await load_interif_2026_contest(session, uberadmin)
    contest = fixture.contest
    contest.active = True
    contest.release_scoreboard_after_end = True
    contest.release_problem_set_after_end = True
    contest.stop_updating_scoreboard = 140
    contest.stop_answers_after = 160

    # Phase 01 adds this field. Keeping the script compatible before and after
    # that migration lets the verified contest data land ahead of the module.
    if "animator_enabled" in Contest.__table__.columns:
        cast(Any, contest).animator_enabled = True

    admin = User(
        username=ADMIN_USERNAME,
        fullname="InterIF Animator Administrator",
        role=RoleEnum.ADMIN,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    admin.password = ADMIN_PASSWORD
    judge = User(
        username=JUDGE_USERNAME,
        fullname="InterIF Clarification Judge",
        role=RoleEnum.JUDGE,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    judge.password = JUDGE_PASSWORD
    session.add_all([admin, judge])
    await session.flush()

    contest.owner_user_id = admin.id
    contest.chief_judge_id = judge.id
    await session.flush()

    clarifications = await seed_clarifications(session, fixture, judge)
    balloon_tasks = await seed_completed_balloon_tasks(session, fixture, admin)
    write_problem_statement_stubs(fixture.problems, statement_dir)
    return SeedResult(
        fixture=fixture,
        admin=admin,
        judge=judge,
        clarification_count=len(clarifications),
        balloon_task_count=len(balloon_tasks),
    )


async def run_seed(uberadmin_username: str) -> SeedResult:
    """Run the seed against the configured Web database and commit it."""
    engine = create_engine(poolclass=NullPool)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session, session.begin():
            uberadmin = await session.scalar(
                select(UberAdmin).where(
                    UberAdmin.username == uberadmin_username,
                    UberAdmin.is_enabled.is_(True),
                )
            )
            if uberadmin is None:
                raise InterIF2026SeedError(f"Enabled UberAdmin {uberadmin_username!r} was not found.")
            return await seed_interif_2026(session, uberadmin)
    finally:
        await engine.dispose()


async def remove_interif_2026(
    session: AsyncSession,
    *,
    statement_dir: Path = settings.PROBLEM_STATEMENT_DIR,
) -> bool:
    """Remove the contest created by this seed and all of its data.

    Args:
        session: Database session used for the atomic removal transaction.
        statement_dir: Directory containing the seeded problem statements.

    Returns:
        True when the seeded contest existed and was removed, otherwise False.
    """
    contest_id = await session.scalar(select(Contest.id).where(Contest.login_slug == CONTEST_SLUG))
    if contest_id is None:
        return False

    team_ids = select(User.id).where(User.contest_id == contest_id)
    problem_ids = tuple(await session.scalars(select(Problem.id).where(Problem.contest_id == contest_id)))

    await session.execute(delete(Task).where(Task.team_id.in_(team_ids)))
    await session.execute(delete(Clarification).where(Clarification.team_id.in_(team_ids)))
    await session.execute(delete(Submission).where(Submission.problem_id.in_(problem_ids)))
    await session.execute(delete(contest_languages_table).where(contest_languages_table.c.contest_id == contest_id))
    await session.execute(delete(Language).where(Language.id == FIXTURE_LANGUAGE_ID))
    await session.execute(
        update(Contest).where(Contest.id == contest_id).values(owner_user_id=None, chief_judge_id=None)
    )
    await session.execute(delete(User).where(User.contest_id == contest_id))
    await session.execute(delete(Problem).where(Problem.id.in_(problem_ids)))
    await session.execute(delete(Site).where(Site.contest_id == contest_id))
    await session.execute(delete(Contest).where(Contest.id == contest_id))
    for problem_id in problem_ids:
        delete_problem_statement(problem_id, statement_dir)
    return True


async def run_remove() -> bool:
    """Remove the seeded contest from the configured Web database."""
    engine = create_engine(poolclass=NullPool)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session, session.begin():
            return await remove_interif_2026(session)
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Seed the finished IX InterIF 2026 contest for animator E2E testing.")
    parser.add_argument(
        "--uberadmin",
        default=settings.UBERADMIN_USERNAME or "uberadmin",
        help="Existing enabled UberAdmin username used as record creator.",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help=f"Remove the previously seeded contest with slug {CONTEST_SLUG!r}.",
    )
    return parser.parse_args()


async def _main(uberadmin_username: str, *, remove: bool = False) -> int:
    """Seed or remove the contest and print the operation result."""
    if remove:
        removed = await run_remove()
        if removed:
            print(f"IX InterIF 2026 animator contest removed: {CONTEST_SLUG}")
        else:
            print(f"IX InterIF 2026 animator contest was not found: {CONTEST_SLUG}")
        return 0

    try:
        result = await run_seed(uberadmin_username)
    except InterIF2026SeedError as exc:
        logger.error("%s", exc)
        return 1

    fixture = result.fixture
    print("IX InterIF 2026 animator contest created successfully.")
    print(f"Contest slug: {fixture.contest.login_slug}")
    print(f"Contest admin: {ADMIN_USERNAME}")
    print(f"Contest password: {ADMIN_PASSWORD}")
    print(f"Contest judge: {JUDGE_USERNAME}")
    print(f"Judge password: {JUDGE_PASSWORD}")
    print(f"Sites: {len(fixture.sites)}")
    print(f"Teams: {len(fixture.teams)}")
    print(f"Problems: {len(fixture.problems)}")
    print(f"Submissions: {len(fixture.submissions)}")
    print(f"Clarifications: {result.clarification_count}")
    print(f"Completed balloon tasks: {result.balloon_task_count}")
    return 0


if __name__ == "__main__":
    configure_logging()
    arguments = parse_args()
    raise SystemExit(asyncio.run(_main(arguments.uberadmin, remove=arguments.remove)))
