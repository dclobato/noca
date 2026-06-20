#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from shared.timing import compute_timestamp_seconds, display_minutes_from_seconds
from web.models._base import _new_uuid
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.submission import HumanSubmissionConfirmation, Submission, SubmissionJudgment, SubmissionJudgmentAudit
from web.models.users import User
from web.routes.contest_admin_problem_helpers import _label
from web.services.judgment_utils import get_active_judgment
from web.services.problem_service import get_active_statement_path
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


async def build_team_submissions_zip(
    session: AsyncSession,
    contest: Contest,
    team: User,
    *,
    statement_dir: Path,
) -> tuple[str, bytes]:
    """Build the team submissions ZIP archive for a finished contest."""
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
    zip_bytes = await anyio.to_thread.run_sync(
        _build_team_submissions_zip_bytes,
        archive_problems,
        statement_dir,
    )
    return zip_filename, zip_bytes


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


def _build_team_submissions_zip_bytes(archive_problems: list[_ExportProblemArchive], statement_dir: Path) -> bytes:
    """Assemble the team submissions ZIP archive from plain export data."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for problem in archive_problems:
            folder_name = problem.folder_name
            zf.writestr(f"{folder_name}/", b"")

            statement_path = get_active_statement_path(problem.problem_id, statement_dir)
            if statement_path is None:
                raise ValueError("Statement file is missing — cannot export.")
            statement_name = "statement.md" if statement_path.suffix == ".md" else "statement.pdf"
            zf.writestr(f"{folder_name}/{statement_name}", statement_path.read_bytes())

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

    return buffer.getvalue()


async def list_submissions(
    session: AsyncSession,
    contest: Contest,
    actor: UberAdmin | User,
) -> list[Submission]:
    """Return submissions visible to *actor* for the given contest.

    TEAM users see only their own submissions ordered newest-first.
    All other roles see every submission in the contest ordered newest-first.
    """
    opts = [
        selectinload(Submission.problem),
        selectinload(Submission.language),
        selectinload(Submission.judgments),
        selectinload(Submission.team).selectinload(User.site),
    ]

    if hasattr(actor, "role") and actor.role == RoleEnum.TEAM:
        stmt = (
            select(Submission)
            .where(Submission.team_id == actor.id)
            .order_by(Submission.created_at.desc())
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
            .order_by(Submission.created_at.desc())
            .options(*judge_opts)
        )

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
