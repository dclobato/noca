#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Standalone ICPC scoreboard test harness.

Reads a submission log file and runs it through the production
``ScoreboardService._compute_icpc`` function so the script exercises
the real scoring logic instead of a reimplementation.

Input format:
    <Team> <Problem> <Time> <Verdict>
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from shared.enumerations import JudgmentStatus, Verdict
from web.services.scoreboard import ProblemResult, ScoreboardService, TeamStanding

VALID_VERDICTS = {"AC", "WA", "TLE", "MLE", "OLE", "RE", "CE", "PE"}


def parse_file(path: Path) -> tuple[list[SimpleNamespace], list[str]]:
    """Parse the submission log and return records plus sorted problem labels."""
    records: list[SimpleNamespace] = []
    problems_seen: set[str] = set()
    errors: list[str] = []

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if len(parts) != 4:
            errors.append(f"Line {lineno}: expected 4 fields, got {len(parts)}: {raw!r}")
            continue

        team, problem, time_str, verdict = parts
        problem = problem.upper()
        verdict = verdict.upper()

        if not time_str.isdigit():
            errors.append(f"Line {lineno}: time must be a non-negative integer, got {time_str!r}")
            continue

        if verdict not in VALID_VERDICTS:
            errors.append(f"Line {lineno}: unknown verdict {verdict!r}, valid: {sorted(VALID_VERDICTS)}")
            continue

        problems_seen.add(problem)
        records.append(SimpleNamespace(team=team, problem=problem, time=int(time_str), verdict=verdict))

    if errors:
        print("Parse errors:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        raise SystemExit(1)

    return records, sorted(problems_seen)


def _build_contest(wa_penalty: int, accept_pe: bool, ce_adds_penalty: bool) -> SimpleNamespace:
    """Build a minimal contest object for `_compute_icpc`."""
    return SimpleNamespace(
        wa_penalty=wa_penalty,
        accept_pe=accept_pe,
        ce_adds_penalty=ce_adds_penalty,
    )


def _label_to_ordinal(label: str) -> int:
    """Convert a problem label such as A or AA to the ordinal expected by `_compute_icpc`."""
    ordinal = 0
    for char in label:
        ordinal = ordinal * 26 + (ord(char) - ord("A") + 1)
    return ordinal


def _build_stubs(
    records: list[SimpleNamespace],
    problem_labels: list[str],
) -> tuple[list[SimpleNamespace], list[SimpleNamespace], list[SimpleNamespace], dict[str, SimpleNamespace | None]]:
    """Convert parsed records into the attribute shape expected by `_compute_icpc`."""
    team_names = sorted({record.team for record in records})
    team_id_map = {name: str(uuid.uuid4()) for name in team_names}
    problem_id_map = {label: str(uuid.uuid4()) for label in problem_labels}

    teams = [
        SimpleNamespace(
            id=team_id_map[name],
            username=name,
            fullname=name,
        )
        for name in team_names
    ]

    problems = [
        SimpleNamespace(
            id=problem_id_map[label],
            ordinal=_label_to_ordinal(label),
        )
        for label in problem_labels
    ]

    submissions: list[SimpleNamespace] = []
    judgments: dict[str, SimpleNamespace | None] = {}

    for record in sorted(records, key=lambda item: (item.team, item.problem, item.time)):
        submission_id = str(uuid.uuid4())
        submissions.append(
            SimpleNamespace(
                id=submission_id,
                team_id=team_id_map[record.team],
                problem_id=problem_id_map[record.problem],
                timestamp_seconds=record.time * 60,
            )
        )
        judgments[submission_id] = SimpleNamespace(
            final_verdict=Verdict(record.verdict),
            status=JudgmentStatus.DONE,
        )

    return teams, problems, submissions, judgments


def _cell(result: ProblemResult) -> str:
    """Format a single problem result."""
    if result.solved and result.solved_at_minutes is not None:
        return f"{result.attempts + 1}/{result.solved_at_minutes}"
    if result.attempts > 0:
        return f"({result.attempts})"
    return "-"


def render_scoreboard(
    standings: list[TeamStanding],
    problems: list[str],
    wa_penalty: int,
    accept_pe: bool,
    ce_adds_penalty: bool,
) -> str:
    """Render a compact text scoreboard."""
    rank_w = 4
    team_w = max(10, max((len(standing.team_name) for standing in standings), default=10) + 2)
    prob_w = max(12, max((len(problem) for problem in problems), default=1) + 8)
    total_w = 16
    divider_len = rank_w + 2 + team_w + 2 + len(problems) * prob_w + 2 + total_w

    lines = [
        f"WA penalty: {wa_penalty} min | Accept PE: {accept_pe} | CE adds penalty: {ce_adds_penalty}",
        "",
        f"{'Rank'.center(rank_w)}  {'Team'.center(team_w)}  "
        f"{''.join(problem.center(prob_w) for problem in problems)}  "
        f"{'Total'.center(total_w)}",
        f"{' ' * rank_w}  {' ' * team_w}  "
        f"{''.join('att/time'.center(prob_w) for _ in problems)}  "
        f"{'AC (time)'.center(total_w)}",
        "-" * divider_len,
    ]

    previous_rank: int | None = None
    for standing in standings:
        if previous_rank is not None and standing.rank != previous_rank:
            lines.append("-" * divider_len)
        previous_rank = standing.rank

        problem_cells = "".join(
            _cell(
                standing.problems.get(
                    problem,
                    ProblemResult(
                        label=problem,
                        problem_id=problem,
                        solved=False,
                        attempts=0,
                        solved_at_minutes=None,
                        penalty=0,
                        is_pending=False,
                    ),
                )
            ).center(prob_w)
            for problem in problems
        )

        lines.append(
            f"{str(standing.rank).center(rank_w)}  "
            f"{standing.team_name.ljust(team_w)}  "
            f"{problem_cells}  "
            f"{f'{standing.problems_solved} ({standing.total_time})'.center(total_w)}"
        )

    lines.extend(
        [
            "-" * divider_len,
            "",
            "att/time = total attempts including AC / time of AC",
            "(n)      = n failed attempts, no AC",
            "-        = no submissions",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """Parse arguments, invoke `_compute_icpc`, and print the scoreboard."""
    parser = argparse.ArgumentParser(
        description="ICPC scoreboard test harness using ScoreboardService._compute_icpc.",
    )
    parser.add_argument("file", type=Path, help="Submission log file (Team Problem Time Verdict)")
    parser.add_argument("--wa-penalty", type=int, default=20, metavar="MIN")
    parser.add_argument("--accept-pe", action="store_true")
    parser.add_argument("--ce-adds-penalty", action="store_true")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        raise SystemExit(1)

    records, problem_labels = parse_file(args.file)
    if not records:
        print("No submissions found in file.", file=sys.stderr)
        raise SystemExit(1)

    contest = _build_contest(args.wa_penalty, args.accept_pe, args.ce_adds_penalty)
    teams, problems, submissions, judgments = _build_stubs(records, problem_labels)
    standings = ScoreboardService()._compute_icpc(  # noqa: SLF001
        contest=contest,
        teams=teams,
        problems=problems,
        submissions=submissions,
        judgments=judgments,
        freeze_at_minutes=0,
        viewer_sees_frozen=False,
    )

    print(
        render_scoreboard(
            standings=standings,
            problems=problem_labels,
            wa_penalty=args.wa_penalty,
            accept_pe=args.accept_pe,
            ce_adds_penalty=args.ce_adds_penalty,
        )
    )


if __name__ == "__main__":
    main()
