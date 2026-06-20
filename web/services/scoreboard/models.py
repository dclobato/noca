#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Scoreboard data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProblemResult:
    """Scoreboard data for one team x problem cell."""

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
    """Scoreboard row for one team."""

    rank: int
    team_id: str
    team_name: str
    team_fullname: str
    problems_solved: int
    total_time: int
    problems: dict[str, ProblemResult]


@dataclass
class ScoreboardSnapshot:
    """Full scoreboard state at a point in time."""

    contest_id: str
    generated_at: str
    is_frozen: bool
    standings: list[TeamStanding]
    problems: list[str]
    balloon_colors: list[str]
