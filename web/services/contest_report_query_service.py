#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Lean, report-only submission query for the contest reports page.

`contest_report_service` aggregates purely in memory and is documented as
doing no I/O of its own; this module is where that I/O lives. It exists
because `submission_service.list_submissions` -- built for the Runs page --
loads full `Submission` ORM rows (including `source_code`, judgment
confirmations, and verdict overrides) that the report never reads. At a few
thousand submissions that waste is negligible, but a busy contest can clear
ten thousand-plus, and pulling every source file into memory just to discard
it is real avoidable I/O and RAM. This module selects only the columns the
report actually aggregates.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import problems as problems_table
from shared.db_schema import sites as sites_table
from shared.db_schema import submission_judgments as submission_judgments_table
from shared.db_schema import submissions as submissions_table
from shared.db_schema import users as users_table
from shared.enumerations import JudgmentStatus, Verdict
from web.models.contest import Contest


@dataclass(slots=True)
class ContestReportSubmissionRow:
    """One submission's report-relevant columns, with its judgment already resolved.

    `judgment_status`/`final_verdict` mirror `judgment_utils.get_active_judgment`'s
    selection exactly: the latest (by `created_at`) judgment that is neither
    `SUPERSEDED` nor `FAILED`, or `None` of each when no such judgment exists.
    Structurally compatible with `shared.services.scoreboard_projection`'s
    `SubmissionInput` protocol (`id`, `team_id`, `problem_id`,
    `timestamp_seconds`, `created_at`), so `submission_sort_key` accepts it
    directly.
    """

    id: str
    problem_id: str
    problem_ordinal: int
    problem_title: str
    problem_color: str
    team_id: str
    team_username: str
    team_fullname: str
    team_site_name: str | None
    language_id: str
    timestamp_seconds: int
    created_at: datetime
    judgment_status: JudgmentStatus | None
    final_verdict: Verdict | None


async def list_contest_report_submissions(
    session: AsyncSession,
    contest: Contest,
    *,
    site_id: str | None = None,
) -> list[ContestReportSubmissionRow]:
    """Load every submission of a contest, flattened for report aggregation.

    One SQL round trip: submissions joined to their problem and team (with the
    team's site), outer-joined to every non-superseded, non-failed judgment so
    the effective one can be picked per submission in Python -- the same
    reduction `contest_queries.load_submission_rows` uses in the animator,
    adapted to `get_active_judgment`'s exact exclusion set.

    `site_id` scopes the query to one site's teams (a team with no site is
    excluded); `None` (the default) returns every submission in the contest.
    """
    stmt = (
        select(
            submissions_table.c.id,
            submissions_table.c.problem_id,
            problems_table.c.ordinal.label("problem_ordinal"),
            problems_table.c.title.label("problem_title"),
            problems_table.c.color.label("problem_color"),
            submissions_table.c.team_id,
            users_table.c.username.label("team_username"),
            users_table.c.fullname.label("team_fullname"),
            sites_table.c.sitename.label("team_site_name"),
            submissions_table.c.language_id,
            submissions_table.c.timestamp_seconds,
            submissions_table.c.created_at,
            submission_judgments_table.c.status.label("judgment_status"),
            submission_judgments_table.c.final_verdict,
            submission_judgments_table.c.created_at.label("judgment_created_at"),
        )
        .join(problems_table, submissions_table.c.problem_id == problems_table.c.id)
        .join(users_table, submissions_table.c.team_id == users_table.c.id)
        .outerjoin(sites_table, users_table.c.site_id == sites_table.c.id)
        .outerjoin(
            submission_judgments_table,
            and_(
                submission_judgments_table.c.submission_id == submissions_table.c.id,
                submission_judgments_table.c.status.notin_([JudgmentStatus.SUPERSEDED, JudgmentStatus.FAILED]),
            ),
        )
        .where(problems_table.c.contest_id == contest.id)
    )
    if site_id is not None:
        stmt = stmt.where(users_table.c.site_id == site_id)

    rows = (await session.execute(stmt)).all()

    submission_order: list[str] = []
    rows_by_submission: dict[str, ContestReportSubmissionRow] = {}
    candidates_by_submission: defaultdict[str, list[tuple[datetime, JudgmentStatus, Verdict | None]]] = defaultdict(
        list
    )
    for row in rows:
        submission_id = str(row.id)
        if submission_id not in rows_by_submission:
            submission_order.append(submission_id)
            rows_by_submission[submission_id] = ContestReportSubmissionRow(
                id=submission_id,
                problem_id=str(row.problem_id),
                problem_ordinal=int(row.problem_ordinal),
                problem_title=str(row.problem_title),
                problem_color=str(row.problem_color),
                team_id=str(row.team_id),
                team_username=str(row.team_username),
                team_fullname=str(row.team_fullname),
                team_site_name=(str(row.team_site_name) if row.team_site_name is not None else None),
                language_id=str(row.language_id),
                timestamp_seconds=int(row.timestamp_seconds),
                created_at=row.created_at,
                judgment_status=None,
                final_verdict=None,
            )
        if row.judgment_status is not None:
            candidates_by_submission[submission_id].append(
                (row.judgment_created_at, row.judgment_status, row.final_verdict)
            )

    for submission_id, candidates in candidates_by_submission.items():
        _, judgment_status, final_verdict = max(candidates, key=lambda candidate: candidate[0])
        report_row = rows_by_submission[submission_id]
        report_row.judgment_status = judgment_status
        report_row.final_verdict = final_verdict

    return [rows_by_submission[submission_id] for submission_id in submission_order]


@dataclass(slots=True)
class ContestReportProblemRow:
    """One contest problem's report-relevant columns, independent of activity."""

    id: str
    ordinal: int
    title: str
    color: str


async def list_contest_report_problems(session: AsyncSession, contest: Contest) -> list[ContestReportProblemRow]:
    """Load every problem in the contest, regardless of submission activity.

    This is the report's authoritative problem set. Deriving it from the
    (possibly site-scoped) submissions instead -- "which problems appear in
    `list_contest_report_submissions`'s result" -- would silently drop a
    problem nobody in the current scope has a judged submission for: a site
    whose teams never touched problem L would simply not show an L column
    anywhere, reading as an 11-problem contest instead of a 12-problem one
    where L got zero traction at that site. Loading the full set here and
    letting every count default to zero keeps the problem visible with
    honest zeros, the same treatment an attempted-but-unsolved problem
    already gets.
    """
    result = await session.execute(
        select(
            problems_table.c.id,
            problems_table.c.ordinal,
            problems_table.c.title,
            problems_table.c.color,
        )
        .where(problems_table.c.contest_id == contest.id)
        .order_by(problems_table.c.ordinal, problems_table.c.id)
    )
    return [
        ContestReportProblemRow(
            id=str(row.id),
            ordinal=int(row.ordinal),
            title=str(row.title),
            color=str(row.color),
        )
        for row in result
    ]
