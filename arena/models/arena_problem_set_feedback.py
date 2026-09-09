#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for one student's teacher feedback on an Arena problem set."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Mapped

from arena.database import ArenaBase
from shared.db_schema.arena import arena_problem_set_student_feedback as feedback_table


class ArenaProblemSetStudentFeedback(ArenaBase):
    """The current Markdown feedback for a student in one problem set."""

    __table__ = feedback_table

    problem_set_id: Mapped[str]
    student_id: Mapped[str]
    teacher_id: Mapped[str | None]
    feedback_text: Mapped[str]
    feedback_at: Mapped[datetime]
