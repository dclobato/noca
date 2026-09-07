#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest report DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ProblemInfo:
    """Lightweight problem descriptor for template rendering."""

    label: str
    title: str
    color: str


@dataclass(slots=True)
class LanguageInfo:
    """Lightweight language descriptor for template rendering."""

    id: str
    name: str
    icon: str


@dataclass(slots=True)
class CellValue:
    """A single count-plus-percentage cell used in cross-tables."""

    count: int
    pct: float


@dataclass(slots=True)
class SolveMetrics:
    """Per-problem submission/solve-time metrics, over solving teams only.

    ``avg_submissions`` and ``median_submissions`` are both taken over the
    per-team submission count on the problem, restricted to teams that
    attempted it at least once (0.0 when nobody attempted it). The other
    fields are ``None`` when the problem has no solve yet: they are
    computed only from each team's first accepted submission, so a team's
    later re-submissions to an already-solved problem never contribute.
    ``first_solver_name`` is the display name (site-prefixed, same formatting
    as the Runs by Team and Problem table) of whichever team's first accepted
    submission landed at ``first_solved_minutes``.

    ``dirt_ratio`` is the ICPC resolver "dirt" metric: the percentage of
    solving teams' submissions to this problem that were wrong, pooled across
    every solver (sum of each solver's wrong attempts, divided by that sum
    plus the solver count) rather than averaged per team. High dirt with a
    healthy solve count means the problem punishes small mistakes; low dirt
    with few solves means it is hard to *think through* rather than to get
    exactly right once you see the approach.
    """

    avg_submissions: float
    median_submissions: float
    median_time_solved: float | None
    avg_time_solved: float | None
    first_solved_minutes: int | None
    first_solver_name: str | None
    dirt_ratio: float | None


@dataclass(slots=True)
class ProblemSummaryRow:
    """Row for the problem summary table."""

    problem: ProblemInfo
    total_runs: int
    ac: CellValue
    ac_pe: CellValue | None
    solve_metrics: SolveMetrics


@dataclass(slots=True)
class DistributionRow:
    """Row for distribution tables."""

    problem: ProblemInfo
    count: int
    pct: float


@dataclass(slots=True)
class TeamRow:
    """Row for the team-by-problem report."""

    team_display: str
    team_username: str
    total_submissions: int
    accepted: CellValue
    cells: dict[str, CellValue] = field(default_factory=dict)


@dataclass(slots=True)
class TimeWindow:
    """One bar in the time-distribution charts."""

    label: str
    all_count: int
    accepted_count: int


@dataclass(slots=True)
class ProblemRaceSeries:
    """One line in the Problem Race chart: cumulative solves over contest time.

    ``solved_minutes`` is every solving team's first-accepted-submission
    minute for this problem, sorted ascending -- one entry per solve, so the
    client derives the cumulative step curve (0, 1, 2, ...) by counting
    entries rather than the server pre-computing a step series. Empty when
    the problem has no solve yet, which renders as a flat zero line.
    """

    problem: ProblemInfo
    solved_minutes: list[int]


@dataclass(slots=True)
class FiveNumberSummary:
    """Min/Q1/median/Q3/max plus mean for an integer distribution.

    Quartiles come from ``statistics.quantiles(..., n=4)`` (linear
    interpolation, the stdlib's default "exclusive" method), so ``q1``,
    ``median``, and ``q3`` can be fractional even though every underlying
    value -- problems solved, or penalty minutes -- is a whole number.
    """

    minimum: int
    q1: float
    median: float
    q3: float
    maximum: int
    mean: float


@dataclass(slots=True)
class SolvedCountBucket:
    """One bar in the "Active Teams by Problems Solved" histogram."""

    solved: int
    team_count: int


@dataclass(slots=True)
class PerformanceSummary:
    """Contest-wide performance distribution across active teams.

    Population is every *active* team (at least one submission, judged or
    not) -- including teams that solved nothing, which contribute 0 to both
    distributions -- so ``active_team_count`` always matches
    ``Highlights.active_teams.active``.

    ``penalty_summary`` uses the real ICPC penalty formula (each solved
    problem's solve-minute plus its penalizing-verdict attempts times the
    contest's WA penalty, respecting ``accept_pe``/``ce_adds_penalty``), not
    the report's separate "dirt" wrongness predicate -- a team that solved
    nothing contributes 0 penalty minutes, same as it contributes 0 solves.

    ``top_10pct_solved`` is the ceiling of the solved-count distribution's
    own 90th percentile -- not a full ICPC-rank threshold, which would pull
    in penalty-time tie-breaking from outside this distribution. Both
    summary fields and the threshold are ``None`` when fewer than two active
    teams exist, since ``statistics.quantiles`` needs at least two points
    and a single-team quartile has no meaning.
    """

    active_team_count: int
    solved_summary: FiveNumberSummary | None
    solved_histogram: list[SolvedCountBucket]
    penalty_summary: FiveNumberSummary | None
    top_10pct_solved: int | None


@dataclass(slots=True)
class ProblemHighlight:
    """Highlight card data for the most/least-solved problem(s).

    ``problems`` holds every problem tied for the extreme solved-team count
    -- one, ordinarily, but all of them when two or more problems tie --
    so a tie is shown honestly instead of picking an arbitrary "winner".
    ``solved_teams``/``pct_of_teams`` describe that shared count once, since
    every listed problem has the identical value by construction.

    ``pct_of_teams`` is out of every *active* team -- one with at least one
    submission in the contest, judged or not -- rather than the full enrolled
    roster. A team that never showed up must not deflate a problem's
    acceptance rate: 80 of 80 active teams solving it is 100%, not 80% just
    because 20 enrolled teams skipped the contest entirely.
    """

    problems: list[ProblemInfo]
    solved_teams: int
    pct_of_teams: float


@dataclass(slots=True)
class LanguageHighlight:
    """Highlight card data for the most-used language."""

    language: LanguageInfo
    count: int
    pct: float


@dataclass(slots=True)
class ActiveTeamsHighlight:
    """Highlight card data for how many enrolled teams actually showed up.

    ``active`` is every team with at least one submission, judged or not --
    the same population that grounds ``ProblemHighlight.pct_of_teams``.
    ``enrolled`` is the full contest roster, so ``pct`` (``active / enrolled``)
    is the one figure on this page that *does* answer "how many of the
    registered teams participated" rather than "how did the participants do".
    """

    active: int
    enrolled: int
    pct: float


@dataclass(slots=True)
class Highlights:
    """Top-of-page highlight cards.

    ``unsolved`` and ``least_solved`` split what used to be one card, because
    "nobody has solved this" and "this was the hardest problem somebody did
    solve" are different findings that read identically when both are called
    "least solved". Mid-contest the difference is stark: most of the set is
    unsolved early on, and a single card would then list half the alphabet
    against a count of zero.

    ``unsolved`` lists every problem with no accepted run, in ordinal order,
    and is empty once the field has cracked them all. ``least_solved``
    therefore ranges over problems with *at least one* solve, and is ``None``
    when no problem has been solved at all -- in which case ``unsolved`` is
    the whole problem set and says everything there is to say.
    """

    most_solved: ProblemHighlight
    least_solved: ProblemHighlight | None
    unsolved: list[ProblemInfo]
    most_used_language: LanguageHighlight | None
    global_acceptance_pct: float
    active_teams: ActiveTeamsHighlight


@dataclass(slots=True)
class ContestReport:
    """All aggregated data for the reports page."""

    problems: list[ProblemInfo]
    languages: list[LanguageInfo]
    accept_pe: bool
    total_runs: int
    total_accepted: int
    problem_summary: list[ProblemSummaryRow]
    runs_distribution: list[DistributionRow]
    accepted_distribution: list[DistributionRow]
    problem_verdict: dict[str, dict[str, CellValue]]
    problem_verdict_totals: dict[str, CellValue]
    problem_language: dict[str, dict[str, CellValue]]
    problem_language_totals: dict[str, CellValue]
    language_verdict: dict[str, dict[str, CellValue]]
    language_verdict_totals: dict[str, CellValue]
    team_problem: list[TeamRow]
    time_windows: list[TimeWindow]
    time_window_minutes: int
    highlights: Highlights
    problem_race: list[ProblemRaceSeries]
    performance: PerformanceSummary
