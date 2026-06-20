#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
web/services/animeitor_export_service.py

Export layer that produces a ZIP file compatible with the legacy BOCA webcast
protocol consumed by ``maratona-animeitor``.

The ZIP contains five files at the root:
- ``contest`` — contest metadata and team list (FS-delimited)
- ``runs``    — submission history (FS-delimited)
- ``time``    — elapsed contest time in seconds
- ``version`` — protocol version (``1.0``)
- ``icpc``    — empty (required by consumer layout)

This module intentionally does NOT modify NOCA's internal domain model.
All legacy-specific decisions (hardcoded penalty, verdict mapping, FS
separator) are confined here.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

import anyio
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from shared.timing import icpc_minutes_from_seconds
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.submission import Submission
from web.models.users import User
from web.routes.contest_admin_problem_helpers import _label
from web.services.judgment_utils import get_active_judgment

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FS = "\x1c"
"""ASCII File Separator — the field delimiter used by the legacy protocol."""

ANIMEITOR_PENALTY = 20
"""Hardcoded penalty exported for strict consumer compatibility."""

# Verdict mapping: NOCA Verdict → legacy status character.
# PE is context-dependent (accept_pe flag), handled in map_verdict().
_VERDICT_MAP: dict[Verdict, str] = {
    Verdict.AC: "Y",
    Verdict.WA: "N",
    Verdict.RE: "N",
    Verdict.TLE: "N",
    Verdict.MLE: "N",
    Verdict.OLE: "N",
    Verdict.CE: "X",
}

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AnimeitorExportError(Exception):
    """Raised when the contest cannot be exported to the Animeitor format."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnimeitorTeam:
    """A team as represented in the exported ``contest`` file."""

    login: str
    institution: str
    display_name: str


@dataclass(frozen=True, slots=True)
class AnimeitorRun:
    """A submission as represented in the exported ``runs`` file."""

    run_id: int
    time_minutes: int
    team_login: str
    problem_letter: str
    status: str


# ---------------------------------------------------------------------------
# Pure functions (no I/O — unit-testable)
# ---------------------------------------------------------------------------


def map_verdict(verdict: Verdict | None, accept_pe: bool) -> str:
    """Map a NOCA verdict to the legacy status character.

    Args:
        verdict: The final verdict from the active judgment, or ``None``
            when no verdict is available yet.
        accept_pe: Whether Presentation Error counts as accepted.

    Returns:
        One of ``"Y"``, ``"N"``, ``"X"``, or ``"?"``.
    """
    if verdict is None:
        return "?"
    if verdict == Verdict.PE:
        return "Y" if accept_pe else "N"
    return _VERDICT_MAP.get(verdict, "?")


def serialize_contest_file(
    contest_name: str,
    max_time_minutes: int,
    current_time_minutes: int,
    freeze_time_minutes: int,
    penalty: int,
    teams: list[AnimeitorTeam],
    num_problems: int,
) -> str:
    """Serialize the ``contest`` file content.

    Args:
        contest_name: Display name of the contest.
        max_time_minutes: Total contest duration in minutes.
        current_time_minutes: Elapsed contest time in minutes.
        freeze_time_minutes: Scoreboard freeze time in minutes.
        penalty: Penalty per wrong answer (hardcoded to 20 for compat).
        teams: Ordered list of teams.
        num_problems: Number of problems in the contest.

    Returns:
        The full text content of the ``contest`` file.
    """
    lines: list[str] = []
    lines.append(contest_name)
    lines.append(f"{max_time_minutes}{FS}{current_time_minutes}{FS}{freeze_time_minutes}{FS}{penalty}")
    lines.append(f"{len(teams)}{FS}{num_problems}")
    for team in teams:
        lines.append(f"{team.login}{FS}{team.institution}{FS}{team.display_name}")
    lines.append(f"1{FS}1")
    lines.append(f"{num_problems}{FS}Y")
    return "\n".join(lines) + "\n"


def serialize_runs_file(runs: list[AnimeitorRun]) -> str:
    """Serialize the ``runs`` file content.

    Args:
        runs: Ordered list of runs (submissions).

    Returns:
        The full text content of the ``runs`` file, or an empty string
        when there are no runs.
    """
    if not runs:
        return ""
    lines: list[str] = []
    for run in runs:
        lines.append(f"{run.run_id}{FS}{run.time_minutes}{FS}{run.team_login}{FS}{run.problem_letter}{FS}{run.status}")
    return "\n".join(lines) + "\n"


def _build_zip_bytes(contest_content: str, runs_content: str, elapsed: int) -> bytes:
    """Assemble Animeitor files into a ZIP archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("contest", contest_content)
        zf.writestr("runs", runs_content)
        zf.writestr("time", str(elapsed))
        zf.writestr("version", "1.0")
        zf.writestr("icpc", "")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Async orchestrator
# ---------------------------------------------------------------------------


def _attachment_safe(value: str) -> str:
    """Sanitize a string for use in a Content-Disposition filename."""
    safe = "".join(char if char.isalnum() or char in "-_." else "_" for char in value)
    return safe or "download"


async def build_animeitor_zip(
    session: AsyncSession,
    contest: Contest,
) -> tuple[str, bytes]:
    """Build the Animeitor-compatible ZIP for a contest.

    Args:
        session: Active async SQLAlchemy session.
        contest: The contest to export.

    Returns:
        A ``(filename, zip_bytes)`` tuple.

    Raises:
        AnimeitorExportError: If the contest has no enrolled teams or no
            problems defined.
    """
    # --- Precondition: teams ---
    team_count = (
        await session.execute(
            select(func.count()).select_from(User).where(User.contest_id == contest.id, User.role == RoleEnum.TEAM)
        )
    ).scalar_one()
    if team_count == 0:
        raise AnimeitorExportError("No teams enrolled in this contest")

    # --- Precondition: problems ---
    problem_count = (
        await session.execute(select(func.count()).select_from(Problem).where(Problem.contest_id == contest.id))
    ).scalar_one()
    if problem_count == 0:
        raise AnimeitorExportError("No problems defined in this contest")

    # --- Load teams ---
    teams_rows = (
        (
            await session.execute(
                select(User).where(User.contest_id == contest.id, User.role == RoleEnum.TEAM).order_by(User.username)
            )
        )
        .scalars()
        .all()
    )

    animeitor_teams = [
        AnimeitorTeam(
            login=t.username,
            institution=contest.contest_name,
            display_name=t.fullname,
        )
        for t in teams_rows
    ]

    # --- Load problems ---
    problem_rows = (
        (await session.execute(select(Problem).where(Problem.contest_id == contest.id).order_by(Problem.ordinal)))
        .scalars()
        .all()
    )

    problem_labels: dict[str, str] = {str(p.id): _label(p.ordinal) for p in problem_rows}

    # --- Load submissions with judgments ---
    team_ids: dict[str, str] = {str(t.id): t.username for t in teams_rows}

    submissions = (
        (
            await session.execute(
                select(Submission)
                .join(Problem, Submission.problem_id == Problem.id)
                .where(Problem.contest_id == contest.id)
                .options(selectinload(Submission.judgments))
                .order_by(
                    Submission.timestamp_seconds,
                    Submission.created_at,
                    Submission.id,
                )
            )
        )
        .scalars()
        .all()
    )

    # --- Build runs ---
    animeitor_runs: list[AnimeitorRun] = []
    for idx, sub in enumerate(submissions, start=1):
        active = get_active_judgment(sub)
        if active is not None and active.status == JudgmentStatus.DONE and active.final_verdict is not None:
            status = map_verdict(active.final_verdict, contest.accept_pe)
        else:
            status = "?"

        time_min = icpc_minutes_from_seconds(sub.timestamp_seconds)

        animeitor_runs.append(
            AnimeitorRun(
                run_id=idx,
                time_minutes=time_min if time_min is not None else 0,
                team_login=team_ids.get(str(sub.team_id), "unknown"),
                problem_letter=problem_labels.get(str(sub.problem_id), "?"),
                status=status,
            )
        )

    # --- Serialize files ---
    elapsed = contest.elapsed_time_seconds
    current_time_minutes = elapsed // 60

    contest_content = serialize_contest_file(
        contest_name=contest.contest_name,
        max_time_minutes=contest.duration_minutes,
        current_time_minutes=current_time_minutes,
        freeze_time_minutes=contest.stop_updating_scoreboard,
        penalty=ANIMEITOR_PENALTY,
        teams=animeitor_teams,
        num_problems=len(problem_rows),
    )
    runs_content = serialize_runs_file(animeitor_runs)

    zip_bytes = await anyio.to_thread.run_sync(_build_zip_bytes, contest_content, runs_content, elapsed)
    filename = f"animeitor-{_attachment_safe(contest.login_slug)}.zip"
    return filename, zip_bytes
