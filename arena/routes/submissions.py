#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena submission detail page routes.

Routes:
  GET  /submissions/{submission_id}                         arena_submission_detail
  POST /submissions/{submission_id}/request-ai-review       arena_submission_request_ai_review
  POST /submissions/{submission_id}/teacher-feedback        arena_submission_teacher_feedback
  POST /submissions/{submission_id}/teacher-feedback/remove arena_submission_teacher_feedback_remove
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, cast

import anyio
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings as arena_settings
from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services.ai_turnaround_stats_service import get_batch_turnaround_stats
from arena.services.arena_problem_set_report_service import can_teacher_view_submission
from arena.services.arena_teacher_feedback_service import delete_teacher_feedback, upsert_teacher_feedback
from arena.services.session_service import build_current_next_url, build_login_redirect_response
from arena.services.user_ai_credit_service import consume_ai_credit
from shared.db_schema import languages as languages_table
from shared.db_schema.arena import (
    arena_ai_batch_jobs,
    arena_classes,
    arena_problem_sets,
    arena_problems,
    arena_submission_ai_reviews,
    arena_submission_judgments,
    arena_submission_teacher_feedback,
    arena_submission_test_results,
    arena_submissions,
    arena_test_cases,
    arena_users,
)
from shared.enumerations import ArenaNotificationKind, ArenaRole, JudgmentStatus
from shared.language_registry import highlightjs_language_for_language_id
from shared.queue_schema import ArenaAIReviewJob
from shared.services.arena_notification_service import create_arena_notification
from shared.services.testcase_files import read_testcase_full
from shared.services.valkey_service.queue_ops import enqueue_arena_ai_review_job
from shared.signal_names import describe_signal

router = APIRouter(tags=["arena-submissions"])

_SUPERSEDED = JudgmentStatus.SUPERSEDED.value


@dataclass(frozen=True)
class AIReviewData:
    """View-model for the AI review section on the submission detail page.

    Attributes:
        ai_response: Response text from the AI provider.
        ai_response_at: Timestamp when the response was stored.
        ai_review_cost: Cost in provider units (e.g. 0.001234), or None if not recorded.
        used_platform_key: ``True`` when the review consumed Arena platform credits;
            ``False`` when the user's personal API key was used.
        turnaround_seconds: Whole seconds from batch staging until the response
            was stored, or ``None`` when the timestamps are unavailable.
    """

    ai_response: str
    ai_response_at: datetime
    ai_review_cost: float | None
    used_platform_key: bool
    turnaround_seconds: int | None


@dataclass(frozen=True)
class TeacherFeedbackData:
    """View-model for the teacher feedback section on the submission detail page.

    Attributes:
        feedback_text: The feedback body the teacher wrote.
        feedback_at: Timestamp the feedback was last written.
    """

    feedback_text: str
    feedback_at: datetime


@dataclass(frozen=True)
class TestResultData:
    """View-model for the first failing test case on the submission detail page.

    Attributes:
        verdict: Verdict string for this test case (e.g. "WA", "TLE").
        stdout_excerpt: Truncated contestant standard output, or None if empty.
        expected_output: Expected output from the test case, or None if absent.
        is_sample: True when the failing test case is a sample (public) case.
        test_case_ordinal: 1-based ordinal of the failing test case.
        stderr_excerpt: Truncated standard error output, or None if empty.
        exit_signal_description: Human-readable fatal signal description
            (e.g. "SIGSEGV — segmentation fault"), or None when the process
            was not signal-killed.
    """

    verdict: str
    stdout_excerpt: str | None
    expected_output: str | None
    is_sample: bool
    test_case_ordinal: int
    stderr_excerpt: str | None
    exit_signal_description: str | None


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction.

    Args:
        response: Template response returned by Jinja2.

    Returns:
        HTMLResponse: Response typed for FastAPI handlers.
    """
    return cast(HTMLResponse, response)


async def _can_manage_feedback(
    session: AsyncSession,
    *,
    actor: ArenaUser,
    submission_problem_set_id: str | None,
) -> bool:
    """Return True when the actor may write teacher feedback on a submission.

    Authorization is derived from persisted submission data only — never from
    user-supplied ``back_*`` params. A submission must be tied to a problem set
    (teacher-visible) to be eligible. Admins may manage any set's feedback;
    teachers may manage only sets in classes they own.

    Args:
        session: Active database session.
        actor: The authenticated Arena user requesting the action.
        submission_problem_set_id: The submission's persisted ``problem_set_id``.

    Returns:
        bool: True when the actor is allowed to add/edit feedback.
    """
    if submission_problem_set_id is None:
        return False
    if actor.role == ArenaRole.ARENA_ADMIN.value:
        return True
    if actor.role == ArenaRole.ARENA_JUDGE.value:
        return await can_teacher_view_submission(session, teacher_id=actor.id, set_id=submission_problem_set_id)
    return False


@router.get("/submissions/{submission_id}", response_class=HTMLResponse, name="arena_submission_detail")
async def arena_submission_detail(
    submission_id: str,
    request: Request,
    back_class_id: str | None = None,
    back_set_id: str | None = None,
    back_user_id: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the detail page for a single Arena submission.

    Only the submitting user or an arena admin may view the detail page.
    The page shows three cards: problem limits and language, resources and
    verdict, and syntax-highlighted source code.

    Args:
        submission_id: UUID of the ``arena_submissions`` row.
        request: Current HTTP request.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        HTMLResponse: Submission detail page, or a redirect on auth failure.

    Raises:
        HTTPException: 404 when the submission is not found or not owned by the user.
    """
    if current_user is None:
        return build_login_redirect_response(request, next_url=build_current_next_url(request))

    is_admin = current_user.role == ArenaRole.ARENA_ADMIN.value

    # Load submission row with problem and language via raw query for efficiency
    submission_row = (
        await session.execute(
            select(
                arena_submissions.c.id,
                arena_submissions.c.user_id,
                arena_submissions.c.language_id,
                arena_submissions.c.source_code,
                arena_submissions.c.created_at,
                arena_problems.c.id,
                arena_problems.c.arena_number,
                arena_problems.c.title,
                arena_problems.c.time_limit_ms,
                arena_problems.c.memory_limit_kb,
                arena_problems.c.pids_limit,
                arena_problems.c.output_limit_in_bytes,
                languages_table.c.name,
                languages_table.c.icon,
                arena_problem_sets.c.id.label("problem_set_id"),
                arena_problem_sets.c.name.label("problem_set_name"),
                arena_classes.c.id.label("class_id"),
                arena_classes.c.name.label("class_name"),
                arena_users.c.nome.label("submission_owner_name"),
            )
            .select_from(
                arena_submissions.join(
                    arena_problems,
                    arena_submissions.c.problem_id == arena_problems.c.id,
                )
                .join(
                    languages_table,
                    arena_submissions.c.language_id == languages_table.c.id,
                )
                .join(
                    arena_users,
                    arena_submissions.c.user_id == arena_users.c.id,
                )
                .outerjoin(
                    arena_problem_sets,
                    arena_submissions.c.problem_set_id == arena_problem_sets.c.id,
                )
                .outerjoin(
                    arena_classes,
                    arena_problem_sets.c.class_id == arena_classes.c.id,
                )
            )
            .where(arena_submissions.c.id == submission_id)
        )
    ).one_or_none()

    if submission_row is None:
        raise HTTPException(status_code=404, detail="Submission not found.")

    is_owner = submission_row[1] == current_user.id
    is_teacher = current_user.role == ArenaRole.ARENA_JUDGE.value
    is_teacher_view = False
    teacher_view_back_url: str | None = None
    teacher_view_user_name: str | None = None

    submission_problem_set_id = submission_row[14]

    if not is_admin and not is_owner:
        if (
            is_teacher
            and back_set_id
            and back_user_id
            and submission_row[1] == back_user_id
            and back_set_id == submission_problem_set_id
        ):
            allowed = await can_teacher_view_submission(session, teacher_id=current_user.id, set_id=back_set_id)
            if not allowed:
                raise HTTPException(status_code=404, detail="Submission not found.")
            is_teacher_view = True
            teacher_view_user_name = await session.scalar(
                select(arena_users.c.nome).where(arena_users.c.id == back_user_id)
            )
            teacher_view_back_url = (
                str(
                    request.url_for(
                        "arena_class_problem_set_report_student",
                        class_id=back_class_id or "",
                        set_id=back_set_id,
                        user_id=back_user_id,
                    )
                )
                + f"#{submission_id}"
            )
        else:
            raise HTTPException(status_code=404, detail="Submission not found.")

    # Load the active (non-superseded) judgment — most recent one
    active_j_sq = (
        select(
            arena_submission_judgments.c.submission_id,
            func.max(arena_submission_judgments.c.created_at).label("max_created_at"),
        )
        .where(
            arena_submission_judgments.c.submission_id == submission_id,
            arena_submission_judgments.c.status != _SUPERSEDED,
        )
        .group_by(arena_submission_judgments.c.submission_id)
        .subquery()
    )
    judgment_row = (
        await session.execute(
            select(
                arena_submission_judgments.c.id,
                arena_submission_judgments.c.status,
                arena_submission_judgments.c.final_verdict,
                arena_submission_judgments.c.max_wall_time_ms,
                arena_submission_judgments.c.max_memory_kb,
                arena_submission_judgments.c.compile_log,
                arena_submission_judgments.c.error_message,
                arena_submission_judgments.c.max_output_bytes,
            ).select_from(
                arena_submission_judgments.join(
                    active_j_sq,
                    and_(
                        arena_submission_judgments.c.submission_id == active_j_sq.c.submission_id,
                        arena_submission_judgments.c.created_at == active_j_sq.c.max_created_at,
                    ),
                )
            )
        )
    ).one_or_none()

    language_id = submission_row[2]
    highlight_language = highlightjs_language_for_language_id(language_id)

    # Load submit_to_ai flag, AI review row, batch job status, and teacher feedback (all LEFT JOINs)
    sub_extra = (
        await session.execute(
            select(
                arena_submissions.c.submit_to_ai,
                arena_submission_ai_reviews.c.ai_response,
                arena_submission_ai_reviews.c.ai_response_at,
                arena_submission_ai_reviews.c["_ai_review_cost"],
                arena_submission_ai_reviews.c.used_platform_key,
                arena_ai_batch_jobs.c.local_status.label("batch_local_status"),
                arena_ai_batch_jobs.c.created_at.label("batch_created_at"),
                arena_submission_teacher_feedback.c.feedback_text,
                arena_submission_teacher_feedback.c.feedback_at,
            )
            .select_from(
                arena_submissions.outerjoin(
                    arena_submission_ai_reviews,
                    arena_submissions.c.id == arena_submission_ai_reviews.c.submission_id,
                )
                .outerjoin(
                    arena_ai_batch_jobs,
                    arena_submissions.c.id == arena_ai_batch_jobs.c.submission_id,
                )
                .outerjoin(
                    arena_submission_teacher_feedback,
                    arena_submissions.c.id == arena_submission_teacher_feedback.c.submission_id,
                )
            )
            .where(arena_submissions.c.id == submission_id)
        )
    ).one_or_none()

    submission_submit_to_ai = bool(sub_extra[0]) if sub_extra else False
    batch_local_status: str | None = sub_extra[5] if sub_extra else None
    ai_review: AIReviewData | None = None
    if sub_extra and sub_extra[1] is not None:
        raw_cost = sub_extra[3]
        response_at = sub_extra[2]
        used_platform_key = bool(sub_extra[4])
        batch_created_at = sub_extra[6]
        turnaround_seconds = (
            max(0, int((response_at - batch_created_at).total_seconds()))
            if used_platform_key and batch_created_at is not None
            else None
        )
        ai_review = AIReviewData(
            ai_response=sub_extra[1],
            ai_response_at=response_at,
            ai_review_cost=raw_cost / 1_000_000 if raw_cost is not None else None,
            used_platform_key=used_platform_key,
            turnaround_seconds=turnaround_seconds,
        )

    # Teacher feedback is a non-AC-only concept: if a previously non-AC submission is
    # rejudged to AC (or is mid-rejudge with no active verdict), hide any stored feedback
    # even though the row persists, matching the create/edit gate below.
    active_verdict = judgment_row[2] if judgment_row is not None else None
    feedback_applies = active_verdict not in (None, "AC")

    teacher_feedback: TeacherFeedbackData | None = None
    if feedback_applies and sub_extra and sub_extra[7] is not None:
        teacher_feedback = TeacherFeedbackData(
            feedback_text=sub_extra[7],
            feedback_at=sub_extra[8],
        )

    # A manager (set teacher / admin) may add or edit feedback on a non-AC, set-tied submission.
    can_give_feedback = feedback_applies and await _can_manage_feedback(
        session, actor=current_user, submission_problem_set_id=submission_problem_set_id
    )

    ai_review_current_balance = current_user.ai_backend_credits
    ai_review_uses_platform_credit = not bool(current_user.ai_api_key)
    ai_review_request_allowed = not ai_review_uses_platform_credit or ai_review_current_balance > 0
    ai_review_projected_balance = (
        ai_review_current_balance - 1
        if ai_review_uses_platform_credit and ai_review_request_allowed
        else ai_review_current_balance
    )
    ai_turnaround_stats = None
    batch_review_active = batch_local_status not in (None, "completed", "failed", "expired", "cancelled")
    if (
        ai_review_uses_platform_credit
        and is_owner
        and ai_review is None
        and active_verdict not in (None, "AC")
        and not submission_submit_to_ai
        and not batch_review_active
    ):
        ai_turnaround_stats = await get_batch_turnaround_stats(request.app.state.valkey_runtime)

    # Load the first failing test case result (at most one row per judgment)
    test_result: TestResultData | None = None
    if judgment_row is not None:
        tr_row = (
            await session.execute(
                select(
                    arena_submission_test_results.c.verdict,
                    arena_submission_test_results.c.stdout_excerpt,
                    arena_test_cases.c.is_sample,
                    arena_test_cases.c.ordinal,
                    arena_submission_test_results.c.stderr_excerpt,
                    arena_submission_test_results.c.exit_signal,
                )
                .select_from(
                    arena_submission_test_results.join(
                        arena_test_cases,
                        arena_submission_test_results.c.test_case_id == arena_test_cases.c.id,
                    )
                )
                .where(arena_submission_test_results.c.judgment_id == judgment_row[0])
            )
        ).one_or_none()
        if tr_row is not None:
            # Expected-output content now lives on the filesystem; read it from disk.
            _, expected_output = await anyio.to_thread.run_sync(
                read_testcase_full, submission_row[5], tr_row[3], arena_settings.PROBLEM_TESTCASE_DIR
            )
            test_result = TestResultData(
                verdict=tr_row[0],
                stdout_excerpt=tr_row[1],
                expected_output=expected_output,
                is_sample=tr_row[2],
                test_case_ordinal=tr_row[3],
                stderr_excerpt=tr_row[4],
                exit_signal_description=describe_signal(tr_row[5]) if tr_row[5] is not None else None,
            )

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "submissions/submission_detail.html",
            {
                "current_user": current_user,
                # Submission
                "submission_id": submission_row[0],
                "submission_owner_id": submission_row[1],
                "submission_owner_name": submission_row[18],
                "source_code": submission_row[3],
                "submitted_at": submission_row[4],
                # Problem
                "problem_id": submission_row[5],
                "problem_number": submission_row[6],
                "problem_title": submission_row[7],
                "time_limit_ms": submission_row[8],
                "memory_limit_kb": submission_row[9],
                "pids_limit": submission_row[10],
                "output_limit_in_bytes": submission_row[11],
                # Language
                "language_name": submission_row[12],
                "language_icon": submission_row[13],
                # Problem set (nullable)
                "problem_set_id": submission_row[14],
                "problem_set_name": submission_row[15],
                "class_id": submission_row[16],
                "class_name": submission_row[17],
                # Judgment
                "judgment": judgment_row,
                # Syntax highlighting
                "highlight_language_class": f"language-{highlight_language}",
                "highlight_language": highlight_language,
                # AI review
                "is_owner": is_owner,
                "is_admin": is_admin,
                "submission_submit_to_ai": submission_submit_to_ai,
                "batch_local_status": batch_local_status,
                "ai_review": ai_review,
                "ai_review_current_balance": ai_review_current_balance,
                "ai_review_projected_balance": ai_review_projected_balance,
                "ai_review_request_allowed": ai_review_request_allowed,
                "ai_review_uses_platform_credit": ai_review_uses_platform_credit,
                "ai_batch_window_minutes": arena_settings.ai_batch_window_minutes,
                "ai_turnaround_stats": ai_turnaround_stats,
                # First failing test case
                "test_result": test_result,
                # Teacher view context (set when a teacher drills into a student's submission)
                "is_teacher_view": is_teacher_view,
                "teacher_view_back_url": teacher_view_back_url,
                "teacher_view_user_name": teacher_view_user_name,
                # Teacher feedback
                "teacher_feedback": teacher_feedback,
                "can_give_feedback": can_give_feedback,
                "back_class_id": back_class_id,
                "back_set_id": back_set_id,
                "back_user_id": back_user_id,
            },
        )
    )


@router.post(
    "/submissions/{submission_id}/request-ai-review",
    name="arena_submission_request_ai_review",
)
async def arena_submission_request_ai_review(
    submission_id: str,
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Enqueue an AI code-review job for a submission.

    Only the submission owner may request a review.  The route is idempotent:
    if a review has already been requested or completed, it redirects back to
    the submission detail without double-enqueuing.

    The decision of whether to use the user's own API key or the platform key is
    frozen at request time (``use_platform_key``) and stored in the job payload so
    the worker does not re-derive it from the user's current state.

    Args:
        submission_id: UUID of the ``arena_submissions`` row.
        request: Current HTTP request.
        flash: Flash-message dependency for user feedback.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        Response: 303 redirect to the submission detail page.

    Raises:
        HTTPException: 404 when the submission is not found or not owned by the user.
    """
    if current_user is None:
        return build_login_redirect_response(
            request,
            next_url=request.url_for("arena_submission_detail", submission_id=submission_id).path,
        )

    detail_url = str(request.url_for("arena_submission_detail", submission_id=submission_id))

    # Load submission fields needed for the job and ownership check
    sub_row = (
        await session.execute(
            select(
                arena_submissions.c.user_id,
                arena_submissions.c.problem_id,
                arena_submissions.c.language_id,
                arena_submissions.c.submit_to_ai,
            ).where(arena_submissions.c.id == submission_id)
        )
    ).one_or_none()

    if sub_row is None or sub_row[0] != current_user.id:
        raise HTTPException(status_code=404, detail="Submission not found.")

    ai_review_exists = (
        await session.scalar(
            select(arena_submission_ai_reviews.c.submission_id).where(
                arena_submission_ai_reviews.c.submission_id == submission_id
            )
        )
    ) is not None
    if ai_review_exists:
        return RedirectResponse(url=detail_url, status_code=303)

    # Already flagged as pending but no review yet. The Valkey enqueue is a
    # second write that cannot share the DB transaction, so a crash or failure
    # after commit can leave submit_to_ai=True with no job on the queue. Re-enqueue
    # idempotently to self-heal that state instead of dead-ending the user. No
    # credit is consumed and submit_to_ai is not touched: the intent (and any
    # platform-credit charge) was already recorded on the first request.
    if sub_row[3]:  # submit_to_ai is True
        heal_job = ArenaAIReviewJob(
            submission_id=submission_id,
            user_id=sub_row[0],
            problem_id=sub_row[1],
            language_id=sub_row[2],
            use_platform_key=not bool(current_user.ai_api_key),
        )
        await enqueue_arena_ai_review_job(request.app.state.valkey_runtime, heal_job)
        flash("Your AI review is still being processed.", FlashCategory.INFO)
        return RedirectResponse(url=detail_url, status_code=303)

    # Gate: user must have their own API key or available platform credits.
    # The decision is frozen here so the worker does not re-derive it.
    use_platform_key = not bool(current_user.ai_api_key)
    if use_platform_key:
        consumed = await consume_ai_credit(current_user, session, submission_id=submission_id)
        if not consumed:
            flash("You do not have enough AI credits for this action.", FlashCategory.DANGER)
            return RedirectResponse(url=detail_url, status_code=303)

    # Mark submission as queued for AI review
    await session.execute(
        update(arena_submissions).where(arena_submissions.c.id == submission_id).values(submit_to_ai=True)
    )

    job = ArenaAIReviewJob(
        submission_id=submission_id,
        user_id=sub_row[0],
        problem_id=sub_row[1],
        language_id=sub_row[2],
        use_platform_key=use_platform_key,
    )
    await session.commit()
    await enqueue_arena_ai_review_job(request.app.state.valkey_runtime, job)

    return RedirectResponse(url=detail_url, status_code=303)


def _feedback_redirect_url(
    request: Request,
    submission_id: str,
    *,
    back_class_id: str,
    back_set_id: str,
    back_user_id: str,
) -> str:
    """Build the detail-page redirect, forwarding teacher-view ``back_*`` params when present."""
    url = str(request.url_for("arena_submission_detail", submission_id=submission_id))
    if back_set_id and back_user_id:
        query = f"?back_class_id={back_class_id}&back_set_id={back_set_id}&back_user_id={back_user_id}"
        return url + query
    return url


@router.post(
    "/submissions/{submission_id}/teacher-feedback",
    name="arena_submission_teacher_feedback",
)
async def arena_submission_teacher_feedback_submit(
    submission_id: str,
    request: Request,
    flash: FlashDep,
    feedback: Annotated[str, Form()] = "",
    back_class_id: Annotated[str, Form()] = "",
    back_set_id: Annotated[str, Form()] = "",
    back_user_id: Annotated[str, Form()] = "",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Create or update teacher feedback on a student's non-AC, set-tied submission.

    Authorization is derived from the submission's persisted ``problem_set_id``
    (never the ``back_*`` form fields, which are navigation-only): the assigned
    teacher of the set's class or an Arena admin may write feedback. Each save
    notifies the student with a fresh notification (per-update ``source_ref``),
    preserving prior notifications.

    Args:
        submission_id: UUID of the ``arena_submissions`` row.
        request: Current HTTP request.
        flash: Flash-message dependency for user feedback.
        feedback: The feedback body submitted by the teacher.
        back_class_id: Navigation-only class id for the return link.
        back_set_id: Navigation-only problem-set id for the return link.
        back_user_id: Navigation-only student id for the return link.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        Response: 303 redirect to the submission detail page.

    Raises:
        HTTPException: 404 when the submission is missing, not set-tied, AC, or
            the actor is not allowed to manage feedback for it.
    """
    if current_user is None:
        return build_login_redirect_response(
            request,
            next_url=request.url_for("arena_submission_detail", submission_id=submission_id).path,
        )

    # Load the submission, its persisted problem set, problem metadata, and active verdict.
    active_j_sq = (
        select(
            arena_submission_judgments.c.submission_id,
            func.max(arena_submission_judgments.c.created_at).label("max_created_at"),
        )
        .where(
            arena_submission_judgments.c.submission_id == submission_id,
            arena_submission_judgments.c.status != _SUPERSEDED,
        )
        .group_by(arena_submission_judgments.c.submission_id)
        .subquery()
    )
    row = (
        await session.execute(
            select(
                arena_submissions.c.user_id,
                arena_submissions.c.problem_set_id,
                arena_problems.c.arena_number,
                arena_problems.c.title,
                arena_submission_judgments.c.final_verdict,
            )
            .select_from(
                arena_submissions.join(
                    arena_problems,
                    arena_submissions.c.problem_id == arena_problems.c.id,
                )
                .outerjoin(
                    active_j_sq,
                    arena_submissions.c.id == active_j_sq.c.submission_id,
                )
                .outerjoin(
                    arena_submission_judgments,
                    and_(
                        arena_submission_judgments.c.submission_id == active_j_sq.c.submission_id,
                        arena_submission_judgments.c.created_at == active_j_sq.c.max_created_at,
                    ),
                )
            )
            .where(arena_submissions.c.id == submission_id)
        )
    ).one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Submission not found.")

    student_id, problem_set_id, problem_number, problem_title, final_verdict = row

    if not await _can_manage_feedback(session, actor=current_user, submission_problem_set_id=problem_set_id):
        raise HTTPException(status_code=404, detail="Submission not found.")

    # Non-AC only: feedback targets submissions the student has not yet got accepted.
    if final_verdict in (None, "AC"):
        raise HTTPException(status_code=404, detail="Submission not found.")

    redirect_url = _feedback_redirect_url(
        request,
        submission_id,
        back_class_id=back_class_id,
        back_set_id=back_set_id,
        back_user_id=back_user_id,
    )

    feedback_text = feedback.strip()
    if not feedback_text:
        flash("Feedback cannot be empty.", FlashCategory.WARNING)
        return RedirectResponse(url=redirect_url, status_code=303)

    feedback_at = await upsert_teacher_feedback(
        session,
        submission_id=submission_id,
        teacher_id=current_user.id,
        feedback_text=feedback_text,
    )
    await create_arena_notification(
        session,
        user_id=student_id,
        notification_kind=ArenaNotificationKind.TEACHER_FEEDBACK_POSTED,
        title="Teacher feedback",
        message=(f"Your teacher left feedback on your submission for problem {problem_number} - {problem_title}."),
        target_url=f"/submissions/{submission_id}",
        source_ref=f"{submission_id}:{int(feedback_at.timestamp() * 1_000_000)}",
        context={"submission_id": submission_id},
    )
    await session.commit()

    flash("Feedback saved.", FlashCategory.SUCCESS)
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post(
    "/submissions/{submission_id}/teacher-feedback/remove",
    name="arena_submission_teacher_feedback_remove",
)
async def arena_submission_teacher_feedback_remove(
    submission_id: str,
    request: Request,
    flash: FlashDep,
    back_class_id: Annotated[str, Form()] = "",
    back_set_id: Annotated[str, Form()] = "",
    back_user_id: Annotated[str, Form()] = "",
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete existing teacher feedback on a submission.

    Authorization mirrors :func:`arena_submission_teacher_feedback_submit`:
    the assigned teacher of the submission's persisted problem-set class, or
    an Arena admin, may remove feedback. Unlike the write path, the
    submission's current verdict is irrelevant here — stale feedback left
    behind by a later rejudge to AC can still be removed.

    Args:
        submission_id: UUID of the ``arena_submissions`` row.
        request: Current HTTP request.
        flash: Flash-message dependency for user feedback.
        back_class_id: Navigation-only class id for the return link.
        back_set_id: Navigation-only problem-set id for the return link.
        back_user_id: Navigation-only student id for the return link.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        Response: 303 redirect to the submission detail page.

    Raises:
        HTTPException: 404 when the submission is missing, not set-tied, or
            the actor is not allowed to manage feedback for it.
    """
    if current_user is None:
        return build_login_redirect_response(
            request,
            next_url=request.url_for("arena_submission_detail", submission_id=submission_id).path,
        )

    row = (
        await session.execute(select(arena_submissions.c.problem_set_id).where(arena_submissions.c.id == submission_id))
    ).one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Submission not found.")

    (problem_set_id,) = row

    if not await _can_manage_feedback(session, actor=current_user, submission_problem_set_id=problem_set_id):
        raise HTTPException(status_code=404, detail="Submission not found.")

    redirect_url = _feedback_redirect_url(
        request,
        submission_id,
        back_class_id=back_class_id,
        back_set_id=back_set_id,
        back_user_id=back_user_id,
    )

    deleted = await delete_teacher_feedback(session, submission_id=submission_id)
    await session.commit()

    if deleted:
        flash("Feedback removed.", FlashCategory.SUCCESS)
    else:
        flash("No feedback to remove.", FlashCategory.WARNING)
    return RedirectResponse(url=redirect_url, status_code=303)
