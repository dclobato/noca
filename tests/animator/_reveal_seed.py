#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared ceremony fixture for the reveal loader and projection tests.

Seed data is written through the Web ORM (helpers in ``_feed_seed``), committed,
then read back through the animator Core loader on a fresh session — proving the
reveal path never depends on the Web ORM at read time.

The module name is underscore-prefixed so pytest does not collect it as a test.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from animator.services.contest_queries import load_enabled_contest
from animator.services.reveal_loader import RevealDataset, load_reveal_dataset
from shared.db_schema import submissions as submissions_table
from shared.enumerations import JudgmentStatus, Verdict
from tests.animator._feed_seed import (
    START,
    add_judgment,
    add_submission,
    feed_session,
    make_contest,
    make_language,
    make_problem,
    make_site,
    make_user,
)
from web.models.language import Language
from web.models.problem import Problem
from web.models.users import UberAdmin, User

FREEZE_MINUTES = 240
WA_PENALTY = 20


async def add_raw_submission(
    session: AsyncSession,
    *,
    submission_id: str,
    problem: Problem,
    team: User,
    language: Language,
    timestamp_seconds: int,
    created_at: datetime,
    verdict: Verdict | None,
) -> str:
    """Insert a submission with a fully controlled id, contest time, and clock.

    ``_feed_seed`` derives ``created_at`` from the contest minute, which cannot
    express the ordering ties these tests need.
    """
    await session.execute(
        insert(submissions_table).values(
            id=submission_id,
            problem_id=problem.id,
            team_id=team.id,
            language_id=language.id,
            source_code="x",
            source_hash=uuid.uuid4().hex,
            source_size_bytes=1,
            timestamp_seconds=timestamp_seconds,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    await add_judgment(
        session,
        submission_id=submission_id,
        status=JudgmentStatus.DONE if verdict is not None else JudgmentStatus.QUEUED,
        verdict=verdict,
        created_at=created_at,
        timestamp_seconds=timestamp_seconds,
    )
    return submission_id


class Ceremony:
    """Identifiers of the seeded two-site ceremony fixture."""

    slug: str
    contest_id: str
    site_a: str
    site_b: str
    a1: str
    a2: str
    a3: str
    b1: str
    p1: str
    p2: str
    p3: str
    later_run: str
    frozen_ac: str
    frozen_pending: str
    frozen_ce: str
    frozen_pe: str
    legacy: str

    def __init__(self, **ids: str) -> None:
        """Store the seeded identifiers as attributes."""
        self.__dict__.update(ids)


async def seed_ceremony(session: AsyncSession, uberadmin: UberAdmin) -> Ceremony:
    """Seed the ceremony fixture the reveal tests share.

    Two sites, a tie, several pending frozen runs, a later run on an already
    solved problem, a legacy zero-timestamp row, and — because the contest sets
    ``accept_pe=True`` and ``ce_adds_penalty=True`` — a penalizing frozen ``CE``
    followed by a frozen ``PE`` that counts as the solve.

    Site A holds ``a1``/``a2``/``a3``; site B holds ``b1``. Problem P1 is solved
    by the cross-site team ``b1`` before ``a1``, so a site ceremony must not let
    ``b1`` consume the first-solver marker.
    """
    contest = await make_contest(
        session,
        uberadmin,
        slug=f"reveal-{uuid.uuid4().hex[:8]}",
        stop_updating_scoreboard=FREEZE_MINUTES,
        wa_penalty=WA_PENALTY,
        accept_pe=True,
        ce_adds_penalty=True,
    )
    language = await make_language(session)
    site_a = await make_site(session, contest, sitename="Campus A", gold=1, silver=2, bronze=3)
    site_b = await make_site(session, contest, sitename="Campus B", gold=1, silver=1, bronze=1)

    a1 = make_user(contest, uberadmin, "a1", site_id=site_a.id)
    a2 = make_user(contest, uberadmin, "a2", site_id=site_a.id)
    a3 = make_user(contest, uberadmin, "a3", site_id=site_a.id)
    b1 = make_user(contest, uberadmin, "b1", site_id=site_b.id)
    p1 = make_problem(contest, 1)
    p2 = make_problem(contest, 2)
    p3 = make_problem(contest, 3)
    session.add_all([a1, a2, a3, b1, p1, p2, p3])
    await session.flush()

    # Cross-site first solve on P1, earlier than any site-A solve.
    await add_submission(session, problem=p1, team=b1, language=language, minutes=10, verdict=Verdict.AC)
    # a1: one failed attempt, a pre-freeze solve, then an irrelevant later run.
    await add_submission(session, problem=p1, team=a1, language=language, minutes=20, verdict=Verdict.WA)
    await add_submission(session, problem=p1, team=a1, language=language, minutes=50, verdict=Verdict.AC)
    later_run = await add_submission(session, problem=p1, team=a1, language=language, minutes=260, verdict=Verdict.WA)
    # a2 ties a1 on solved+time via P2, and has an unrevealed frozen solve on P3.
    await add_submission(session, problem=p2, team=a2, language=language, minutes=20, verdict=Verdict.WA)
    await add_submission(session, problem=p2, team=a2, language=language, minutes=50, verdict=Verdict.AC)
    frozen_ac = await add_submission(session, problem=p3, team=a2, language=language, minutes=250, verdict=Verdict.AC)
    # a3: a still-unjudged frozen run, plus a frozen CE that penalizes the
    # frozen PE solve that follows it (accept_pe / ce_adds_penalty are both on).
    frozen_pending = await add_submission(
        session,
        problem=p2,
        team=a3,
        language=language,
        minutes=270,
        verdict=None,
        status=JudgmentStatus.QUEUED,
    )
    frozen_ce = await add_submission(session, problem=p1, team=a3, language=language, minutes=245, verdict=Verdict.CE)
    frozen_pe = await add_submission(session, problem=p1, team=a3, language=language, minutes=255, verdict=Verdict.PE)
    # A legacy row with the column's ``0`` server default: always pre-freeze.
    legacy = await add_raw_submission(
        session,
        submission_id=f"legacy-{uuid.uuid4().hex[:8]}",
        problem=p3,
        team=a3,
        language=language,
        timestamp_seconds=0,
        created_at=START,
        verdict=Verdict.WA,
    )
    await session.commit()

    return Ceremony(
        slug=contest.login_slug,
        contest_id=contest.id,
        site_a=site_a.id,
        site_b=site_b.id,
        a1=a1.id,
        a2=a2.id,
        a3=a3.id,
        b1=b1.id,
        p1=p1.id,
        p2=p2.id,
        p3=p3.id,
        later_run=later_run.id,
        frozen_ac=frozen_ac.id,
        frozen_pending=frozen_pending.id,
        frozen_ce=frozen_ce.id,
        frozen_pe=frozen_pe.id,
        legacy=legacy,
    )


async def load(session: AsyncSession, slug: str, site_id: str | None) -> RevealDataset:
    """Resolve the contest and load a scoped dataset on a fresh session."""
    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        contest = await load_enabled_contest(feed, slug)
        assert contest is not None
        return await load_reveal_dataset(feed, contest, site_id=site_id)
