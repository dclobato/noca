#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The two backfills of ``202608250001``, run verbatim.

The migration's DDL is exercised by ``alembic upgrade``/``downgrade``; what these tests
cover is the part a schema comparison cannot see -- whether the backfill statements
classify and mark the right rows. They execute the exact SQL text the migration runs,
imported from it, so a statement cannot drift away from its coverage. That is also why
those statements are written in portable SQL: a PostgreSQL-only phrasing would have been
untestable in this suite, and an approximation of it in a test proves nothing.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import clarification_reads
from shared.enumerations import RoleEnum
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.users import UberAdmin, User

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "202608250001_add_clarification_announcement_reads.py"
)


def _load_migration() -> ModuleType:
    """Import the migration module by path (its name starts with a digit)."""
    spec = importlib.util.spec_from_file_location("announcement_migration_under_test", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_user(contest: Contest, uberadmin: UberAdmin, username: str, role: RoleEnum) -> User:
    user = User(
        username=username,
        fullname=username.replace("_", " ").title(),
        role=role,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    return user


async def _seed_pre_migration_rows(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
) -> dict[str, str]:
    """Create rows in the shape the migration finds, with the flag not yet set."""
    judge = _make_user(contest, uberadmin, "mig_judge", RoleEnum.JUDGE)
    team_a = _make_user(contest, uberadmin, "mig_team_a", RoleEnum.TEAM)
    team_b = _make_user(contest, uberadmin, "mig_team_b", RoleEnum.TEAM)
    staff = _make_user(contest, uberadmin, "mig_staff", RoleEnum.STAFF)
    session.add_all([judge, team_a, team_b, staff])
    await session.flush()

    now = datetime.now(UTC)
    announcement = Clarification(
        team_id=judge.id,
        judge_id=judge.id,
        question="Announcement",
        answer="Problem A was restarted.",
        is_contest_public=True,
        answered_at=now,
        answered_timestamp_seconds=12,
        created_at=now,
        created_timestamp_seconds=12,
    )
    question = Clarification(
        team_id=team_a.id,
        question="Is the input sorted?",
        created_at=now,
        created_timestamp_seconds=5,
    )
    session.add_all([announcement, question])
    await session.flush()

    # The pre-migration shape: the column exists (the suite builds the schema from current
    # metadata) but no row has been classified yet.
    await session.execute(update(Clarification).values(is_announcement=False))
    await session.execute(delete(clarification_reads))
    await session.flush()

    return {
        "announcement": announcement.id,
        "question": question.id,
        "judge": judge.id,
        "team_a": team_a.id,
        "team_b": team_b.id,
        "staff": staff.id,
    }


@pytest.mark.asyncio
async def test_the_flag_backfill_classifies_by_the_author_role(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """A non-TEAM author marks the row an announcement; a team's question stays one."""
    ids = await _seed_pre_migration_rows(session, running_contest, uberadmin)

    await session.execute(text(_load_migration().BACKFILL_ANNOUNCEMENT_FLAG))

    result = await session.execute(
        select(Clarification.id, Clarification.is_announcement).where(
            Clarification.id.in_([ids["announcement"], ids["question"]])
        )
    )
    assert dict(result.all()) == {ids["announcement"]: True, ids["question"]: False}


@pytest.mark.asyncio
async def test_the_read_backfill_covers_every_existing_team_and_only_announcements(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """The installed base is caught up, so upgrading notifies nobody retroactively."""
    ids = await _seed_pre_migration_rows(session, running_contest, uberadmin)
    migration = _load_migration()

    await session.execute(text(migration.BACKFILL_ANNOUNCEMENT_FLAG))
    await session.execute(text(migration.BACKFILL_READ_MARKERS))

    result = await session.execute(select(clarification_reads.c.clarification_id, clarification_reads.c.user_id))
    rows = set(result.all())

    # Both teams are caught up on the announcement...
    assert (ids["announcement"], ids["team_a"]) in rows
    assert (ids["announcement"], ids["team_b"]) in rows
    # ...and nothing else is marked: not the ordinary question, and not the non-team
    # accounts, which have no team notifications to suppress.
    assert rows == {(ids["announcement"], ids["team_a"]), (ids["announcement"], ids["team_b"])}
