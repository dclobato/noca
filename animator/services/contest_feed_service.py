#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read-only contest feed for the animator runtime.

Public orchestration for one enabled contest: it composes the Core loaders in
:mod:`animator.services.contest_queries` and maps their records to typed public
responses. Scoring is delegated to
:func:`shared.services.scoreboard_projection.compute_icpc`, so the animator never
re-implements NOCA scoring rules and never imports ``web``.

``load_enabled_contest`` is re-exported here so the feed service remains the
single public entry point for contest resolution.

Query budget (independent of team/problem/submission/site counts):

* meta:     problems + sites + site team-counts (3 queries; contest already loaded)
* snapshot: teams + problems + submissions/judgments (3 queries; contest already loaded)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.query_records import (
    ContestRecord,
    JudgmentRecord,
    ProblemRecord,
    SubmissionRecord,
    TeamRecord,
)
from animator.models.responses import (
    ContestMetaResponse,
    PendingSubmissionResponse,
    ProblemCellResponse,
    ProblemMeta,
    ScoreboardSnapshotResponse,
    SiteMeta,
    TeamStandingResponse,
)
from animator.models.reveal_session import MedalCutoffs
from animator.services.contest_queries import (
    load_enabled_contest,
    load_problems,
    load_sites,
    load_submission_rows,
    load_teams,
)
from shared.services.balloon_assets import medal_band_for_rank
from shared.services.scoreboard_projection import (
    ScoreboardSnapshot,
    TeamStanding,
    bucket_visible_pending_submissions,
    compute_icpc,
    ordinal_to_label,
)

__all__ = [
    "build_meta_response",
    "build_pending_submissions",
    "build_snapshot",
    "build_snapshot_response",
    "load_enabled_contest",
    "snapshot_to_response",
]


def _now_utc(now: datetime | None) -> datetime:
    """Return the injected clock value or the current UTC instant."""
    return now if now is not None else datetime.now(UTC)


@dataclass(frozen=True)
class _Projection:
    """Loaded rows plus the computed standings and snapshot for one contest.

    The rows and the ``standings`` are computed exactly once so both the
    snapshot response and the authoritative pending-submission list are derived
    from the same visible data.
    """

    teams: list[TeamRecord]
    problem_records: list[ProblemRecord]
    submission_records: list[SubmissionRecord]
    judgments: dict[str, JudgmentRecord | None]
    standings: list[TeamStanding]
    snapshot: ScoreboardSnapshot


async def _project(
    session: AsyncSession,
    contest: ContestRecord,
    now: datetime | None = None,
    site_id: str | None = None,
) -> _Projection:
    """Load contest rows once and compute a global or site projection."""
    reference = _now_utc(now)
    teams = await load_teams(session, contest.id)
    if site_id is not None:
        teams = [team for team in teams if team.site_id == site_id]
    problem_records = await load_problems(session, contest.id)
    submission_records, judgments = await load_submission_rows(session, contest.id)
    if site_id is not None:
        team_ids = {team.id for team in teams}
        submission_records = [submission for submission in submission_records if submission.team_id in team_ids]
        judgments = {submission.id: judgments.get(submission.id) for submission in submission_records}

    standings = compute_icpc(
        contest=contest,
        teams=teams,
        problems=problem_records,
        submissions=submission_records,
        judgments=judgments,
        freeze_at_seconds=contest.freeze_at_seconds,
        viewer_sees_frozen=contest.is_frozen_at(reference),
    )
    labels = [ordinal_to_label(problem.ordinal) for problem in problem_records]
    balloon_colors = [problem.color.lstrip("#") for problem in problem_records]
    snapshot = ScoreboardSnapshot(
        contest_id=contest.id,
        generated_at=reference.isoformat(),
        is_frozen=contest.is_frozen_at(reference),
        standings=standings,
        problems=labels,
        balloon_colors=balloon_colors,
    )
    return _Projection(
        teams=teams,
        problem_records=problem_records,
        submission_records=submission_records,
        judgments=judgments,
        standings=standings,
        snapshot=snapshot,
    )


async def build_snapshot(
    session: AsyncSession,
    contest: ContestRecord,
    now: datetime | None = None,
    site_id: str | None = None,
) -> ScoreboardSnapshot:
    """Build the public scoreboard snapshot for an enabled contest.

    Args:
        session: Active database session.
        contest: The enabled contest to project.
        now: Injectable clock for deterministic freeze/timestamp behavior.
        site_id: Optional site whose teams define the scoreboard scope.

    Returns:
        A shared ``ScoreboardSnapshot`` computed with public freeze visibility.
    """
    projection = await _project(session, contest, now=now, site_id=site_id)
    return projection.snapshot


def build_pending_submissions(
    standings: list[TeamStanding],
    submission_records: list[SubmissionRecord],
    judgments: dict[str, JudgmentRecord | None],
    teams: list[TeamRecord],
    problem_records: list[ProblemRecord],
    freeze_at_seconds: int,
) -> list[PendingSubmissionResponse]:
    """Build the authoritative, freeze-safe pending-submission list.

    One entry is emitted per scoreboard cell whose ``is_pending`` is True (so the
    list agrees with the ``?`` cells and the not-solved rule). ``is_pending`` marks
    a *cell* visible but not which *submissions* in it are visible, so the cell's
    candidates are filtered to unresolved submissions (judgment absent or
    ``final_verdict is None``) at or before the freeze boundary — the same boundary
    ``compute_icpc`` uses — and the newest of that filtered set supplies
    ``submission_id`` / ``created_at``. This keeps a newer post-freeze unresolved
    submission from leaking through ordering. The whole list is sorted newest-first.

    Args:
        standings: Computed standings whose cells carry ``is_pending``.
        submission_records: All visible submissions in the projection.
        judgments: Effective judgments keyed by submission id.
        teams: Team records used to resolve display names.
        problem_records: Problem records used to derive labels.
        freeze_at_seconds: Contest-relative freeze boundary in seconds.

    Returns:
        Pending-submission entries ordered newest ``created_at`` first.
    """
    team_name_by_id = {team.id: team.username for team in teams}
    ordinal_by_problem = {problem.id: problem.ordinal for problem in problem_records}
    pending_by_cell = bucket_visible_pending_submissions(
        submission_records,
        judgments,
        freeze_at_seconds=freeze_at_seconds,
        viewer_sees_frozen=True,
    )

    def sort_key(submission: SubmissionRecord) -> tuple[datetime, int, str]:
        created = submission.created_at
        if created is None:
            created = datetime.min.replace(tzinfo=UTC)
        elif created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (created, submission.timestamp_seconds, submission.id)

    entries: list[tuple[tuple[datetime, int, str], PendingSubmissionResponse]] = []
    for row in standings:
        for cell in row.problems.values():
            if not cell.is_pending:
                continue
            candidates = pending_by_cell.get((row.team_id, cell.problem_id), [])
            if not candidates:
                continue
            newest = max(candidates, key=sort_key)
            key = sort_key(newest)
            entries.append(
                (
                    key,
                    PendingSubmissionResponse(
                        submission_id=newest.id,
                        team_id=row.team_id,
                        problem_id=cell.problem_id,
                        team_name=team_name_by_id.get(row.team_id, row.team_name),
                        problem_label=ordinal_to_label(ordinal_by_problem[cell.problem_id]),
                        created_at=key[0].isoformat(),
                    ),
                )
            )

    entries.sort(key=lambda item: item[0], reverse=True)
    return [entry[1] for entry in entries]


def snapshot_to_response(
    snapshot: ScoreboardSnapshot,
    pending_submissions: list[PendingSubmissionResponse] | None = None,
    *,
    teams: list[TeamRecord],
    wa_penalty: int,
    cutoffs: MedalCutoffs | None = None,
) -> ScoreboardSnapshotResponse:
    """Map a shared snapshot to the typed public response model.

    Args:
        snapshot: The shared scoreboard projection.
        pending_submissions: Freeze-safe unresolved submissions, if any.
        teams: Team records, used to resolve each row's site name.
        wa_penalty: Minutes added per penalizing attempt.
        cutoffs: Medal cutoffs in force for the requested scope, or ``None`` when
            the scope has none configured.

    Returns:
        The typed snapshot response, each row carrying its medal band.

    Medal bands come from the shared ``medal_band_for_rank`` -- the same function
    the ceremony's ``medal_for_rank`` delegates to -- so the live scoreboard and
    the reveal ceremony cannot disagree about who is on the podium.
    """
    team_by_id = {team.id: team for team in teams}
    standings = [
        TeamStandingResponse(
            medal=(
                None
                if cutoffs is None
                else medal_band_for_rank(row.rank, gold=cutoffs.gold, silver=cutoffs.silver, bronze=cutoffs.bronze)
            ),
            rank=row.rank,
            team_id=row.team_id,
            team_name=row.team_name,
            team_fullname=row.team_fullname,
            site_name=team_by_id[row.team_id].site_name if row.team_id in team_by_id else None,
            problems_solved=row.problems_solved,
            total_time=row.total_time,
            problems={
                label: ProblemCellResponse(
                    label=cell.label,
                    problem_id=cell.problem_id,
                    solved=cell.solved,
                    attempts=cell.attempts,
                    solved_at_minutes=cell.solved_at_minutes,
                    penalty=cell.attempts * wa_penalty,
                    is_pending=cell.is_pending,
                    is_first_balloon=cell.is_first_balloon,
                )
                for label, cell in row.problems.items()
            },
        )
        for row in snapshot.standings
    ]
    return ScoreboardSnapshotResponse(
        contest_id=snapshot.contest_id,
        generated_at=snapshot.generated_at,
        version=snapshot.generated_at,
        is_frozen=snapshot.is_frozen,
        problems=snapshot.problems,
        balloon_colors=snapshot.balloon_colors,
        standings=standings,
        pending_submissions=pending_submissions or [],
    )


async def build_snapshot_response(
    session: AsyncSession,
    contest: ContestRecord,
    now: datetime | None = None,
    site_id: str | None = None,
    cutoffs: MedalCutoffs | None = None,
) -> ScoreboardSnapshotResponse:
    """Build and serialize a global or site scoreboard snapshot response.

    Args:
        session: Active database session.
        contest: The resolved animator-enabled contest.
        now: Reference instant, defaulting to the current time.
        site_id: Site to narrow the standings to, or ``None`` for global scope.
        cutoffs: Medal cutoffs in force for the requested scope. **Required**
            when ``site_id`` is given -- ``site_id`` alone does not let this
            service reach the selected site's cutoffs without re-querying every
            site, which the scope resolution already did. Global scope leaves
            this ``None`` and the contest's own global cutoffs are used.

    Returns:
        The typed snapshot response for the requested scope.

    Raises:
        ValueError: If a site scope is requested without its cutoffs.
    """
    # A site's cutoffs are NOT NULL, so their absence here is a caller mistake,
    # not "this site has no medals". Failing loudly beats the alternatives:
    # falling back to the contest-wide bands would show a site the wrong podium,
    # and defaulting to no medals would silently blank one.
    if cutoffs is None and site_id is not None:
        raise ValueError("a site-scoped snapshot requires the site's medal cutoffs")
    if cutoffs is None:
        cutoffs = MedalCutoffs.from_optional(
            contest.global_gold_cutoff,
            contest.global_silver_cutoff,
            contest.global_bronze_cutoff,
        )
    projection = await _project(session, contest, now=now, site_id=site_id)
    pending_submissions = build_pending_submissions(
        projection.standings,
        projection.submission_records,
        projection.judgments,
        projection.teams,
        projection.problem_records,
        contest.freeze_at_seconds,
    )
    return snapshot_to_response(
        projection.snapshot,
        pending_submissions,
        teams=projection.teams,
        wa_penalty=contest.wa_penalty,
        cutoffs=cutoffs,
    )


async def build_meta_response(
    session: AsyncSession,
    contest: ContestRecord,
    now: datetime | None = None,
) -> ContestMetaResponse:
    """Build the public contest metadata response for an enabled contest."""
    reference = _now_utc(now)
    problem_records = await load_problems(session, contest.id)
    site_records = await load_sites(session, contest.id)

    problem_meta = [
        ProblemMeta(
            problem_id=problem.id,
            ordinal=problem.ordinal,
            label=ordinal_to_label(problem.ordinal),
            balloon_color=problem.color.lstrip("#"),
        )
        for problem in problem_records
    ]
    site_meta = [
        SiteMeta(
            site_id=site.id,
            name=site.sitename,
            gold_cutoff=site.gold_cutoff,
            silver_cutoff=site.silver_cutoff,
            bronze_cutoff=site.bronze_cutoff,
            team_count=site.team_count,
        )
        for site in site_records
    ]
    return ContestMetaResponse(
        contest_id=contest.id,
        slug=contest.login_slug,
        name=contest.contest_name,
        start_time=contest.start_time_utc.isoformat(),
        end_time=contest.end_time_utc.isoformat(),
        freeze_at=contest.freeze_at_utc.isoformat(),
        is_frozen=contest.is_frozen_at(reference),
        problems=problem_meta,
        sites=site_meta,
    )
