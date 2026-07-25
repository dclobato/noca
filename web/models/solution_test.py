#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM models for non-scoring solution-test runs."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Mapped, relationship

from shared.db_schema import solution_test_case_results as solution_test_case_results_table
from shared.db_schema import solution_test_runs as solution_test_runs_table
from shared.enumerations import JudgmentStatus, Verdict
from web.database import Base

if TYPE_CHECKING:
    from web.models.language import Language
    from web.models.problem import Problem
    from web.models.users import UberAdmin, User

_TERMINAL_STATUSES = {JudgmentStatus.DONE, JudgmentStatus.FAILED}


class SolutionTestRun(Base):
    """One staff-triggered, non-scoring run of a candidate solution."""

    __table__ = solution_test_runs_table

    id: Mapped[str]
    problem_id: Mapped[str]
    language_id: Mapped[str]
    source_code: Mapped[str]
    source_hash: Mapped[str]
    source_size_bytes: Mapped[int]
    status: Mapped[JudgmentStatus]
    verdict: Mapped[Verdict | None]
    compile_log: Mapped[str | None]
    error_message: Mapped[str | None]
    max_wall_time_ms: Mapped[int | None]
    max_memory_kb: Mapped[int | None]
    worker_id: Mapped[str | None]
    attempt_token: Mapped[str | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    triggered_by_user_id: Mapped[str | None]
    triggered_by_uberadmin_id: Mapped[str | None]
    triggered_by_label: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[Problem] = relationship(foreign_keys=[solution_test_runs_table.c.problem_id])
    language: Mapped[Language] = relationship(foreign_keys=[solution_test_runs_table.c.language_id])
    triggered_by_user: Mapped[User | None] = relationship(
        foreign_keys=[solution_test_runs_table.c.triggered_by_user_id]
    )
    triggered_by_uberadmin: Mapped[UberAdmin | None] = relationship(
        foreign_keys=[solution_test_runs_table.c.triggered_by_uberadmin_id]
    )
    case_results: Mapped[list[SolutionTestCaseResult]] = relationship(
        back_populates="solution_test_run",
        cascade="all, delete-orphan",
        order_by="(SolutionTestCaseResult.ordinal, SolutionTestCaseResult.attempt_number)",
    )

    @property
    def is_terminal(self) -> bool:
        """Whether the run has finished; drives the self-terminating HTMX poll."""
        return self.status in _TERMINAL_STATUSES

    @property
    def actor_label(self) -> str:
        """Return the display name of the actor who triggered this run.

        Falls back to the label snapshotted at creation, so attribution survives
        the deletion of the triggering account (both actor FKs are ``SET NULL``).
        """
        for actor in (self.triggered_by_user, self.triggered_by_uberadmin):
            username = getattr(actor, "username", None)
            if username:
                return str(username)
        return self.triggered_by_label


class SolutionTestCaseResult(Base):
    """One executed test case of a solution-test run.

    Ordinary rows have ``attempt_number IS NULL``; interactive (custom-validator)
    rows carry the attempt number and the recorded transcript instead.
    """

    __table__ = solution_test_case_results_table

    id: Mapped[str]
    solution_test_run_id: Mapped[str]
    test_case_id: Mapped[str | None]
    ordinal: Mapped[int]
    attempt_number: Mapped[int | None]
    verdict: Mapped[Verdict]
    wall_time_ms: Mapped[int | None]
    memory_kb: Mapped[int | None]
    output_bytes: Mapped[int | None]
    exit_code: Mapped[int | None]
    exit_signal: Mapped[int | None]
    input_excerpt: Mapped[str | None]
    expected_output_excerpt: Mapped[str | None]
    stdout_excerpt: Mapped[str | None]
    stderr_excerpt: Mapped[str | None]
    transcript: Mapped[Any | None]
    created_at: Mapped[datetime]

    solution_test_run: Mapped[SolutionTestRun] = relationship(back_populates="case_results")

    @property
    def test_case_deleted(self) -> bool:
        """Whether the executed test case has since been removed from the problem."""
        return self.test_case_id is None
