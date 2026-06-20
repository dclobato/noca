#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import event, inspect
from sqlalchemy.orm import Mapped, Session, relationship

from shared.db_schema import human_submission_confirmations as human_submission_confirmations_table
from shared.db_schema import submission_judgment_audit as submission_judgment_audit_table
from shared.db_schema import submission_judgments as submission_judgments_table
from shared.db_schema import submission_test_results as submission_test_results_table
from shared.db_schema import submissions as submissions_table
from shared.db_schema import verdict_overrides as verdict_overrides_table
from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from shared.timing import compute_timestamp_seconds
from web.database import Base

if TYPE_CHECKING:
    from web.models.language import Language
    from web.models.problem import Problem, ProblemTestCase
    from web.models.users import User


class Submission(Base):
    __table__ = submissions_table

    id: Mapped[str]
    problem_id: Mapped[str]
    team_id: Mapped[str]
    language_id: Mapped[str]
    source_code: Mapped[str]
    source_hash: Mapped[str]
    source_size_bytes: Mapped[int]
    timestamp_seconds: Mapped[int]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[Problem] = relationship("Problem", foreign_keys=[submissions_table.c.problem_id])
    team: Mapped[User] = relationship("User", foreign_keys=[submissions_table.c.team_id], back_populates="submissions")
    language: Mapped[Language] = relationship("Language", foreign_keys=[submissions_table.c.language_id])
    judgments: Mapped[list[SubmissionJudgment]] = relationship(
        "SubmissionJudgment",
        back_populates="submission",
    )
    overrides: Mapped[list[VerdictOverride]] = relationship(
        "VerdictOverride",
        back_populates="submission",
    )


class SubmissionJudgment(Base):
    __table__ = submission_judgments_table

    id: Mapped[str]
    submission_id: Mapped[str]
    status: Mapped[JudgmentStatus]
    autojudge_verdict: Mapped[Verdict | None]
    final_verdict: Mapped[Verdict | None]
    compile_log: Mapped[str | None]
    max_wall_time_ms: Mapped[int | None]
    max_memory_kb: Mapped[int | None]
    min_wall_time_ms: Mapped[int | None]
    min_memory_kb: Mapped[int | None]
    error_message: Mapped[str | None]
    worker_id: Mapped[str | None]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    timestamp_seconds: Mapped[int | None]

    submission: Mapped[Submission] = relationship("Submission", back_populates="judgments")
    test_results: Mapped[list[SubmissionTestResult]] = relationship(
        "SubmissionTestResult",
        back_populates="judgment",
    )
    confirmations: Mapped[list[HumanSubmissionConfirmation]] = relationship(
        "HumanSubmissionConfirmation",
        foreign_keys="HumanSubmissionConfirmation.judgment_id",
        back_populates="judgment",
    )
    audit_logs: Mapped[list[SubmissionJudgmentAudit]] = relationship(
        "SubmissionJudgmentAudit",
        primaryjoin="SubmissionJudgment.id == foreign(SubmissionJudgmentAudit.judgment_id)",
        back_populates="judgment",
    )
    overrides: Mapped[list[VerdictOverride]] = relationship(
        "VerdictOverride",
        back_populates="judgment",
    )


class HumanSubmissionConfirmation(Base):
    __table__ = human_submission_confirmations_table

    id: Mapped[str]
    judgment_id: Mapped[str]
    judge_id: Mapped[str]
    confirmed_verdict: Mapped[Verdict]
    is_chief_confirmation: Mapped[bool]
    created_at: Mapped[datetime]
    timestamp_seconds: Mapped[int | None]

    judgment: Mapped[SubmissionJudgment] = relationship(
        "SubmissionJudgment",
        foreign_keys=[human_submission_confirmations_table.c.judgment_id],
        back_populates="confirmations",
    )
    judge: Mapped[User] = relationship(
        "User",
        foreign_keys=[human_submission_confirmations_table.c.judge_id],
        back_populates="confirmations_as_judge",
    )


class SubmissionTestResult(Base):
    __table__ = submission_test_results_table

    id: Mapped[str]
    judgment_id: Mapped[str]
    test_case_id: Mapped[str]
    verdict: Mapped[Verdict]
    wall_time_ms: Mapped[int | None]
    memory_kb: Mapped[int | None]
    exit_code: Mapped[int | None]
    stdout_excerpt: Mapped[str | None]
    stderr_excerpt: Mapped[str | None]
    created_at: Mapped[datetime]

    judgment: Mapped[SubmissionJudgment] = relationship(
        "SubmissionJudgment",
        back_populates="test_results",
    )
    test_case: Mapped[ProblemTestCase] = relationship("ProblemTestCase")


class SubmissionJudgmentAudit(Base):
    __table__ = submission_judgment_audit_table

    id: Mapped[str]
    judgment_id: Mapped[str]
    submission_id: Mapped[str]
    actor_user_id: Mapped[str | None]
    event_source: Mapped[str]
    event_type: Mapped[str]
    from_status: Mapped[JudgmentStatus | None]
    to_status: Mapped[JudgmentStatus | None]
    from_verdict: Mapped[Verdict | None]
    to_verdict: Mapped[Verdict | None]
    message: Mapped[str | None]
    created_at: Mapped[datetime]
    timestamp_seconds: Mapped[int | None]

    judgment: Mapped[SubmissionJudgment] = relationship(
        "SubmissionJudgment",
        primaryjoin="foreign(SubmissionJudgmentAudit.judgment_id) == SubmissionJudgment.id",
        back_populates="audit_logs",
    )


class VerdictOverride(Base):
    __table__ = verdict_overrides_table

    id: Mapped[str]
    submission_id: Mapped[str]
    judgment_id: Mapped[str]
    overridden_by: Mapped[str]
    original_verdict: Mapped[Verdict]
    new_verdict: Mapped[Verdict]
    reason: Mapped[str]
    timestamp_seconds: Mapped[int | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    submission: Mapped[Submission] = relationship("Submission", back_populates="overrides")
    judgment: Mapped[SubmissionJudgment] = relationship("SubmissionJudgment", back_populates="overrides")
    chief_judge: Mapped[User] = relationship("User", foreign_keys=[verdict_overrides_table.c.overridden_by])


_SUBMISSION_IMMUTABLE_FIELDS = {
    "problem_id",
    "team_id",
    "language_id",
    "source_code",
    "source_hash",
    "source_size_bytes",
}


def _derive_final_verdict(
    confirmations: list[HumanSubmissionConfirmation],
    autojudge_verdict: Verdict | None,
) -> Verdict | None:
    """Derive the final verdict from human confirmations.

    Args:
        confirmations: All non-deleted confirmations for the judgment.
        autojudge_verdict: The verdict produced by the autojudge.

    Returns:
        The derived final verdict, or None if more confirmations are required.

    Raises:
        ValueError: If more than one chief confirmation exists.
    """
    chief_confirmations = [c for c in confirmations if c.is_chief_confirmation]
    if chief_confirmations:
        if len(chief_confirmations) > 1:
            raise ValueError("A judgment can have at most one chief confirmation.")
        return chief_confirmations[0].confirmed_verdict

    non_chief = [c for c in confirmations if not c.is_chief_confirmation]
    if len(non_chief) >= 2 and autojudge_verdict is not None:
        matching = [c for c in non_chief if c.confirmed_verdict == autojudge_verdict]
        if len(matching) >= 2:
            return autojudge_verdict

    return None


def _validate_submission(session: Session, submission: Submission) -> None:
    from web.models.problem import Problem
    from web.models.users import User

    problem = submission.problem or session.get(Problem, submission.problem_id)
    team = submission.team or session.get(User, submission.team_id)

    if problem is None or team is None:
        return

    if team.role != RoleEnum.TEAM:
        raise ValueError("Submission.team must reference a user with TEAM role.")

    if team.contest_id != problem.contest_id:
        raise ValueError("Submission.team and Submission.problem must belong to the same contest.")


def _validate_confirmation(session: Session, confirmation: HumanSubmissionConfirmation) -> None:
    from web.models.users import User

    judgment = confirmation.judgment or session.get(SubmissionJudgment, confirmation.judgment_id)
    judge = confirmation.judge or session.get(User, confirmation.judge_id)

    if judgment is None or judge is None:
        return

    if judge.role != RoleEnum.JUDGE:
        raise ValueError("Only users with JUDGE role can confirm a submission judgment.")

    if judgment.status != JudgmentStatus.DONE:
        raise ValueError("Human confirmations are only allowed after the judgment is DONE.")


def _validate_test_result(session: Session, test_result: SubmissionTestResult) -> None:
    from web.models.problem import ProblemTestCase

    judgment = test_result.judgment or session.get(SubmissionJudgment, test_result.judgment_id)
    test_case = test_result.test_case or session.get(ProblemTestCase, test_result.test_case_id)

    if judgment is None or test_case is None:
        return

    if test_case.problem_id != judgment.submission.problem_id:
        raise ValueError("Submission test results must reference test cases from the submission problem.")


def _append_judgment_audit(session: Session, judgment: SubmissionJudgment) -> None:
    state = inspect(judgment)

    if judgment in session.new:
        event_type = "created"
        from_status = None
        to_status = judgment.status
        from_verdict = None
        to_verdict = judgment.final_verdict
    else:
        tracked_attrs = {
            attr.key
            for attr in state.attrs
            if attr.history.has_changes()
            and attr.key
            in {
                "status",
                "autojudge_verdict",
                "final_verdict",
                "worker_id",
                "started_at",
                "finished_at",
                "error_message",
            }
        }
        if not tracked_attrs:
            return

        status_history = state.attrs.status.history
        final_verdict_history = state.attrs.final_verdict.history
        from_status = status_history.deleted[0] if status_history.deleted else judgment.status
        to_status = status_history.added[0] if status_history.added else judgment.status
        from_verdict = final_verdict_history.deleted[0] if final_verdict_history.deleted else judgment.final_verdict
        to_verdict = final_verdict_history.added[0] if final_verdict_history.added else judgment.final_verdict
        event_type = "updated"

    # Compute contest-relative timestamp_seconds via the session identity map.
    # In web-layer flushes the submission, problem, and contest are typically
    # already cached; session.get() is an O(1) identity-map lookup in that case.
    contest_start: datetime | None = None
    submission = session.get(Submission, judgment.submission_id)
    if submission is not None:
        from web.models.contest import Contest
        from web.models.problem import Problem

        problem = session.get(Problem, submission.problem_id)
        if problem is not None:
            contest = session.get(Contest, problem.contest_id)
            if contest is not None:
                contest_start = contest.start_time

    now = datetime.now(UTC)

    session.add(
        SubmissionJudgmentAudit(
            judgment=judgment,
            submission_id=judgment.submission_id,
            event_source="model_hook",
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            from_verdict=from_verdict,
            to_verdict=to_verdict,
            created_at=now,
            timestamp_seconds=compute_timestamp_seconds(contest_start, now) if contest_start else None,
        )
    )


@event.listens_for(Session, "before_flush")
def _maintain_submission_model_invariants(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    affected_judgments: set[SubmissionJudgment] = set()

    for obj in session.new.union(session.dirty):
        if isinstance(obj, Submission):
            _validate_submission(session, obj)

            if obj in session.dirty:
                state = inspect(obj)
                changed_fields = {
                    field_name
                    for field_name in _SUBMISSION_IMMUTABLE_FIELDS
                    if state.attrs[field_name].history.has_changes()
                }
                if changed_fields:
                    changed_list = ", ".join(sorted(changed_fields))
                    raise ValueError(f"Submission is immutable after creation. Changed fields: {changed_list}.")

        elif isinstance(obj, SubmissionJudgment):
            affected_judgments.add(obj)

            if obj.status == JudgmentStatus.DONE and obj.autojudge_verdict is None:
                raise ValueError("SubmissionJudgment.autojudge_verdict must be set when status is DONE.")

        elif isinstance(obj, HumanSubmissionConfirmation):
            _validate_confirmation(session, obj)
            if obj.judgment is not None:
                affected_judgments.add(obj.judgment)

        elif isinstance(obj, SubmissionTestResult):
            _validate_test_result(session, obj)

        elif isinstance(obj, VerdictOverride):
            from web.models.contest import Contest
            from web.models.problem import Problem

            submission = obj.submission or session.get(Submission, obj.submission_id)
            if submission is not None:
                problem = submission.problem or session.get(Problem, submission.problem_id)
                if problem is not None:
                    contest = session.get(Contest, problem.contest_id)
                    if contest is not None and contest.chief_judge_id != obj.overridden_by:
                        raise ValueError("Only the contest chief judge may create a VerdictOverride.")

            judgment = obj.judgment if obj.judgment is not None else session.get(SubmissionJudgment, obj.judgment_id)
            if judgment is not None:
                affected_judgments.add(judgment)

    for obj in session.deleted:
        if isinstance(obj, HumanSubmissionConfirmation) and obj.judgment is not None:
            affected_judgments.add(obj.judgment)
        elif isinstance(obj, VerdictOverride):
            judgment = obj.judgment if obj.judgment is not None else session.get(SubmissionJudgment, obj.judgment_id)
            if judgment is not None:
                affected_judgments.add(judgment)

    for judgment in affected_judgments:
        overrides = [override for override in judgment.overrides if override not in session.deleted]
        overrides += [
            override
            for override in session.new
            if isinstance(override, VerdictOverride)
            and override.judgment_id == judgment.id
            and override not in overrides
        ]

        if overrides:
            latest_override = max(overrides, key=lambda override: override.created_at.replace(tzinfo=None))
            judgment.final_verdict = latest_override.new_verdict
        else:
            confirmations = [
                confirmation for confirmation in judgment.confirmations if confirmation not in session.deleted
            ]

            if len(confirmations) > 3:
                raise ValueError("A judgment can have at most three human confirmations.")

            judge_ids = [confirmation.judge_id for confirmation in confirmations]
            if len(judge_ids) != len(set(judge_ids)):
                raise ValueError("A judge can confirm a given judgment only once.")

            judgment.final_verdict = _derive_final_verdict(confirmations, judgment.autojudge_verdict)

    for obj in session.new.union(session.dirty):
        if isinstance(obj, SubmissionJudgment):
            _append_judgment_audit(session, obj)
