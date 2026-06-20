#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import event, exists, inspect, select
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, Session, relationship
from sqlalchemy.sql.elements import ColumnElement

from shared.db_schema import problem_categories as problem_categories_table
from shared.db_schema import problem_categories_map as problem_categories_map
from shared.db_schema import problem_language_limits as problem_language_limits_table
from shared.db_schema import problem_limit_change_batch_languages as problem_limit_change_batch_languages_table
from shared.db_schema import problem_limit_change_batch_submissions as problem_limit_change_batch_submissions_table
from shared.db_schema import problem_limit_change_batches as problem_limit_change_batches_table
from shared.db_schema import problems as problems_table
from shared.db_schema import profiling_case_results as profiling_case_results_table
from shared.db_schema import profiling_runs as profiling_runs_table
from shared.db_schema import test_cases as test_cases_table
from shared.enumerations import ProfilingStatus, Verdict
from web.database import Base

if TYPE_CHECKING:
    from web.models.clarification import Clarification
    from web.models.contest import Contest, Task
    from web.models.language import Language
    from web.models.submission import Submission, SubmissionJudgment
    from web.models.users import UberAdmin, User


class Problem(Base):
    __table__ = problems_table

    id: Mapped[str]
    contest_id: Mapped[str]
    title: Mapped[str]
    time_limit_ms: Mapped[int]
    memory_limit_kb: Mapped[int]
    pids_limit: Mapped[int]
    output_limit_in_bytes: Mapped[int | None]
    author: Mapped[str | None]
    notes: Mapped[str | None]
    color: Mapped[str]
    ordinal: Mapped[int]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    contest: Mapped[Contest] = relationship(back_populates="problems")
    test_cases: Mapped[list[ProblemTestCase]] = relationship(
        back_populates="problem",
        order_by="ProblemTestCase.ordinal",
        cascade="all, delete-orphan",
    )
    language_limits: Mapped[list[ProblemLanguageLimit]] = relationship(
        back_populates="problem",
        cascade="all, delete-orphan",
    )
    profiling_runs: Mapped[list[ProfilingRun]] = relationship(
        back_populates="problem",
        cascade="all, delete-orphan",
        order_by=lambda: ProfilingRun.created_at.desc(),
    )
    categories: Mapped[list[ProblemCategory]] = relationship(
        secondary=problem_categories_map,
        back_populates="problems",
    )
    clarifications: Mapped[list[Clarification]] = relationship(
        back_populates="problem",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="problem",
        cascade="all, delete-orphan",
    )
    limit_change_batches: Mapped[list[ProblemLimitChangeBatch]] = relationship(
        back_populates="problem",
        cascade="all, delete-orphan",
        order_by=lambda: ProblemLimitChangeBatch.created_at.desc(),
    )

    @hybrid_property
    def usable(self) -> bool:
        return len(self.test_cases) > 0

    @usable.expression
    def _usable_expression(cls: type[Problem]) -> ColumnElement[bool]:
        return exists(select(1).where(ProblemTestCase.problem_id == cls.id))


class ProblemTestCase(Base):
    __table__ = test_cases_table

    id: Mapped[str]
    problem_id: Mapped[str]
    ordinal: Mapped[int]
    is_sample: Mapped[bool]
    input_size_bytes: Mapped[int | None]
    output_size_bytes: Mapped[int | None]
    explanation: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[Problem] = relationship(back_populates="test_cases")


class ProblemLanguageLimit(Base):
    __table__ = problem_language_limits_table

    problem_id: Mapped[str]
    language_id: Mapped[str]
    time_limit_ms: Mapped[int]
    memory_limit_kb: Mapped[int]
    pids_limit: Mapped[int]
    output_limit_in_bytes: Mapped[int | None]
    repetitions: Mapped[int]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[Problem] = relationship(back_populates="language_limits")
    language: Mapped[Language] = relationship()


class ProblemLimitChangeBatch(Base):
    __table__ = problem_limit_change_batches_table

    id: Mapped[str]
    contest_id: Mapped[str]
    problem_id: Mapped[str]
    triggered_by_user_id: Mapped[str | None]
    triggered_by_uberadmin_id: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    contest: Mapped[Contest] = relationship()
    problem: Mapped[Problem] = relationship(back_populates="limit_change_batches")
    triggered_by_user: Mapped[User | None] = relationship(
        foreign_keys=[problem_limit_change_batches_table.c.triggered_by_user_id]
    )
    triggered_by_uberadmin: Mapped[UberAdmin | None] = relationship(
        foreign_keys=[problem_limit_change_batches_table.c.triggered_by_uberadmin_id]
    )
    languages: Mapped[list[ProblemLimitChangeBatchLanguage]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="ProblemLimitChangeBatchLanguage.language_id",
    )
    submissions: Mapped[list[ProblemLimitChangeBatchSubmission]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="ProblemLimitChangeBatchSubmission.created_at",
    )


class ProblemLimitChangeBatchLanguage(Base):
    __table__ = problem_limit_change_batch_languages_table

    batch_id: Mapped[str]
    language_id: Mapped[str]
    change_kind: Mapped[str]
    before_limits: Mapped[dict[str, int | None]]
    after_limits: Mapped[dict[str, int | None]]
    created_at: Mapped[datetime]

    batch: Mapped[ProblemLimitChangeBatch] = relationship(back_populates="languages")
    language: Mapped[Language] = relationship()


class ProblemLimitChangeBatchSubmission(Base):
    __table__ = problem_limit_change_batch_submissions_table

    batch_id: Mapped[str]
    submission_id: Mapped[str]
    language_id: Mapped[str]
    original_judgment_id: Mapped[str]
    original_final_verdict: Mapped[Verdict]
    rejudge_status: Mapped[str]
    queued_judgment_id: Mapped[str | None]
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    batch: Mapped[ProblemLimitChangeBatch] = relationship(back_populates="submissions")
    submission: Mapped[Submission] = relationship()
    language: Mapped[Language] = relationship()
    original_judgment: Mapped[SubmissionJudgment] = relationship(
        foreign_keys=[problem_limit_change_batch_submissions_table.c.original_judgment_id]
    )
    queued_judgment: Mapped[SubmissionJudgment | None] = relationship(
        foreign_keys=[problem_limit_change_batch_submissions_table.c.queued_judgment_id]
    )


class ProfilingRun(Base):
    __table__ = profiling_runs_table

    id: Mapped[str]
    problem_id: Mapped[str]
    language_id: Mapped[str]
    source_code: Mapped[str]
    source_hash: Mapped[str]
    status: Mapped[ProfilingStatus]
    safety_factor: Mapped[float]
    worker_id: Mapped[str | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    error_message: Mapped[str | None]
    compile_log: Mapped[str | None]
    triggered_by_user_id: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[Problem] = relationship(back_populates="profiling_runs")
    language: Mapped[Language] = relationship()
    triggered_by_user: Mapped[User | None] = relationship()
    case_results: Mapped[list[ProfilingCaseResult]] = relationship(
        back_populates="profiling_run",
        cascade="all, delete-orphan",
        order_by="ProfilingCaseResult.ordinal",
    )


class ProfilingCaseResult(Base):
    __table__ = profiling_case_results_table

    id: Mapped[str]
    profiling_run_id: Mapped[str]
    test_case_id: Mapped[str]
    ordinal: Mapped[int]
    total_wall_time_ms: Mapped[int | None]
    peak_memory_kb: Mapped[int | None]
    peak_output_bytes: Mapped[int | None]
    peak_pids: Mapped[int | None]
    verdict: Mapped[Verdict]
    exit_code: Mapped[int | None]
    created_at: Mapped[datetime]

    profiling_run: Mapped[ProfilingRun] = relationship(back_populates="case_results")
    test_case: Mapped[ProblemTestCase] = relationship()


class ProblemCategory(Base):
    __table__ = problem_categories_table

    id: Mapped[str]
    name: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problems: Mapped[list[Problem]] = relationship(
        secondary=problem_categories_map,
        back_populates="categories",
    )


@event.listens_for(Session, "before_flush")
def _maintain_problem_model_invariants(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    from web.models.contest import Contest

    affected_contests: set[str] = set()
    affected_problems: set[str] = set()
    categories_to_check: set[ProblemCategory] = set()

    for obj in session.new.union(session.dirty).union(session.deleted):
        if isinstance(obj, Problem):
            state = inspect(obj)

            if obj.contest_id:
                affected_contests.add(obj.contest_id)

            history = state.attrs.contest_id.history
            for old_contest_id in history.deleted:
                if old_contest_id:
                    affected_contests.add(old_contest_id)

            if obj.id:
                affected_problems.add(obj.id)

            category_history = state.attrs.categories.history
            categories_to_check.update(category_history.added)
            categories_to_check.update(category_history.deleted)

            if "categories" in obj.__dict__:
                categories_to_check.update(obj.categories)

        elif isinstance(obj, ProblemTestCase):
            state = inspect(obj)

            if obj.problem_id:
                affected_problems.add(obj.problem_id)

            history = state.attrs.problem_id.history
            for old_problem_id in history.deleted:
                if old_problem_id:
                    affected_problems.add(old_problem_id)

        elif isinstance(obj, ProblemCategory):
            categories_to_check.add(obj)

    for contest_id in affected_contests:
        contest = session.get(Contest, contest_id)
        if contest is None:
            continue

        problems = [problem for problem in contest.problems if problem not in session.deleted]
        problems.sort(key=lambda problem: (problem.ordinal or 10**9, problem.id or ""))

        for expected_ordinal, problem in enumerate(problems, start=1):
            if problem.ordinal != expected_ordinal:
                problem.ordinal = expected_ordinal

    for problem_id in affected_problems:
        loaded_problem = session.get(Problem, problem_id)
        if loaded_problem is None:
            continue

        test_cases = [test_case for test_case in loaded_problem.test_cases if test_case not in session.deleted]
        test_cases.sort(key=lambda test_case: (test_case.ordinal or 10**9, test_case.id or ""))

        for expected_ordinal, test_case in enumerate(test_cases, start=1):
            if test_case.ordinal != expected_ordinal:
                test_case.ordinal = expected_ordinal

    for category in categories_to_check:
        if category in session.deleted or category.id is None and not category.problems:
            continue

        attached_problems = [problem for problem in category.problems if problem not in session.deleted]
        if not attached_problems:
            session.delete(category)
