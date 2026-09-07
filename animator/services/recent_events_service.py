#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Recent-activity seed for the animator scoreboard's ticker.

The scoreboard's activity rail is otherwise session-local: it shows only what the
page's own ``EventSource`` observed, so a projector opened mid-contest -- or
reloaded -- starts blank and stays blank until the next submission. This module
answers the same question from rows the snapshot has *already* loaded, so a page
can seed its rail with the last few things that happened.

Three properties matter and are load-bearing:

* **No extra query.** The builder consumes ``_Projection``'s submissions,
  judgments and standings. It runs inside the snapshot build and therefore
  inside the animator's per-process feed cache.
* **Same freeze boundary.** Visibility is decided by the rule
  :func:`shared.services.scoreboard_projection.compute_icpc` uses -- drop
  anything past ``freeze_at_seconds`` while the viewer sees a frozen board -- so
  the ticker can never narrate a run the board is withholding.
* **Same event keys as the live stream.** A seeded verdict is keyed
  ``verdict:<judgment_id>`` and a seeded unresolved submission
  ``submission:<submission_id>``, exactly as the SSE handlers key theirs, so a
  page that seeds and *then* receives the same event renders it once.

Wording is deliberately not built here. The payload carries a ``kind`` and the
parts (team, label, verdict) and the client composes the sentence, so the seeded
backlog and the live stream cannot drift into two vocabularies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from animator.models.query_records import (
    JudgmentRecord,
    ProblemRecord,
    SubmissionRecord,
    TeamRecord,
)
from animator.models.responses import RecentEventKind, RecentEventResponse
from shared.enumerations import Verdict
from shared.services.scoreboard_projection import (
    ordinal_to_label,
    submission_sort_key,
)
from shared.timing import icpc_minutes_from_seconds

__all__ = ["RECENT_EVENT_LIMIT", "build_recent_events"]

# How many past events a fresh page is seeded with. Small on purpose: the rail
# is a "what just happened" ticker, not a run log, and its own client-side
# window holds 30.
RECENT_EVENT_LIMIT = 10


def build_recent_events(
    submission_records: Sequence[SubmissionRecord],
    judgments: Mapping[str, JudgmentRecord | None],
    teams: Sequence[TeamRecord],
    problem_records: Sequence[ProblemRecord],
    *,
    freeze_at_seconds: int,
    viewer_sees_frozen: bool,
    accept_pe: bool,
    limit: int = RECENT_EVENT_LIMIT,
) -> list[RecentEventResponse]:
    """Build the newest visible activity entries, oldest first.

    One entry is emitted per *submission*, never two: an unresolved submission
    reports that it was submitted, and a resolved one reports its result. A
    submission that was judged is narrated by its verdict alone, because the
    "submitted" half is what the ticker already said at the time and repeating it
    in a ten-item backlog would halve the history a page starts with.

    A resolved submission is the cell's *solving* one when it is the first
    accepted submission for that team and problem in canonical order -- the same
    "stop at the first accepted" rule ``compute_icpc`` applies -- and it earns the
    balloon wording. It earns the first-solver wording when it is also the
    earliest accepted submission for that problem across every visible team,
    which is exactly ``is_first_balloon``. A later accepted submission on an
    already-solved cell is neither, and reports its bare verdict.

    Args:
        submission_records: Visible submissions for the requested scope.
        judgments: Effective judgments keyed by submission id.
        teams: Team records supplying display names.
        problem_records: Problem records supplying labels.
        freeze_at_seconds: Contest-relative freeze boundary in seconds.
        viewer_sees_frozen: Whether post-freeze submissions are withheld.
        accept_pe: Whether a presentation error counts as accepted.
        limit: Maximum entries to return.

    Returns:
        Up to ``limit`` entries ordered oldest first, ready to append to the
        client's rail in the order it would have received them live.
    """
    team_name_by_id = {team.id: team.username for team in teams}
    team_fullname_by_id = {team.id: team.fullname for team in teams}
    label_by_problem = {problem.id: ordinal_to_label(problem.ordinal) for problem in problem_records}

    visible = [
        submission
        for submission in submission_records
        if not (viewer_sees_frozen and int(submission.timestamp_seconds) > freeze_at_seconds)
    ]
    visible.sort(key=submission_sort_key)

    def accepted(submission: SubmissionRecord) -> bool:
        judgment = judgments.get(submission.id)
        verdict = judgment.final_verdict if judgment is not None else None
        return verdict == Verdict.AC or (accept_pe and verdict == Verdict.PE)

    solving_by_cell: dict[tuple[str, str], str] = {}
    first_by_problem: dict[str, str] = {}
    for submission in visible:
        if not accepted(submission):
            continue
        solving_by_cell.setdefault((submission.team_id, submission.problem_id), submission.id)
        first_by_problem.setdefault(submission.problem_id, submission.id)

    entries: list[RecentEventResponse] = []
    # `visible[-0:]` is the whole list, so an explicit empty case is required.
    for submission in visible[-limit:] if limit > 0 else []:
        team_name = team_name_by_id.get(submission.team_id)
        team_fullname = team_fullname_by_id.get(submission.team_id) or team_name or ""
        label = label_by_problem.get(submission.problem_id)
        # A submission whose team or problem is outside the scope cannot be
        # named, and the rail never renders raw identifiers.
        if not team_name or not label:
            continue
        # The helper is nullable only for a nullable offset; this one is an int.
        minute = icpc_minutes_from_seconds(int(submission.timestamp_seconds)) or 0
        judgment = judgments.get(submission.id)
        verdict = judgment.final_verdict if judgment is not None else None
        if judgment is None or verdict is None:
            entries.append(
                RecentEventResponse(
                    key=f"submission:{submission.id}",
                    minute=minute,
                    kind="submitted",
                    team_name=team_name,
                    team_fullname=team_fullname,
                    problem_label=label,
                )
            )
            continue
        kind: RecentEventKind = "verdict"
        if solving_by_cell.get((submission.team_id, submission.problem_id)) == submission.id:
            kind = "first" if first_by_problem.get(submission.problem_id) == submission.id else "balloon"
        entries.append(
            RecentEventResponse(
                key=f"verdict:{judgment.id}",
                minute=minute,
                kind=kind,
                team_name=team_name,
                team_fullname=team_fullname,
                problem_label=label,
                verdict=verdict.value,
            )
        )
    return entries
