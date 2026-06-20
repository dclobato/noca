#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest report aggregation logic."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from shared.enumerations import JudgmentStatus, Verdict
from web.services.assorted_utils import format_site_identity
from web.services.judgment_utils import get_active_judgment

from .common import ordinal_to_label, pct
from .models import (
    CellValue,
    ContestReport,
    DistributionRow,
    LanguageInfo,
    ProblemInfo,
    ProblemSummaryRow,
)
from .tables import (
    build_language_verdict_table,
    build_problem_language_table,
    build_problem_verdict_table,
    build_team_rows,
    build_time_windows,
)

if TYPE_CHECKING:
    from web.models.contest import Contest
    from web.models.language import Language
    from web.models.submission import Submission


def compute_contest_report(
    contest: Contest,
    submissions: list[Submission],
    languages: list[Language],
) -> ContestReport:
    """Aggregate submissions into a contest report."""
    accept_pe = contest.accept_pe

    def is_accepted(verdict: Verdict) -> bool:
        return verdict == Verdict.AC or (accept_pe and verdict == Verdict.PE)

    judged: list[tuple[Submission, Verdict]] = []
    for submission in submissions:
        judgment = get_active_judgment(submission)
        if judgment is not None and judgment.status == JudgmentStatus.DONE and judgment.final_verdict is not None:
            judged.append((submission, judgment.final_verdict))

    problems_seen: dict[str, tuple[int, str, str, str]] = {}
    for submission, _ in judged:
        problem_id = str(submission.problem_id)
        if problem_id not in problems_seen:
            problem = submission.problem
            problems_seen[problem_id] = (
                problem.ordinal,
                ordinal_to_label(problem.ordinal),
                problem.title,
                (problem.color or "#888888").lstrip("#"),
            )

    sorted_problems = sorted(problems_seen.values(), key=lambda item: item[0])
    problem_infos = [ProblemInfo(label=label, title=title, color=color) for _, label, title, color in sorted_problems]
    pid_to_label = {problem_id: problems_seen[problem_id][1] for problem_id in problems_seen}
    language_infos = [LanguageInfo(id=lang.id, name=lang.name, icon=lang.icon or "") for lang in languages]

    by_problem: defaultdict[str, int] = defaultdict(int)
    ac_by_problem: defaultdict[str, int] = defaultdict(int)
    acpe_by_problem: defaultdict[str, int] = defaultdict(int)
    problem_verdict: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    problem_language: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    language_verdict: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    team_total: defaultdict[str, int] = defaultdict(int)
    team_accepted: defaultdict[str, int] = defaultdict(int)
    team_problem_total: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    team_problem_accepted: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    team_display_map: dict[str, str] = {}
    window_all: defaultdict[int, int] = defaultdict(int)
    window_accepted: defaultdict[int, int] = defaultdict(int)

    total_runs = 0
    total_acpe = 0

    for submission, verdict in judged:
        label = pid_to_label[str(submission.problem_id)]
        language_id = str(submission.language_id)
        total_runs += 1

        by_problem[label] += 1
        problem_verdict[label][verdict.value] += 1
        problem_language[label][language_id] += 1
        language_verdict[language_id][verdict.value] += 1

        accepted = is_accepted(verdict)
        if verdict == Verdict.AC:
            ac_by_problem[label] += 1
        if accepted:
            total_acpe += 1
            acpe_by_problem[label] += 1

        team = submission.team
        team_key = str(submission.team_id)
        if team_key not in team_display_map:
            site_name = team.site.sitename if team and team.site else None
            base = team.fullname or team.username if team else "Unknown"
            username = team.username if team else "unknown"
            team_display_map[team_key] = f"{format_site_identity(site_name, base)} ({username})"
        team_total[team_key] += 1
        team_problem_total[team_key][label] += 1
        if accepted:
            team_accepted[team_key] += 1
            team_problem_accepted[team_key][label] += 1

        timestamp_seconds = submission.timestamp_seconds
        if timestamp_seconds is not None and timestamp_seconds >= 0:
            window_index = timestamp_seconds // 600
            window_all[window_index] += 1
            if accepted:
                window_accepted[window_index] += 1

    problem_summary = [
        ProblemSummaryRow(
            problem=problem,
            total_runs=by_problem[problem.label],
            ac=CellValue(
                count=ac_by_problem[problem.label],
                pct=pct(ac_by_problem[problem.label], by_problem[problem.label]),
            ),
            ac_pe=(
                CellValue(
                    count=acpe_by_problem[problem.label],
                    pct=pct(acpe_by_problem[problem.label], by_problem[problem.label]),
                )
                if accept_pe
                else None
            ),
        )
        for problem in problem_infos
    ]

    runs_distribution = [
        DistributionRow(
            problem=problem,
            count=by_problem[problem.label],
            pct=pct(by_problem[problem.label], total_runs),
        )
        for problem in problem_infos
    ]
    accepted_distribution = [
        DistributionRow(
            problem=problem,
            count=acpe_by_problem[problem.label],
            pct=pct(acpe_by_problem[problem.label], total_acpe),
        )
        for problem in problem_infos
    ]

    problem_verdict_table, problem_verdict_totals = build_problem_verdict_table(
        problem_infos,
        by_problem,
        problem_verdict,
        total_runs,
    )
    problem_language_table, problem_language_totals, language_totals = build_problem_language_table(
        problem_infos,
        language_infos,
        by_problem,
        problem_language,
        total_runs,
    )
    language_verdict_table, language_verdict_totals = build_language_verdict_table(
        language_infos,
        language_verdict,
        language_totals,
        total_runs,
    )
    team_rows = build_team_rows(problem_infos, team_total, team_accepted, team_problem_total, team_display_map)
    time_windows = build_time_windows(contest.duration_minutes, window_all, window_accepted)

    return ContestReport(
        problems=problem_infos,
        languages=language_infos,
        accept_pe=accept_pe,
        total_runs=total_runs,
        total_accepted=total_acpe,
        problem_summary=problem_summary,
        runs_distribution=runs_distribution,
        accepted_distribution=accepted_distribution,
        problem_verdict=problem_verdict_table,
        problem_verdict_totals=problem_verdict_totals,
        problem_language=problem_language_table,
        problem_language_totals=problem_language_totals,
        language_verdict=language_verdict_table,
        language_verdict_totals=language_verdict_totals,
        team_problem=team_rows,
        time_windows=time_windows,
    )
