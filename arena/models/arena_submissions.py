#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM models for Arena submissions, judgments, test results, and user progress.

These models map onto the Core tables defined in
shared.db_schema.arena.arena_submissions:

  - ArenaSubmission: one row per submission attempt.
  - ArenaSubmissionAIReview: AI review result for a submission (1:1, sparse).
  - ArenaSubmissionTeacherFeedback: teacher feedback on a submission (1:1, sparse).
  - ArenaSubmissionJudgment: autojudge-only judgment for a submission.
  - ArenaSubmissionTestResult: first non-AC test case per judgment (at most one row).
  - ArenaUserSolvedProblem: first AC per (user, problem) pair; source of truth for solved problems.
  - ArenaUserTriedProblem: problems a user has attempted; updated on each new submission.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from shared.db_schema.arena import arena_problem_solvers as arena_problem_solvers_table
from shared.db_schema.arena import arena_problem_tried as arena_problem_tried_table
from shared.db_schema.arena import arena_submission_ai_reviews as arena_submission_ai_reviews_table
from shared.db_schema.arena import arena_submission_judgments as arena_submission_judgments_table
from shared.db_schema.arena import (
    arena_submission_teacher_feedback as arena_submission_teacher_feedback_table,
)
from shared.db_schema.arena import arena_submission_test_results as arena_submission_test_results_table
from shared.db_schema.arena import arena_submissions as arena_submissions_table

if TYPE_CHECKING:
    from arena.models.arena_problems import ArenaProblem, ArenaTestCase
    from arena.models.arena_users import ArenaUser


class ArenaSubmission(ArenaBase):
    """ORM model for a single arena submission attempt.

    Maps onto the ``arena_submissions`` Core table. Each row records what the
    user submitted (source code, language) and links to one optional
    ``ArenaSubmissionAIReview`` and one or more ``ArenaSubmissionJudgment`` records.

    Attributes:
        id: UUID string primary key.
        user_id: FK to arena_users.
        problem_id: FK to arena_problems.
        language_id: FK to languages (e.g. 'gcc-c17').
        source_code: Full submitted source code.
        source_hash: SHA-256 hex digest for fast lookups.
        source_size_bytes: Source length in bytes.
        problem_set_id: When set, ties the submission to a problem set (teacher-visible);
            NULL means the submission is private to the submitter.
        submit_to_ai: When True, submission should be forwarded to the AI provider.
        created_at: Record creation timestamp.
        updated_at: Record last-update timestamp.
        user: Back-reference to the submitting ArenaUser.
        problem: Back-reference to the submitted ArenaProblem.
        ai_review: Optional 1:1 AI review result (None until reviewed).
        teacher_feedback: Optional 1:1 teacher feedback (None until a teacher comments).
        judgments: Ordered list of judgment attempts (cascade delete).
    """

    __table__ = arena_submissions_table

    id: Mapped[str]
    user_id: Mapped[str]
    problem_id: Mapped[str]
    language_id: Mapped[str]
    source_code: Mapped[str]
    source_hash: Mapped[str]
    source_size_bytes: Mapped[int]
    problem_set_id: Mapped[str | None]
    submit_to_ai: Mapped[bool]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        back_populates="submissions",
        foreign_keys=[arena_submissions_table.c.user_id],
    )
    problem: Mapped[ArenaProblem] = relationship(
        "ArenaProblem",
        back_populates="submissions",
        foreign_keys=[arena_submissions_table.c.problem_id],
    )
    ai_review: Mapped[ArenaSubmissionAIReview | None] = relationship(
        "ArenaSubmissionAIReview",
        back_populates="submission",
        cascade="all, delete-orphan",
        uselist=False,
        foreign_keys=[arena_submission_ai_reviews_table.c.submission_id],
    )
    teacher_feedback: Mapped[ArenaSubmissionTeacherFeedback | None] = relationship(
        "ArenaSubmissionTeacherFeedback",
        back_populates="submission",
        cascade="all, delete-orphan",
        uselist=False,
        foreign_keys=[arena_submission_teacher_feedback_table.c.submission_id],
    )
    judgments: Mapped[list[ArenaSubmissionJudgment]] = relationship(
        "ArenaSubmissionJudgment",
        back_populates="submission",
        cascade="all, delete-orphan",
        foreign_keys=[arena_submission_judgments_table.c.submission_id],
    )


class ArenaSubmissionAIReview(ArenaBase):
    """AI review result for a single arena submission.

    Maps onto the ``arena_submission_ai_reviews`` Core table. At most one row
    exists per submission (submission_id is the primary key). The row is created
    by the ``aiassistant`` worker after the OpenAI Responses API call completes.

    Attributes:
        submission_id: PK + FK to arena_submissions (CASCADE).
        ai_response: Response text from the AI provider.
        ai_response_at: Timestamp when the response was stored.
        _ai_review_cost: Raw integer micros (access via ai_review_cost property).
        submission: Back-reference to the owning ArenaSubmission.
    """

    __table__ = arena_submission_ai_reviews_table

    _COST_SCALE = 1_000_000

    submission_id: Mapped[str]
    ai_response: Mapped[str]
    ai_response_at: Mapped[datetime]
    _ai_review_cost: Mapped[int | None]
    used_platform_key: Mapped[bool]

    @property
    def ai_review_cost(self) -> float | None:
        """Return the AI review cost as a float (e.g. 0.001234).

        Returns:
            float | None: Cost in provider units, or None if not set.
        """
        if self._ai_review_cost is None:
            return None
        return self._ai_review_cost / self._COST_SCALE

    @ai_review_cost.setter
    def ai_review_cost(self, value: float | None) -> None:
        """Store the AI review cost as integer micros (value * 1_000_000).

        Args:
            value: Cost in provider units as a float, or None to clear.
        """
        if value is None:
            self._ai_review_cost = None
        else:
            self._ai_review_cost = round(value * self._COST_SCALE)

    submission: Mapped[ArenaSubmission] = relationship(
        "ArenaSubmission",
        back_populates="ai_review",
        foreign_keys=[arena_submission_ai_reviews_table.c.submission_id],
    )


class ArenaSubmissionTeacherFeedback(ArenaBase):
    """Teacher feedback on a single arena submission.

    Maps onto the ``arena_submission_teacher_feedback`` Core table. At most one
    row exists per submission (submission_id is the primary key). The row is
    created or updated by the Arena HTTP process when a teacher (or admin)
    comments on a student's non-AC, set-tied submission.

    Attributes:
        submission_id: PK + FK to arena_submissions (CASCADE).
        teacher_id: FK to arena_users for the author (SET NULL on author removal).
        feedback_text: Free-text feedback the teacher left.
        feedback_at: Timestamp the feedback was last written (refreshed on edit).
        submission: Back-reference to the owning ArenaSubmission.
    """

    __table__ = arena_submission_teacher_feedback_table

    submission_id: Mapped[str]
    teacher_id: Mapped[str | None]
    feedback_text: Mapped[str]
    feedback_at: Mapped[datetime]

    submission: Mapped[ArenaSubmission] = relationship(
        "ArenaSubmission",
        back_populates="teacher_feedback",
        foreign_keys=[arena_submission_teacher_feedback_table.c.submission_id],
    )


class ArenaSubmissionJudgment(ArenaBase):
    """ORM model for an autojudge-only judgment of an arena submission.

    Maps onto the ``arena_submission_judgments`` Core table. Arena uses
    autojudge-only verdict flow: ``final_verdict`` is set directly from
    ``autojudge_verdict`` by the verdict handler. There is no human
    confirmation step, no verdict override, and no judgment audit table.

    Attributes:
        id: UUID string primary key.
        submission_id: FK to arena_submissions (CASCADE).
        status: JudgmentStatus string (QUEUED → DISPATCHED → JUDGING → DONE | FAILED | SUPERSEDED).
        autojudge_verdict: Verdict string set by the autojudge worker.
        final_verdict: Equals autojudge_verdict; no human step.
        compile_log: Compiler output (nullable).
        max_wall_time_ms: Worst wall-clock time across all test cases.
        max_memory_kb: Peak memory usage across all test cases.
        error_message: Internal judge error message (nullable).
        worker_id: Identifying string of the worker that processed the job.
        started_at: Timestamp when the worker began processing.
        finished_at: Timestamp when the worker finished.
        created_at: Record creation timestamp.
        submission: Back-reference to the owning ArenaSubmission.
        test_result: Optional first non-AC test result (uselist=False, cascade).
    """

    __table__ = arena_submission_judgments_table

    id: Mapped[str]
    submission_id: Mapped[str]
    status: Mapped[str]
    autojudge_verdict: Mapped[str | None]
    final_verdict: Mapped[str | None]
    compile_log: Mapped[str | None]
    max_wall_time_ms: Mapped[int | None]
    max_memory_kb: Mapped[int | None]
    error_message: Mapped[str | None]
    worker_id: Mapped[str | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    created_at: Mapped[datetime]

    submission: Mapped[ArenaSubmission] = relationship(
        "ArenaSubmission",
        back_populates="judgments",
        foreign_keys=[arena_submission_judgments_table.c.submission_id],
    )
    test_result: Mapped[ArenaSubmissionTestResult | None] = relationship(
        "ArenaSubmissionTestResult",
        back_populates="judgment",
        cascade="all, delete-orphan",
        uselist=False,
        foreign_keys=[arena_submission_test_results_table.c.judgment_id],
    )


class ArenaSubmissionTestResult(ArenaBase):
    """First non-AC test case result for an arena judgment.

    Maps onto the ``arena_submission_test_results`` Core table. At most one
    row exists per judgment (enforced by a UNIQUE constraint on judgment_id).
    The verdict handler writes this row for the first test case that is not AC,
    giving the user actionable feedback without revealing all secret cases.

    Attributes:
        id: UUID string primary key.
        judgment_id: FK to arena_submission_judgments (CASCADE, UNIQUE).
        test_case_id: FK to arena_test_cases (RESTRICT).
        verdict: Verdict string for this test case.
        wall_time_ms: Wall-clock execution time in milliseconds.
        memory_kb: Peak memory usage in kilobytes.
        exit_code: Process exit code.
        stdout_excerpt: Truncated standard output.
        stderr_excerpt: Truncated standard error.
        created_at: Record creation timestamp.
        judgment: Back-reference to the owning ArenaSubmissionJudgment.
        test_case: Back-reference to the ArenaTestCase that was run.
    """

    __table__ = arena_submission_test_results_table

    id: Mapped[str]
    judgment_id: Mapped[str]
    test_case_id: Mapped[str]
    verdict: Mapped[str]
    wall_time_ms: Mapped[int | None]
    memory_kb: Mapped[int | None]
    exit_code: Mapped[int | None]
    stdout_excerpt: Mapped[str | None]
    stderr_excerpt: Mapped[str | None]
    created_at: Mapped[datetime]

    judgment: Mapped[ArenaSubmissionJudgment] = relationship(
        "ArenaSubmissionJudgment",
        back_populates="test_result",
        foreign_keys=[arena_submission_test_results_table.c.judgment_id],
    )
    test_case: Mapped[ArenaTestCase] = relationship(
        "ArenaTestCase",
        foreign_keys=[arena_submission_test_results_table.c.test_case_id],
    )


class ArenaUserSolvedProblem(ArenaBase):
    """Records the first accepted submission for a (user, problem) pair.

    Maps onto the ``arena_problem_solvers`` Core table. This is the source of
    truth for the solved-problems count used in rankings. The service layer
    inserts with ``ON CONFLICT DO NOTHING`` so only the first AC is recorded.

    Attributes:
        problem_id: Composite PK + FK to arena_problems (CASCADE).
        user_id: Composite PK + FK to arena_users (CASCADE).
        solved_at: Timestamp of the first accepted submission.
        user: Back-reference to the ArenaUser.
        problem: Back-reference to the ArenaProblem.
    """

    __table__ = arena_problem_solvers_table

    problem_id: Mapped[str]
    user_id: Mapped[str]
    solved_at: Mapped[datetime]

    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        back_populates="solved_problems_records",
        foreign_keys=[arena_problem_solvers_table.c.user_id],
    )
    problem: Mapped[ArenaProblem] = relationship(
        "ArenaProblem",
        back_populates="solver_links",
        foreign_keys=[arena_problem_solvers_table.c.problem_id],
    )


class ArenaUserTriedProblem(ArenaBase):
    """Records problems a user has submitted to without achieving a first AC.

    Maps onto the ``arena_problem_tried`` Core table. One row per (user, problem)
    pair; ``last_tried_at`` is updated on each new submission via
    ``ON CONFLICT DO UPDATE`` at the service layer. A user "failed to solve" a
    problem when they appear in this table but NOT in ``arena_problem_solvers``.

    Attributes:
        problem_id: Composite PK + FK to arena_problems (CASCADE).
        user_id: Composite PK + FK to arena_users (CASCADE).
        last_tried_at: Timestamp of the most recent submission for this pair.
        user: Back-reference to the ArenaUser.
        problem: Back-reference to the ArenaProblem.
    """

    __table__ = arena_problem_tried_table

    problem_id: Mapped[str]
    user_id: Mapped[str]
    last_tried_at: Mapped[datetime]

    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        back_populates="tried_problems_records",
        foreign_keys=[arena_problem_tried_table.c.user_id],
    )
    problem: Mapped[ArenaProblem] = relationship(
        "ArenaProblem",
        back_populates="tried_links",
        foreign_keys=[arena_problem_tried_table.c.problem_id],
    )
