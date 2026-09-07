#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import anyio
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from shared.services.problem_judgeability import judgeability_error
from shared.timing import compute_timestamp_seconds, display_minutes_from_seconds
from web.models._base import _new_uuid
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.submission import HumanSubmissionConfirmation, Submission, SubmissionJudgment, SubmissionJudgmentAudit
from web.models.users import User
from web.routes.contest_admin_problem_helpers import _label
from web.services.judgment_utils import get_active_judgment
from web.services.problem_service import get_active_statement_path, load_contest_problem_judgeability_facts
from web.services.rate_limit_service import check_submission_rate_limit

if TYPE_CHECKING:
    from web.models.users import UberAdmin


class DuplicateSubmissionError(Exception):
    """Raised when a submission with identical (team, problem, language, hash) already exists."""


class SubmissionRateLimitError(Exception):
    """Raised when a team exceeds the per-window submission rate limit."""

    def __init__(self, next_allowed_at: datetime) -> None:
        """Initialize with the earliest time the team may submit again.

        Args:
            next_allowed_at: UTC datetime when the oldest in-window submission
                falls outside the window and the team may submit again.
        """
        super().__init__()
        self.next_allowed_at = next_allowed_at


@dataclass(frozen=True, slots=True)
class SubmissionFilters:
    """Optional server-side filters for contest submission lists."""

    problem_id: str | None = None
    team_id: str | None = None
    autojudge_verdict: Verdict | None = None
    final_verdict: Verdict | None = None


@dataclass(slots=True)
class _ExportSubmission:
    submission_id: str
    timestamp_seconds: int
    created_at: datetime
    verdict: Verdict
    source_filename: str
    source_code: str


@dataclass(slots=True)
class _ExportProblemArchive:
    problem_id: str
    folder_name: str
    entries: list[_ExportSubmission]


def _attachment_safe(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_." else "_" for char in value)
    return safe or "download"


def _accepted_submission(entries: list[_ExportSubmission]) -> tuple[_ExportSubmission, str] | None:
    for entry in entries:
        if entry.verdict == Verdict.AC:
            return entry, entry.source_filename
    for entry in entries:
        if entry.verdict == Verdict.PE:
            return entry, f"PE-{entry.source_filename}"
    return None


async def write_team_submissions_zip(
    session: AsyncSession,
    contest: Contest,
    team: User,
    *,
    statement_dir: Path,
    destination: Path,
) -> str:
    """Write the team submissions ZIP archive for a finished contest."""
    problems = list(
        (
            await session.execute(
                select(Problem).where(Problem.contest_id == contest.id).order_by(Problem.ordinal, Problem.id)
            )
        )
        .scalars()
        .all()
    )

    submissions = await list_submissions(session, contest, team)

    zip_filename = f"submissions-{_attachment_safe(contest.login_slug)}-{_attachment_safe(team.username)}.zip"
    archive_problems = _prepare_team_submission_archive(problems, submissions)
    await anyio.to_thread.run_sync(
        _write_team_submissions_zip,
        archive_problems,
        statement_dir,
        destination,
    )
    return zip_filename


def _prepare_team_submission_archive(
    problems: list[Problem],
    submissions: list[Submission],
) -> list[_ExportProblemArchive]:
    """Materialize ORM rows into plain archive data before threaded ZIP work."""
    archive_problems: list[_ExportProblemArchive] = []
    for problem in problems:
        problem_entries: list[_ExportSubmission] = []
        for submission in submissions:
            if str(submission.problem_id) != str(problem.id):
                continue
            judgment = get_active_judgment(submission)
            if judgment is None or judgment.final_verdict is None or submission.language is None:
                continue
            problem_entries.append(
                _ExportSubmission(
                    submission_id=submission.id,
                    timestamp_seconds=int(submission.timestamp_seconds),
                    created_at=submission.created_at,
                    verdict=judgment.final_verdict,
                    source_filename=submission.language.source_filename,
                    source_code=submission.source_code,
                )
            )
        problem_entries.sort(key=lambda entry: (entry.timestamp_seconds, entry.created_at, entry.submission_id))
        archive_problems.append(
            _ExportProblemArchive(
                problem_id=problem.id,
                folder_name=f"Problem {_label(problem.ordinal)}",
                entries=problem_entries,
            )
        )
    return archive_problems


def _write_team_submissions_zip(
    archive_problems: list[_ExportProblemArchive],
    statement_dir: Path,
    destination: Path,
) -> None:
    """Assemble the team submissions ZIP archive from plain export data."""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for problem in archive_problems:
            folder_name = problem.folder_name
            zf.writestr(f"{folder_name}/", b"")

            statement_path = get_active_statement_path(problem.problem_id, statement_dir)
            if statement_path is None:
                raise ValueError("Statement file is missing — cannot export.")
            statement_name = "statement.md" if statement_path.suffix == ".md" else "statement.pdf"
            zf.write(statement_path, f"{folder_name}/{statement_name}")

            problem_entries = problem.entries
            if not problem_entries:
                continue

            zf.writestr(f"{folder_name}/AC/", b"")
            zf.writestr(f"{folder_name}/Other/", b"")
            accepted_entry = _accepted_submission(problem_entries)
            accepted_id = accepted_entry[0].submission_id if accepted_entry is not None else None

            for entry in problem_entries:
                if entry.submission_id == accepted_id and accepted_entry is not None:
                    zf.writestr(f"{folder_name}/AC/{accepted_entry[1]}", entry.source_code.encode("utf-8"))
                    continue
                display_minutes = display_minutes_from_seconds(entry.timestamp_seconds)
                zf.writestr(
                    f"{folder_name}/Other/{display_minutes:04d}-{entry.verdict.value}-{entry.source_filename}",
                    entry.source_code.encode("utf-8"),
                )


SubmissionSort = Literal["time_asc", "time_desc", "problem_asc", "problem_desc"]

_ALLOWED_SUBMISSION_SORTS: frozenset[str] = frozenset({"time_asc", "time_desc", "problem_asc", "problem_desc"})


def normalize_submission_sort(value: str | None) -> SubmissionSort:
    """Normalize a runs list ``sort_by`` query parameter."""
    if value in _ALLOWED_SUBMISSION_SORTS:
        return cast(SubmissionSort, value)
    return "time_desc"


def _order_for_submission_sort(sort_by: SubmissionSort) -> tuple[Any, ...]:
    """Return the ORDER BY clauses for *sort_by*; problem sort ties break on newest first."""
    if sort_by == "time_asc":
        return (Submission.created_at.asc(),)
    if sort_by == "problem_asc":
        return (Problem.ordinal.asc(), Submission.created_at.desc())
    if sort_by == "problem_desc":
        return (Problem.ordinal.desc(), Submission.created_at.desc())
    return (Submission.created_at.desc(),)


def _apply_submission_filters(stmt: Select[tuple[Submission]], filters: SubmissionFilters) -> Select[tuple[Submission]]:
    """Apply Runs-page filters to a submission query."""
    if filters.problem_id:
        stmt = stmt.where(Submission.problem_id == filters.problem_id)
    if filters.team_id:
        stmt = stmt.where(Submission.team_id == filters.team_id)

    if filters.autojudge_verdict is None and filters.final_verdict is None:
        return stmt

    latest_judgment_id = (
        select(SubmissionJudgment.id)
        .where(SubmissionJudgment.submission_id == Submission.id)
        .order_by(SubmissionJudgment.created_at.desc(), SubmissionJudgment.id.desc())
        .limit(1)
        .correlate(Submission)
        .scalar_subquery()
    )
    stmt = stmt.join(SubmissionJudgment, SubmissionJudgment.id == latest_judgment_id)
    if filters.autojudge_verdict is not None:
        stmt = stmt.where(SubmissionJudgment.autojudge_verdict == filters.autojudge_verdict)
    if filters.final_verdict is not None:
        stmt = stmt.where(SubmissionJudgment.final_verdict == filters.final_verdict)
    return stmt


async def list_submission_teams(session: AsyncSession, contest: Contest) -> list[User]:
    """Return contest teams that have submissions for the Runs filter."""
    stmt = (
        select(User)
        .join(Submission, Submission.team_id == User.id)
        .join(Problem, Submission.problem_id == Problem.id)
        .where(Problem.contest_id == contest.id, User.role == RoleEnum.TEAM)
        .options(selectinload(User.site))
        .distinct()
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_submissions(
    session: AsyncSession,
    contest: Contest,
    actor: UberAdmin | User,
    sort_by: SubmissionSort = "time_desc",
    *,
    filters: SubmissionFilters | None = None,
) -> list[Submission]:
    """Return submissions visible to *actor* for the given contest.

    TEAM users see only their own submissions; all other roles see every
    submission in the contest. Both default to newest-first.
    """
    opts = [
        selectinload(Submission.problem),
        selectinload(Submission.language),
        selectinload(Submission.judgments),
        selectinload(Submission.team).selectinload(User.site),
    ]
    order = _order_for_submission_sort(sort_by)
    filters = filters or SubmissionFilters()

    if hasattr(actor, "role") and actor.role == RoleEnum.TEAM:
        stmt = (
            select(Submission)
            .join(Problem, Submission.problem_id == Problem.id)
            .where(Submission.team_id == actor.id)
            .options(*opts)
        )
    else:
        judge_opts = opts + [
            selectinload(Submission.judgments)
            .selectinload(SubmissionJudgment.confirmations)
            .selectinload(HumanSubmissionConfirmation.judge)
            .selectinload(User.site),
            selectinload(Submission.judgments).selectinload(SubmissionJudgment.overrides),
        ]
        stmt = (
            select(Submission)
            .join(Problem, Submission.problem_id == Problem.id)
            .where(Problem.contest_id == contest.id)
            .options(*judge_opts)
        )

    stmt = _apply_submission_filters(stmt, filters).order_by(*order)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_submission(
    session: AsyncSession,
    actor: User,
    contest: Contest,
    problem_id: str,
    language_id: str,
    source_code: str,
    source_hash: str,
    source_size: int,
    rate_limit_window_seconds: int = 60,
    rate_limit_max_submissions: int = 3,
) -> tuple[Submission, SubmissionJudgment]:
    """Create a Submission and its initial SubmissionJudgment (status=QUEUED).

    Also adds an explicit WEB audit row alongside the one produced by the
    model hook.  Does NOT commit — the caller owns the transaction.

    Args:
        session: Active async SQLAlchemy session.
        actor: The TEAM user submitting the solution.
        contest: The contest the submission belongs to (used to compute timestamp_seconds).
        problem_id: UUID of the problem being submitted.
        language_id: Language key for the submission.
        source_code: Source code string.
        source_hash: SHA-256 hex digest of source bytes.
        source_size: Size in bytes of the source file.
        rate_limit_window_seconds: Rolling window length in seconds for rate limiting.
        rate_limit_max_submissions: Maximum submissions allowed within the window.

    Raises:
        SubmissionRateLimitError: if the team has exceeded the rate limit for
            the current window, carrying next_allowed_at as the retry time.
        DuplicateSubmissionError: if an identical submission already exists
            (detected by pre-flight SELECT or by DB unique constraint race).
    """
    allowed, next_allowed_at = await check_submission_rate_limit(
        session, actor.id, rate_limit_window_seconds, rate_limit_max_submissions
    )
    if not allowed:
        assert next_allowed_at is not None
        raise SubmissionRateLimitError(next_allowed_at)

    # One shared contract, decided from the problem's stored strategy. An
    # interactive problem needs an active valid validator and secret input-only
    # cases; a standard one needs cases that all carry an expected output. A
    # problem that lost its validator is refused here rather than judged by the
    # token comparator against cases that have no expected output.
    facts = await load_contest_problem_judgeability_facts(session, problem_id)
    reason = judgeability_error(facts)
    if reason is not None:
        raise ValueError(reason)

    # Pre-flight duplicate check (fast path; race covered by DB constraint below)
    duplicate = (
        await session.execute(
            select(Submission.id).where(
                Submission.team_id == actor.id,
                Submission.problem_id == problem_id,
                Submission.language_id == language_id,
                Submission.source_hash == source_hash,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise DuplicateSubmissionError

    # Generate IDs explicitly so FK references work before flush
    submission_id = _new_uuid()
    judgment_id = _new_uuid()

    now = datetime.now(UTC)

    submission = Submission(
        id=submission_id,
        problem_id=problem_id,
        team_id=actor.id,
        language_id=language_id,
        source_code=source_code,
        source_hash=source_hash,
        source_size_bytes=source_size,
        timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(submission)

    judgment = SubmissionJudgment(
        id=judgment_id,
        submission_id=submission_id,
        status=JudgmentStatus.QUEUED,
        timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(judgment)

    # Flush now to catch any DB-level constraint violation (race condition)
    # and to allow the model hook to fire before we add the explicit WEB audit.
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise DuplicateSubmissionError from None

    # Explicit WEB-sourced audit (coexists with the model_hook/created row
    # emitted by the before_flush hook in submission.py)
    audit = SubmissionJudgmentAudit(
        judgment_id=judgment_id,
        submission_id=submission_id,
        actor_user_id=actor.id,
        event_source="WEB",
        event_type="SUBMISSION_CREATED",
        from_status=None,
        to_status=JudgmentStatus.QUEUED,
        message="Submission created and enqueued",
        created_at=now,
        timestamp_seconds=compute_timestamp_seconds(contest.start_time, now),
    )
    session.add(audit)

    return submission, judgment
