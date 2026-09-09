#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for teacher feedback on an Arena problem set."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, Text

from .._base import metadata

arena_problem_set_student_feedback = Table(
    "arena_problem_set_student_feedback",
    metadata,
    Column(
        "problem_set_id",
        String(36),
        ForeignKey("arena_problem_sets.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Problem set that the feedback covers.",
    ),
    Column(
        "student_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Student who receives the feedback.",
    ),
    Column(
        "teacher_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Teacher or admin who most recently wrote the feedback.",
    ),
    Column(
        "feedback_text",
        Text,
        nullable=False,
        comment="Markdown feedback for the student's work across the problem set.",
    ),
    Column(
        "feedback_at",
        DateTime(timezone=True),
        nullable=False,
        comment="When the feedback was last saved.",
    ),
)
