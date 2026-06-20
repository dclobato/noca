#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from shared.db_schema import clarifications as clarifications_table
from web.database import Base

if TYPE_CHECKING:
    from web.models.problem import Problem
    from web.models.users import User


class Clarification(Base):
    __table__ = clarifications_table

    id: Mapped[str]
    team_id: Mapped[str]
    judge_id: Mapped[str | None]
    problem_id: Mapped[str]
    question: Mapped[str]
    is_contest_public: Mapped[bool]
    answer: Mapped[str | None]
    created_timestamp_seconds: Mapped[int]
    answered_at: Mapped[datetime | None]
    answered_timestamp_seconds: Mapped[int | None]
    hidden: Mapped[bool]
    hidden_by_judge_id: Mapped[str | None]
    hidden_by_admin_id: Mapped[str | None]
    hidden_at: Mapped[datetime | None]
    hidden_timestamp_seconds: Mapped[int | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[Problem] = relationship(
        back_populates="clarifications",
        foreign_keys="[Clarification.problem_id]",
    )
    team: Mapped[User] = relationship(
        back_populates="clarifications_as_team",
        foreign_keys="[Clarification.team_id]",
    )
    hidden_by_judge: Mapped[User | None] = relationship(
        back_populates="hidden_clarifications_as_judge",
        foreign_keys="[Clarification.hidden_by_judge_id]",
    )
    hidden_by_admin: Mapped[User | None] = relationship(
        back_populates="hidden_clarifications_as_admin",
        foreign_keys="[Clarification.hidden_by_admin_id]",
    )
