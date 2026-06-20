#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM models for Arena problem-set rating snapshots.

These map onto the Core tables defined in
``shared.db_schema.arena.arena_problem_set_snapshots``:

  - ArenaProblemSetUserSnapshot: per-user total of AC'd problem ratings for a set.
  - ArenaProblemSetProblemSnapshot: per (user, problem) AC rating captured at
    snapshot time.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Mapped

from arena.database import ArenaBase
from shared.db_schema.arena import (
    arena_problem_set_problem_snapshots as arena_problem_set_problem_snapshots_table,
)
from shared.db_schema.arena import (
    arena_problem_set_user_snapshots as arena_problem_set_user_snapshots_table,
)


class ArenaProblemSetUserSnapshot(ArenaBase):
    """Frozen per-user total for a problem set after its deadline.

    Attributes:
        problem_set_id: FK to arena_problem_sets.
        user_id: FK to arena_users.
        total_rating: Sum of current ratings of the problems the user got AC on.
        snapshot_at: When the snapshot was taken.
    """

    __table__ = arena_problem_set_user_snapshots_table

    problem_set_id: Mapped[str]
    user_id: Mapped[str]
    total_rating: Mapped[int]
    snapshot_at: Mapped[datetime]


class ArenaProblemSetProblemSnapshot(ArenaBase):
    """Frozen per (user, problem) AC rating for a problem set after its deadline.

    Attributes:
        problem_set_id: FK to arena_problem_sets.
        user_id: FK to arena_users.
        problem_id: FK to arena_problems.
        rating: The problem's rating captured at snapshot time.
    """

    __table__ = arena_problem_set_problem_snapshots_table

    problem_set_id: Mapped[str]
    user_id: Mapped[str]
    problem_id: Mapped[str]
    rating: Mapped[int]
