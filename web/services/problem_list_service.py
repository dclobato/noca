#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem card data for the participant-facing contest problem list.

Combines contest problems with scoreboard-derived statistics: a solving rate
(the share of registered teams with an Accepted verdict) and, for a team
viewer, that team's own status on each problem. Both figures ride on the
existing ``ScoreboardSnapshot`` rather than a second query path, so they
automatically respect the same freeze visibility rules as the scoreboard
itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shared.enumerations import RoleEnum
from shared.services.scoreboard_projection import ScoreboardSnapshot, ordinal_to_label
from web.models.problem import Problem
from web.models.users import UberAdmin, User

ViewerStatus = Literal["solved", "pending", "attempted", "untried"]


@dataclass
class ProblemCardData:
    """One card's worth of data for the contest problem list.

    Attributes:
        problem: The underlying problem record.
        label: Display label derived from the problem's ordinal.
        solving_rate: Percentage of registered teams with an Accepted verdict
            on this problem, rounded to the nearest integer (0 when the
            contest has no teams yet).
        viewer_status: The current team's own status on this problem, or
            ``None`` for a viewer with no personal standing (staff, judge,
            admin, uberadmin).
    """

    problem: Problem
    label: str
    solving_rate: int
    viewer_status: ViewerStatus | None


def build_problem_cards(
    problems: list[Problem],
    snapshot: ScoreboardSnapshot,
    actor: UberAdmin | User,
) -> list[ProblemCardData]:
    """Combine contest problems with scoreboard-derived per-problem stats.

    Args:
        problems: Contest problems in display order.
        snapshot: Scoreboard snapshot visible to the requesting viewer.
        actor: The requesting user, used to look up their own standing.

    Returns:
        One ``ProblemCardData`` per problem, in the same order as ``problems``.
    """
    total_teams = len(snapshot.standings)
    own_standing = None
    if isinstance(actor, User) and actor.role == RoleEnum.TEAM:
        own_standing = next((s for s in snapshot.standings if s.team_id == str(actor.id)), None)

    rows: list[ProblemCardData] = []
    for problem in problems:
        label = ordinal_to_label(problem.ordinal)
        solved_teams = sum(
            1 for standing in snapshot.standings if (result := standing.problems.get(label)) and result.solved
        )
        solving_rate = round(solved_teams / total_teams * 100) if total_teams else 0

        viewer_status: ViewerStatus | None = None
        if own_standing is not None:
            result = own_standing.problems.get(label)
            if result is not None:
                if result.solved:
                    viewer_status = "solved"
                elif result.is_pending:
                    viewer_status = "pending"
                elif result.attempts > 0:
                    viewer_status = "attempted"
                else:
                    viewer_status = "untried"

        rows.append(
            ProblemCardData(
                problem=problem,
                label=label,
                solving_rate=solving_rate,
                viewer_status=viewer_status,
            )
        )
    return rows
