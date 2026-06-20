#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure ICPC scoreboard computation."""

from __future__ import annotations

from collections import defaultdict

from shared.enumerations import Verdict
from shared.timing import icpc_minutes_from_seconds
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User

from .models import ProblemResult, TeamStanding


def ordinal_to_label(ordinal: int) -> str:
    """Convert a 1-based problem ordinal to a label."""
    ordinal -= 1
    label = ""
    while True:
        label = chr(ord("A") + ordinal % 26) + label
        ordinal = ordinal // 26 - 1
        if ordinal < 0:
            break
    return label


def compute_icpc(
    contest: Contest,
    teams: list[User],
    problems: list[Problem],
    submissions: list[Submission],
    judgments: dict[str, SubmissionJudgment | None],
    freeze_at_seconds: int,
    viewer_sees_frozen: bool,
) -> list[TeamStanding]:
    """Compute ICPC standings from raw data."""
    wa_penalty = int(contest.wa_penalty)
    accept_pe = bool(contest.accept_pe)
    ce_adds_penalty = bool(contest.ce_adds_penalty)

    subs_by_team_problem: dict[tuple[str, str], list[Submission]] = defaultdict(list)
    for submission in submissions:
        subs_by_team_problem[(str(submission.team_id), str(submission.problem_id))].append(submission)

    first_accepted_by_problem: dict[str, str] = {}
    ordered_submissions = sorted(
        submissions,
        key=lambda item: (int(item.timestamp_seconds), getattr(item, "created_at", None), str(item.id)),
    )
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
            has_pending = False

            for submission in team_submissions:
                ts_seconds = int(submission.timestamp_seconds)
                if viewer_sees_frozen and ts_seconds > freeze_at_seconds:
                    break

                judgment = judgments.get(str(submission.id))
                verdict = judgment.final_verdict if judgment is not None else None
                if verdict is None:
                    has_pending = True
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
                is_pending=has_pending and not solved,
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
