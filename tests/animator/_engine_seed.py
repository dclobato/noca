#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Focused ceremony fixtures for the reveal *state machine*.

``_reveal_seed`` builds the broad ceremony the loader and projection tests
assert exact ranks and pending sets against; extending it would invalidate those
assertions. The two situations Phase 10 must pin down — a reveal that lifts the
focused team over other eligible teams, and two tied teams that are *both*
eligible — therefore get their own contests here.

The module name is underscore-prefixed so pytest does not collect it as a test.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from animator.services.contest_queries import load_enabled_contest
from animator.services.reveal_loader import RevealDataset, load_reveal_dataset
from shared.enumerations import Verdict
from tests.animator._feed_seed import (
    add_submission,
    feed_session,
    make_contest,
    make_language,
    make_problem,
    make_user,
)
from web.models.users import UberAdmin, User

FREEZE_MINUTES = 240


class Fixture:
    """Identifiers of a seeded engine fixture."""

    slug: str
    teams: dict[str, str]
    problems: dict[str, str]
    runs: dict[str, str]

    def __init__(self, slug: str, teams: dict[str, str], problems: dict[str, str], runs: dict[str, str]) -> None:
        """Store the seeded identifiers."""
        self.slug = slug
        self.teams = teams
        self.problems = problems
        self.runs = runs


async def load_global(session: AsyncSession, slug: str) -> RevealDataset:
    """Load the global-scope dataset for ``slug`` on a fresh session."""
    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        contest = await load_enabled_contest(feed, slug)
        assert contest is not None
        return await load_reveal_dataset(feed, contest, site_id=None)


async def seed_leapfrog(session: AsyncSession, uberadmin: UberAdmin) -> Fixture:
    """Seed a ceremony where a reveal lifts the focused team over another.

    ``lx`` starts tied at the bottom with two relevant frozen cells. Revealing
    the first is an accepted run that lifts it above ``lb``, which is itself
    still eligible — so focus must move to ``lb`` rather than staying on ``lx``.
    The cursor comes back to ``lx`` once ``lb`` is exhausted.

    Standings order is unambiguous: teams load by ``(username, id)`` and the
    projection sorts stably on score alone.
    """
    contest = await make_contest(
        session,
        uberadmin,
        slug=f"leap-{uuid.uuid4().hex[:8]}",
        stop_updating_scoreboard=FREEZE_MINUTES,
    )
    language = await make_language(session)
    teams: dict[str, User] = {name: make_user(contest, uberadmin, name) for name in ("lb", "lm", "lx")}
    problems = {label: make_problem(contest, ordinal) for ordinal, label in enumerate("ABC", start=1)}
    session.add_all([*teams.values(), *problems.values()])
    await session.flush()

    # Pre-freeze: only ``lm`` has scored, so ``lb`` and ``lx`` tie at the bottom.
    await add_submission(
        session, problem=problems["A"], team=teams["lm"], language=language, minutes=100, verdict=Verdict.AC
    )
    # Frozen, all relevant: lx solves A and fails B, lm fails B, lb fails C.
    lx_solve = await add_submission(
        session, problem=problems["A"], team=teams["lx"], language=language, minutes=250, verdict=Verdict.AC
    )
    lx_fail = await add_submission(
        session, problem=problems["B"], team=teams["lx"], language=language, minutes=260, verdict=Verdict.WA
    )
    lm_fail = await add_submission(
        session, problem=problems["B"], team=teams["lm"], language=language, minutes=245, verdict=Verdict.WA
    )
    lb_fail = await add_submission(
        session, problem=problems["C"], team=teams["lb"], language=language, minutes=270, verdict=Verdict.WA
    )
    await session.commit()

    return Fixture(
        slug=contest.login_slug,
        teams={name: row.id for name, row in teams.items()},
        problems={label: row.id for label, row in problems.items()},
        runs={
            "lx_solve": lx_solve.id,
            "lx_fail": lx_fail.id,
            "lm_fail": lm_fail.id,
            "lb_fail": lb_fail.id,
        },
    )


async def seed_tied_eligible(session: AsyncSession, uberadmin: UberAdmin) -> Fixture:
    """Seed a ceremony where two tied teams both hold a relevant frozen run.

    ``ta`` and ``tb`` solve the same problem at the same contest minute, so they
    share rank 1, and each has an unrevealed frozen run on a still-unsolved
    problem. Focus must resolve by standings order, not by the rank number.
    """
    contest = await make_contest(
        session,
        uberadmin,
        slug=f"tied-{uuid.uuid4().hex[:8]}",
        stop_updating_scoreboard=FREEZE_MINUTES,
    )
    language = await make_language(session)
    teams: dict[str, User] = {name: make_user(contest, uberadmin, name) for name in ("ta", "tb")}
    problems = {label: make_problem(contest, ordinal) for ordinal, label in enumerate("AB", start=1)}
    session.add_all([*teams.values(), *problems.values()])
    await session.flush()

    for name in ("ta", "tb"):
        await add_submission(
            session, problem=problems["A"], team=teams[name], language=language, minutes=100, verdict=Verdict.AC
        )
    ta_run = await add_submission(
        session, problem=problems["B"], team=teams["ta"], language=language, minutes=250, verdict=Verdict.WA
    )
    tb_run = await add_submission(
        session, problem=problems["B"], team=teams["tb"], language=language, minutes=250, verdict=Verdict.WA
    )
    await session.commit()

    return Fixture(
        slug=contest.login_slug,
        teams={name: row.id for name, row in teams.items()},
        problems={label: row.id for label, row in problems.items()},
        runs={"ta_run": ta_run.id, "tb_run": tb_run.id},
    )
