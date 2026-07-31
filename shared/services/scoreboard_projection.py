#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
shared/services/scoreboard_projection.py

Pure ICPC scoreboard projection shared by every NOCA runtime.

This module is the single owner of the scoreboard semantics: data models,
snapshot serialization, and the pure ``compute_icpc`` calculation. It accepts
structural inputs (protocols) so it never type-imports ``web`` models — the
web layer (and later the animator runtime) only adapts its own records to the
protocols defined here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from shared.enumerations import Verdict
from shared.timing import icpc_minutes_from_seconds


class ContestScoringInput(Protocol):
    """Contest attributes consumed by the ICPC scoring rules.

    Attributes:
        wa_penalty: Minutes added for each penalizing attempt before a solve.
        accept_pe: Whether presentation errors count as accepted submissions.
        ce_adds_penalty: Whether compilation errors count as penalizing attempts.
    """

    @property
    def wa_penalty(self) -> int: ...
    @property
    def accept_pe(self) -> bool: ...
    @property
    def ce_adds_penalty(self) -> bool: ...


class TeamInput(Protocol):
    """Team attributes consumed by the scoreboard projection.

    Attributes:
        id: Stable team identifier.
        username: Short team name displayed in the scoreboard.
        fullname: Full team name displayed in expanded views.
    """

    @property
    def id(self) -> str: ...
    @property
    def username(self) -> str: ...
    @property
    def fullname(self) -> str: ...


class ProblemInput(Protocol):
    """Problem attributes consumed by the scoreboard projection.

    Attributes:
        id: Stable problem identifier.
        ordinal: One-based problem position used to derive its label.
    """

    @property
    def id(self) -> str: ...
    @property
    def ordinal(self) -> int: ...


class SubmissionInput(Protocol):
    """Submission attributes consumed by the scoreboard projection.

    Attributes:
        id: Stable submission identifier.
        team_id: Identifier of the submitting team.
        problem_id: Identifier of the submitted problem.
        timestamp_seconds: Contest-relative submission time in seconds.
        created_at: Creation timestamp used to break contest-time ties.
    """

    @property
    def id(self) -> str: ...
    @property
    def team_id(self) -> str: ...
    @property
    def problem_id(self) -> str: ...
    @property
    def timestamp_seconds(self) -> int: ...
    @property
    def created_at(self) -> datetime | None: ...


class JudgmentInput(Protocol):
    """Judgment attributes consumed by the scoreboard projection.

    Attributes:
        final_verdict: Effective verdict, or ``None`` while pending.
    """

    @property
    def final_verdict(self) -> Verdict | None: ...


@dataclass
class ProblemResult:
    """Scoreboard data for one team and problem cell.

    Attributes:
        label: Display label derived from the problem ordinal.
        problem_id: Stable problem identifier.
        solved: Whether the team solved the problem in the visible data.
        attempts: Penalizing attempts made before the accepted submission.
        solved_at_minutes: Contest minute of the accepted submission.
        penalty: Penalty minutes contributed by failed attempts.
        is_pending: Whether the cell contains an unresolved visible submission.
        is_first_balloon: Whether this cell contains the first solve for the problem.
    """

    label: str
    problem_id: str
    solved: bool
    attempts: int
    solved_at_minutes: int | None
    penalty: int
    is_pending: bool
    is_first_balloon: bool = False


@dataclass
class TeamStanding:
    """Scoreboard row for one team.

    Attributes:
        rank: Position-based rank, shared by teams with equal scores.
        team_id: Stable team identifier.
        team_name: Short team display name.
        team_fullname: Full team display name.
        problems_solved: Number of solved problems.
        total_time: Total ICPC time, including attempt penalties.
        problems: Problem results keyed by display label.
    """

    rank: int
    team_id: str
    team_name: str
    team_fullname: str
    problems_solved: int
    total_time: int
    problems: dict[str, ProblemResult]


@dataclass
class ScoreboardSnapshot:
    """Full scoreboard state at a point in time.

    Attributes:
        contest_id: Stable contest identifier.
        generated_at: ISO 8601 snapshot-generation timestamp.
        is_frozen: Whether the contest scoreboard is currently frozen.
        standings: Ranked team rows.
        problems: Problem labels in display order.
        balloon_colors: Problem balloon colors in display order.
    """

    contest_id: str
    generated_at: str
    is_frozen: bool
    standings: list[TeamStanding]
    problems: list[str]
    balloon_colors: list[str]


_CREATED_AT_FLOOR = datetime.min.replace(tzinfo=UTC)


def submission_sort_key(submission: SubmissionInput) -> tuple[int, datetime, str]:
    """Return the canonical submission ordering key.

    The key is ``(timestamp_seconds, created_at, id)``. This module is the single
    owner of that order, so every consumer — ``compute_icpc`` itself and the
    animator's frozen reveal universe — sorts identically. ``created_at`` is
    normalized *for comparison only*: naive values are read as UTC, and a
    ``created_at`` that is ``None`` — or absent entirely, which callers'
    lightweight records may be — sorts first rather than raising ``TypeError``
    mid-sort.

    Args:
        submission: The submission to key.

    Returns:
        A totally ordered comparison key.
    """
    created = getattr(submission, "created_at", None)
    if created is None:
        created = _CREATED_AT_FLOOR
    elif created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (int(submission.timestamp_seconds), created, str(submission.id))


def ordinal_to_label(ordinal: int) -> str:
    """Convert a one-based problem ordinal to a spreadsheet-style label.

    Args:
        ordinal: One-based problem ordinal.

    Returns:
        The corresponding label, such as ``A``, ``Z``, or ``AA``.
    """
    ordinal -= 1
    label = ""
    while True:
        label = chr(ord("A") + ordinal % 26) + label
        ordinal = ordinal // 26 - 1
        if ordinal < 0:
            break
    return label


def bucket_visible_pending_submissions[SubmissionT: SubmissionInput](
    submissions: Sequence[SubmissionT],
    judgments: Mapping[str, JudgmentInput | None],
    *,
    freeze_at_seconds: int,
    viewer_sees_frozen: bool,
) -> dict[tuple[str, str], list[SubmissionT]]:
    """Group unresolved submissions visible to the scoreboard viewer.

    Args:
        submissions: Submissions to inspect.
        judgments: Effective judgments keyed by submission identifier.
        freeze_at_seconds: Contest-relative freeze boundary in seconds.
        viewer_sees_frozen: Whether to hide submissions after the boundary.

    Returns:
        Visible unresolved submissions keyed by ``(team_id, problem_id)``.
    """
    pending_by_cell: dict[tuple[str, str], list[SubmissionT]] = defaultdict(list)
    for submission in submissions:
        if viewer_sees_frozen and int(submission.timestamp_seconds) > freeze_at_seconds:
            continue
        judgment = judgments.get(str(submission.id))
        if judgment is None or judgment.final_verdict is None:
            pending_by_cell[(str(submission.team_id), str(submission.problem_id))].append(submission)
    return pending_by_cell


def compute_icpc(
    contest: ContestScoringInput,
    teams: Sequence[TeamInput],
    problems: Sequence[ProblemInput],
    submissions: Sequence[SubmissionInput],
    judgments: Mapping[str, JudgmentInput | None],
    freeze_at_seconds: int,
    viewer_sees_frozen: bool,
) -> list[TeamStanding]:
    """Compute deterministic ICPC standings from raw structural inputs.

    Args:
        contest: Contest-specific scoring options.
        teams: Teams included in the projection.
        problems: Problems included in display order.
        submissions: Submissions in any input order.
        judgments: Effective judgments keyed by submission identifier.
        freeze_at_seconds: Contest-relative freeze boundary in seconds.
        viewer_sees_frozen: Whether to hide submissions after the freeze boundary.

    Returns:
        Team standings ordered by solved count and total time.
    """
    wa_penalty = int(contest.wa_penalty)
    accept_pe = bool(contest.accept_pe)
    ce_adds_penalty = bool(contest.ce_adds_penalty)

    ordered_submissions = sorted(submissions, key=submission_sort_key)
    subs_by_team_problem: dict[tuple[str, str], list[SubmissionInput]] = defaultdict(list)
    for submission in ordered_submissions:
        subs_by_team_problem[(str(submission.team_id), str(submission.problem_id))].append(submission)
    pending_submissions_by_cell = bucket_visible_pending_submissions(
        ordered_submissions,
        judgments,
        freeze_at_seconds=freeze_at_seconds,
        viewer_sees_frozen=viewer_sees_frozen,
    )

    first_accepted_by_problem: dict[str, str] = {}
    for submission in ordered_submissions:
        ts_seconds = int(submission.timestamp_seconds)
        if viewer_sees_frozen and ts_seconds > freeze_at_seconds:
            continue

        judgment = judgments.get(str(submission.id))
        verdict = judgment.final_verdict if judgment is not None else None
        is_accepted = verdict == Verdict.AC or (accept_pe and verdict == Verdict.PE)
        if is_accepted:
            first_accepted_by_problem.setdefault(str(submission.problem_id), str(submission.id))

    problem_labels = {str(problem.id): ordinal_to_label(problem.ordinal) for problem in problems}
    unranked: list[tuple[int, int, TeamStanding]] = []

    for team in teams:
        team_id = str(team.id)
        total_time = 0
        problems_solved = 0
        problem_results: dict[str, ProblemResult] = {}

        for problem in problems:
            problem_id = str(problem.id)
            label = problem_labels[problem_id]
            team_submissions = subs_by_team_problem.get((team_id, problem_id), [])

            failed_attempts = 0
            solved = False
            solved_at_minutes: int | None = None
            solved_submission_id: str | None = None

            for submission in team_submissions:
                ts_seconds = int(submission.timestamp_seconds)
                if viewer_sees_frozen and ts_seconds > freeze_at_seconds:
                    break

                judgment = judgments.get(str(submission.id))
                verdict = judgment.final_verdict if judgment is not None else None
                if verdict is None:
                    continue

                is_accepted = verdict == Verdict.AC or (accept_pe and verdict == Verdict.PE)
                is_failed = (
                    verdict in (Verdict.WA, Verdict.RE, Verdict.TLE, Verdict.MLE, Verdict.OLE)
                    or (ce_adds_penalty and verdict == Verdict.CE)
                    or (not accept_pe and verdict == Verdict.PE)
                )

                if is_accepted:
                    solved = True
                    solved_at_minutes = icpc_minutes_from_seconds(ts_seconds)
                    solved_submission_id = str(submission.id)
                    break

                if is_failed:
                    failed_attempts += 1

            penalty = failed_attempts * wa_penalty if solved else 0
            if solved and solved_at_minutes is not None:
                problems_solved += 1
                total_time += solved_at_minutes + penalty

            problem_results[label] = ProblemResult(
                label=label,
                problem_id=problem_id,
                solved=solved,
                attempts=failed_attempts,
                solved_at_minutes=solved_at_minutes,
                penalty=penalty,
                is_pending=bool(pending_submissions_by_cell.get((team_id, problem_id))) and not solved,
                is_first_balloon=first_accepted_by_problem.get(problem_id) == solved_submission_id,
            )

        standing = TeamStanding(
            rank=0,
            team_id=team_id,
            team_name=str(team.username),
            team_fullname=str(team.fullname),
            problems_solved=problems_solved,
            total_time=total_time,
            problems=problem_results,
        )
        unranked.append((-problems_solved, total_time, standing))

    unranked.sort(key=lambda item: (item[0], item[1]))

    standings: list[TeamStanding] = []
    rank = 1
    for index, (_, _, standing) in enumerate(unranked):
        if index > 0:
            previous = unranked[index - 1][2]
            if standing.problems_solved != previous.problems_solved or standing.total_time != previous.total_time:
                rank = index + 1
        standing.rank = rank
        standings.append(standing)

    return standings


def snapshot_to_dict(snapshot: ScoreboardSnapshot) -> dict[str, Any]:
    """Serialize a scoreboard snapshot to a JSON-compatible dictionary.

    Args:
        snapshot: Snapshot to serialize.

    Returns:
        A dictionary containing only JSON-compatible snapshot values.
    """
    return asdict(snapshot)


def snapshot_from_dict(data: dict[str, Any]) -> ScoreboardSnapshot:
    """Deserialize a scoreboard snapshot from a JSON-compatible dictionary.

    Args:
        data: Serialized snapshot payload.

    Returns:
        The reconstructed scoreboard snapshot.
    """
    standings = [
        TeamStanding(
            rank=row["rank"],
            team_id=row["team_id"],
            team_name=row["team_name"],
            team_fullname=row.get("team_fullname", row["team_name"]),
            problems_solved=row["problems_solved"],
            total_time=row["total_time"],
            problems={
                label: ProblemResult(**{**problem, "is_first_balloon": problem.get("is_first_balloon", False)})
                for label, problem in row["problems"].items()
            },
        )
        for row in data["standings"]
    ]
    return ScoreboardSnapshot(
        contest_id=data["contest_id"],
        generated_at=data["generated_at"],
        is_frozen=data["is_frozen"],
        standings=standings,
        problems=data["problems"],
        balloon_colors=data["balloon_colors"],
    )
