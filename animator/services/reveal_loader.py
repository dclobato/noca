#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reveal-session data loading and deterministic frozen-universe construction.

This module owns no SQL of its own: it composes the existing Core loaders in
:mod:`animator.services.contest_queries`, then applies ceremony scope and builds
the immutable post-freeze universe for one session.

**Scope is applied to every projection input.**
:func:`shared.services.scoreboard_projection.compute_icpc` derives first solvers
by scanning *every* submission it is given, without cross-checking the ``teams``
argument. Filtering only the frozen ids would therefore let an out-of-site team's
earlier accepted run consume the first-solver marker in a site ceremony. The
dataset consequently narrows teams, submissions, **and** the judgment mapping
before anything is projected.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.query_records import (
    ContestRecord,
    JudgmentRecord,
    ProblemRecord,
    SiteRecord,
    SubmissionRecord,
    TeamRecord,
)
from animator.models.reveal_session import MedalCutoffs, RevealSessionState
from animator.services.contest_queries import (
    load_problems,
    load_sites,
    load_submission_rows,
    load_teams,
)
from shared.services.scoreboard_projection import submission_sort_key

__all__ = [
    "RevealDataset",
    "UnknownSiteError",
    "build_frozen_submission_ids",
    "initialize_reveal_session",
    "load_reveal_dataset",
    "submission_sort_key",
    "submissions_by_id",
]


class UnknownSiteError(LookupError):
    """Raised when a requested site does not belong to the contest."""

    def __init__(self, contest_id: str, site_id: str) -> None:
        """Store the offending identifiers and build a readable message."""
        self.contest_id = contest_id
        self.site_id = site_id
        super().__init__(f"site {site_id!r} does not belong to contest {contest_id!r}")


@dataclass(frozen=True)
class RevealDataset:
    """Scoped rows for one ceremony, ready for the shared ICPC projection.

    Attributes:
        contest: The animator-enabled contest being revealed.
        site: The site being revealed, or ``None`` for a global ceremony.
        teams: In-scope ``RoleEnum.TEAM`` users.
        problems: The contest's problems in display order.
        submissions: Submissions belonging to ``teams`` only.
        judgments: Effective judgments keyed by the ids in ``submissions``.
        site_names: Site display names keyed by site id, for team views.
    """

    contest: ContestRecord
    site: SiteRecord | None
    teams: list[TeamRecord]
    problems: list[ProblemRecord]
    submissions: list[SubmissionRecord]
    judgments: dict[str, JudgmentRecord | None]
    site_names: dict[str, str]


async def load_reveal_dataset(
    session: AsyncSession,
    contest: ContestRecord,
    site_id: str | None = None,
) -> RevealDataset:
    """Load and scope every projection input for one reveal ceremony.

    Args:
        session: Active database session.
        contest: The enabled contest to reveal.
        site_id: Site to restrict the ceremony to, or ``None`` for global scope.

    Returns:
        The scoped dataset.

    Raises:
        UnknownSiteError: If ``site_id`` is not a site of this contest.
    """
    teams = await load_teams(session, contest.id)
    problems = await load_problems(session, contest.id)
    site_records = await load_sites(session, contest.id)
    submissions, judgments = await load_submission_rows(session, contest.id)

    site: SiteRecord | None = None
    if site_id is not None:
        site = next((row for row in site_records if row.id == site_id), None)
        if site is None:
            raise UnknownSiteError(contest.id, site_id)
        teams = [team for team in teams if team.site_id == site_id]
        team_ids = {team.id for team in teams}
        submissions = [row for row in submissions if row.team_id in team_ids]
        judgments = {row.id: judgments.get(row.id) for row in submissions}

    return RevealDataset(
        contest=contest,
        site=site,
        teams=teams,
        problems=problems,
        submissions=submissions,
        judgments=judgments,
        site_names={row.id: row.sitename for row in site_records},
    )


def build_frozen_submission_ids(dataset: RevealDataset) -> tuple[str, ...]:
    """Build the immutable post-freeze universe for a ceremony.

    A submission is frozen exactly when ``timestamp_seconds > freeze_at_seconds``
    — the same predicate the scoreboard uses, on contest-relative seconds rather
    than the wall clock. Legacy rows carry the column's ``0`` server default and
    are therefore always pre-freeze. Membership depends only on that predicate:
    a post-freeze run on an already-solved problem stays in the universe, and
    whether it is *relevant* to a reveal step is a later decision.

    Args:
        dataset: The scoped ceremony dataset.

    Returns:
        Frozen submission ids ordered by ``(timestamp_seconds, created_at, id)``.
    """
    freeze_at_seconds = dataset.contest.freeze_at_seconds
    frozen = [row for row in dataset.submissions if int(row.timestamp_seconds) > freeze_at_seconds]
    frozen.sort(key=submission_sort_key)
    return tuple(row.id for row in frozen)


def initialize_reveal_session(dataset: RevealDataset) -> RevealSessionState:
    """Build the initial, idle state for a ceremony.

    Args:
        dataset: The scoped ceremony dataset.

    Returns:
        A state with an empty step trail, ``phase="idle"``, no focused team, and
        the site's medal cutoffs (``None`` for a global ceremony).
    """
    site = dataset.site
    cutoffs: MedalCutoffs | None = None
    if site is not None:
        cutoffs = MedalCutoffs(
            gold=site.gold_cutoff,
            silver=site.silver_cutoff,
            bronze=site.bronze_cutoff,
        )
    return RevealSessionState(
        contest_id=dataset.contest.id,
        site_id=site.id if site is not None else None,
        site_name=site.sitename if site is not None else None,
        phase="idle",
        medal_cutoffs=cutoffs,
        frozen_submission_ids=build_frozen_submission_ids(dataset),
        step_log=(),
        focused_team_id=None,
    )


def submissions_by_id(submissions: Sequence[SubmissionRecord]) -> dict[str, SubmissionRecord]:
    """Index submissions by identifier."""
    return {row.id: row for row in submissions}
