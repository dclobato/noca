#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for Arena problem sets.

A problem set belongs to exactly one class and is created only by the assigned
teacher. It carries an optional ``starts_on``/``deadline`` window and owns a
many-to-many set of problems through the ``arena_problem_set_problems`` junction.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from shared.db_schema.arena import arena_problem_set_problems as arena_problem_set_problems_table
from shared.db_schema.arena import arena_problem_sets as arena_problem_sets_table

if TYPE_CHECKING:
    from arena.models.arena_classes import ArenaClass
    from arena.models.arena_problems import ArenaProblem


class ArenaProblemSet(ArenaBase):
    """A problem set owned by a class.

    Maps onto the ``arena_problem_sets`` Core table.

    Attributes:
        id: UUID string primary key.
        class_id: FK to arena_classes (CASCADE).
        name: Display name of the problem set.
        description: Teacher-facing notes/description for the problem set.
        starts_on: Startline; submissions are accepted from this moment (date/time).
        deadline: Deadline; submissions are accepted up to this moment (date/time).
        created_at: Record creation timestamp.
        updated_at: Record last-update timestamp.
        arena_class: Back-reference to the owning ArenaClass.
        problems: Problems belonging to the set (via arena_problem_set_problems).
    """

    __table__ = arena_problem_sets_table

    id: Mapped[str]
    class_id: Mapped[str]
    name: Mapped[str]
    description: Mapped[str | None]
    starts_on: Mapped[datetime | None]
    deadline: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    arena_class: Mapped[ArenaClass] = relationship(
        "ArenaClass",
        back_populates="problem_sets",
        foreign_keys=[arena_problem_sets_table.c.class_id],
    )
    problems: Mapped[list[ArenaProblem]] = relationship(
        "ArenaProblem",
        secondary=arena_problem_set_problems_table,
    )
