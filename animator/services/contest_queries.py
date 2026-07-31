#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""SQLAlchemy Core loaders for the animator contest feed.

This module owns query construction and the deterministic judgment resolution.
Orchestration and response mapping live in
:mod:`animator.services.contest_feed_service`. Every loader reads the shared
schema through Core only and never imports ``web``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.query_records import (
    ContestRecord,
    JudgmentRecord,
    ProblemRecord,
    SiteRecord,
    SubmissionRecord,
    TeamRecord,
)
from shared.db_schema import (
    contests,
    problems,
    sites,
    submission_judgments,
    submissions,
    users,
)
from shared.enumerations import JudgmentStatus, RoleEnum, Verdict


async def load_enabled_contest(session: AsyncSession, slug: str) -> ContestRecord | None:
    """Load a contest by slug, gated on ``animator_enabled``.

    The gate covers existence and the animator flag only. It deliberately does
    not filter on ``active``: the Phase 05 contract gates on existence and
    ``animator_enabled`` alone.

    Args:
        session: Active database session.
        slug: Public contest slug.

    Returns:
        The contest record when it exists and is animator-enabled, else ``None``.
    """
    row = (
        await session.execute(
            select(
                contests.c.id,
                contests.c.login_slug,
                contests.c.contest_name,
                contests.c.animator_enabled,
                contests.c.start_time,
                contests.c.duration_minutes,
                contests.c.stop_updating_scoreboard,
                contests.c.release_scoreboard_after_end,
                contests.c.wa_penalty,
                contests.c.accept_pe,
                contests.c.ce_adds_penalty,
            ).where(
                contests.c.login_slug == slug,
                contests.c.animator_enabled.is_(True),
            )
        )
    ).first()
    if row is None:
        return None
    return ContestRecord(
        id=str(row.id),
        login_slug=str(row.login_slug),
        contest_name=str(row.contest_name),
        animator_enabled=bool(row.animator_enabled),
        start_time=row.start_time,
        duration_minutes=int(row.duration_minutes),
        stop_updating_scoreboard=int(row.stop_updating_scoreboard),
        release_scoreboard_after_end=bool(row.release_scoreboard_after_end),
        wa_penalty=int(row.wa_penalty),
        accept_pe=bool(row.accept_pe),
        ce_adds_penalty=bool(row.ce_adds_penalty),
    )


async def load_teams(session: AsyncSession, contest_id: str) -> list[TeamRecord]:
    """Load the contest's ``RoleEnum.TEAM`` users in a deterministic order."""
    result = await session.execute(
        select(
            users.c.id,
            users.c.username,
            users.c.fullname,
            users.c.site_id,
            sites.c.sitename.label("site_name"),
        )
        .outerjoin(sites, users.c.site_id == sites.c.id)
        .where(users.c.contest_id == contest_id, users.c.role == RoleEnum.TEAM)
        .order_by(users.c.username, users.c.id)
    )
    return [
        TeamRecord(
            id=str(row.id),
            username=str(row.username),
            fullname=str(row.fullname),
            site_id=str(row.site_id) if row.site_id is not None else None,
            site_name=str(row.site_name) if row.site_name is not None else None,
        )
        for row in result
    ]


async def load_problems(session: AsyncSession, contest_id: str) -> list[ProblemRecord]:
    """Load the contest's problems ordered by ordinal."""
    result = await session.execute(
        select(problems.c.id, problems.c.ordinal, problems.c.color, problems.c.title)
        .where(problems.c.contest_id == contest_id)
        .order_by(problems.c.ordinal, problems.c.id)
    )
    return [
        ProblemRecord(
            id=str(row.id),
            ordinal=int(row.ordinal),
            color=str(row.color),
            title=str(row.title),
        )
        for row in result
    ]


async def load_sites(session: AsyncSession, contest_id: str) -> list[SiteRecord]:
    """Load the contest's sites with medal cutoffs and team counts."""
    team_counts_stmt = (
        select(users.c.site_id, func.count().label("team_count"))
        .where(
            users.c.contest_id == contest_id,
            users.c.role == RoleEnum.TEAM,
            users.c.site_id.is_not(None),
        )
        .group_by(users.c.site_id)
    )
    team_counts = {str(row.site_id): int(row.team_count) for row in await session.execute(team_counts_stmt)}

    result = await session.execute(
        select(
            sites.c.id,
            sites.c.sitename,
            sites.c.gold_cutoff,
            sites.c.silver_cutoff,
            sites.c.bronze_cutoff,
        )
        .where(sites.c.contest_id == contest_id)
        .order_by(sites.c.sitename, sites.c.id)
    )
    return [
        SiteRecord(
            id=str(row.id),
            sitename=str(row.sitename),
            gold_cutoff=int(row.gold_cutoff),
            silver_cutoff=int(row.silver_cutoff),
            bronze_cutoff=int(row.bronze_cutoff),
            team_count=team_counts.get(str(row.id), 0),
        )
        for row in result
    ]


# Candidate judgment rows for one submission, in query order:
# (judgment_id, status, created_at, final_verdict).
_JudgmentCandidate = tuple[str, JudgmentStatus, datetime | None, Verdict | None]


async def load_submission_rows(
    session: AsyncSession,
    contest_id: str,
) -> tuple[list[SubmissionRecord], dict[str, JudgmentRecord | None]]:
    """Load team submissions and their effective judgments in one joined query.

    The submitting user is joined and constrained to this contest and
    ``RoleEnum.TEAM``. Without that constraint an earlier accepted submission by
    a judge, admin, or cross-contest user could consume the first-balloon marker
    in ``compute_icpc`` before the legitimate first team solve is seen.
    """
    rows = (
        await session.execute(
            select(
                submissions.c.id,
                submissions.c.team_id,
                submissions.c.problem_id,
                submissions.c.timestamp_seconds,
                submissions.c.created_at,
                submission_judgments.c.id.label("judgment_id"),
                submission_judgments.c.status.label("judgment_status"),
                submission_judgments.c.final_verdict.label("judgment_final_verdict"),
                submission_judgments.c.created_at.label("judgment_created_at"),
            )
            .join(problems, submissions.c.problem_id == problems.c.id)
            .join(users, submissions.c.team_id == users.c.id)
            .outerjoin(
                submission_judgments,
                and_(
                    submission_judgments.c.submission_id == submissions.c.id,
                    submission_judgments.c.status != JudgmentStatus.SUPERSEDED,
                ),
            )
            .where(
                problems.c.contest_id == contest_id,
                users.c.contest_id == contest_id,
                users.c.role == RoleEnum.TEAM,
            )
            .order_by(
                submissions.c.team_id,
                submissions.c.problem_id,
                submissions.c.timestamp_seconds,
                submissions.c.id,
            )
        )
    ).all()

    submission_order: list[SubmissionRecord] = []
    seen: set[str] = set()
    candidates: dict[str, list[_JudgmentCandidate]] = defaultdict(list)

    for row in rows:
        submission_id = str(row.id)
        if submission_id not in seen:
            seen.add(submission_id)
            submission_order.append(
                SubmissionRecord(
                    id=submission_id,
                    team_id=str(row.team_id),
                    problem_id=str(row.problem_id),
                    timestamp_seconds=int(row.timestamp_seconds),
                    created_at=row.created_at,
                )
            )
        if row.judgment_id is not None:
            candidates[submission_id].append(
                (
                    str(row.judgment_id),
                    row.judgment_status,
                    row.judgment_created_at,
                    row.judgment_final_verdict,
                )
            )

    judgments: dict[str, JudgmentRecord | None] = {}
    for submission in submission_order:
        submission_candidates = candidates.get(submission.id)
        judgments[submission.id] = _select_effective_judgment(submission_candidates) if submission_candidates else None
    return submission_order, judgments


def _select_effective_judgment(candidates: list[_JudgmentCandidate]) -> JudgmentRecord:
    """Choose the effective judgment deterministically for one submission.

    Ordering mirrors the Web scoreboard's "prefer DONE" intent but is fully
    specified so repeated snapshots never differ: prefer ``DONE`` status, then
    the latest ``created_at``, then the highest judgment id.

    Args:
        candidates: Non-empty candidate rows for one submission.

    Returns:
        The effective judgment record.
    """
    best = max(candidates, key=_judgment_sort_key)
    return JudgmentRecord(final_verdict=best[3])


def _judgment_sort_key(item: _JudgmentCandidate) -> tuple[bool, datetime, str]:
    judgment_id, status, created_at, _verdict = item
    is_done = status == JudgmentStatus.DONE
    normalized_created = created_at if created_at is not None else datetime.min
    if normalized_created.tzinfo is None:
        normalized_created = normalized_created.replace(tzinfo=UTC)
    return (is_done, normalized_created, judgment_id)
