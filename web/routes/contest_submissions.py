#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from shared.services.lock_service import get_lock
from web.config import settings
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models import (
    HumanSubmissionConfirmation,
    Problem,
    Submission,
    SubmissionJudgment,
    SubmissionTestResult,
    UberAdmin,
    User,
)
from web.routes import contest_submissions_files as _contest_submissions_files
from web.routes import contest_submissions_review as _contest_submissions_review
from web.routes.contest_admin_problem_helpers import _label
from web.routes.contest_submissions_helpers import (
    _build_confirmation_panel,
    _html,
    submission_highlight_assets,
)
from web.services.judging_service import get_judging_history
from web.services.judgment_utils import get_active_judgment
from web.services.submission_service import build_team_submissions_zip

router = APIRouter(prefix="/c/{slug}/submissions", tags=["contest_submissions"])

download_source = _contest_submissions_files.download_source
download_test_case_file = _contest_submissions_files.download_test_case_file
test_case_detail = _contest_submissions_files.test_case_detail
post_acquire_submission_review = _contest_submissions_review.post_acquire_submission_review
post_confirm_submission_verdict = _contest_submissions_review.post_confirm_submission_verdict
post_rejudge_submission = _contest_submissions_review.post_rejudge_submission
post_release_submission_review = _contest_submissions_review.post_release_submission_review

__all__ = [
    "router",
    "download_source",
    "download_test_case_file",
    "test_case_detail",
    "post_acquire_submission_review",
    "post_confirm_submission_verdict",
    "post_rejudge_submission",
    "post_release_submission_review",
]


@router.get("/download-all", name="team_submissions_download")
async def download_all_sources(
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Download all finalized submissions made by the current team during the contest."""
    ensure_allowed_role(ctx.actor, (RoleEnum.TEAM,))

    if not ctx.contest.is_past or not ctx.contest.release_scoreboard_after_end:
        raise HTTPException(status_code=403)

    assert isinstance(ctx.actor, User)
    try:
        filename, zip_bytes = await build_team_submissions_zip(
            ctx.session,
            ctx.contest,
            ctx.actor,
            statement_dir=settings.PROBLEM_STATEMENT_DIR,
        )
    except ValueError as exc:
        return Response(content=str(exc), status_code=409)

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{submission_id}/review", response_class=HTMLResponse, name="submission_review")
async def review_submission(
    request: Request,
    submission_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> HTMLResponse:
    """Render the unified submission review page.

    Combines source code, compile log, judging history, per-test-case results, the verdict
    confirmation panel, confirmation form (for judges), and the chief-judge override form.

    Args:
        request: The incoming HTTP request.
        submission_id: UUID of the submission to review.
        ctx: Contest context with authentication and session.

    Returns:
        The rendered review page.

    Raises:
        HTTPException: 404 if the submission is not found or does not belong to the contest.
    """
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE))

    result = await ctx.session.execute(
        select(Submission)
        .where(Submission.id == submission_id)
        .options(
            selectinload(Submission.judgments)
            .selectinload(SubmissionJudgment.test_results)
            .selectinload(SubmissionTestResult.test_case),
            selectinload(Submission.judgments)
            .selectinload(SubmissionJudgment.confirmations)
            .selectinload(HumanSubmissionConfirmation.judge),
            selectinload(Submission.overrides),
            selectinload(Submission.language),
            selectinload(Submission.problem).selectinload(Problem.language_limits),
        )
    )
    submission = result.scalar_one_or_none()
    if submission is None or submission.problem.contest_id != ctx.contest.id:
        raise HTTPException(status_code=404)

    active_judgment = get_active_judgment(submission)
    lock_service_available = request.app.state.valkey_runtime.is_available
    review_lock = None
    review_lock_holder_name = None
    if active_judgment is not None and lock_service_available:
        review_lock = await get_lock(
            request.app.state.valkey_runtime,
            kind="review",
            contest_id=ctx.contest.id,
            resource_id=active_judgment.id,
        )
        if review_lock is not None:
            holder = (
                await ctx.session.execute(
                    select(User).where(User.id == review_lock.holder_id, User.contest_id == ctx.contest.id)
                )
            ).scalar_one_or_none()
            if holder is not None:
                review_lock_holder_name = holder.fullname or holder.username

    is_judge = isinstance(ctx.actor, User) and ctx.actor.role == RoleEnum.JUDGE
    is_chief_judge = (
        ctx.contest.chief_judge_id is not None and getattr(ctx.actor, "id", None) == ctx.contest.chief_judge_id
    )
    is_privileged = (
        isinstance(ctx.actor, UberAdmin)
        or (isinstance(ctx.actor, User) and ctx.actor.role == RoleEnum.ADMIN)
        or is_chief_judge
    )
    can_see_test_results = isinstance(ctx.actor, UberAdmin) or ctx.actor.role in {RoleEnum.JUDGE, RoleEnum.ADMIN}

    has_confirmed = False
    if is_judge and active_judgment is not None:
        has_confirmed = any(c.judge_id == ctx.actor.id for c in active_judgment.confirmations)

    can_confirm = (
        not ctx.contest.autojudge_only
        and is_judge
        and not has_confirmed
        and active_judgment is not None
        and active_judgment.status == JudgmentStatus.DONE
        and active_judgment.final_verdict is None
        and (not lock_service_available or (review_lock is not None and review_lock.holder_id == ctx.actor.id))
    )
    can_override = (
        is_chief_judge
        and active_judgment is not None
        and active_judgment.status == JudgmentStatus.DONE
        and active_judgment.final_verdict is not None
    )
    can_rejudge = is_chief_judge and active_judgment is not None and active_judgment.final_verdict is not None

    panel = _build_confirmation_panel(active_judgment, ctx.actor, has_confirmed, is_chief_judge)
    judging_history = await get_judging_history(ctx.session, submission_id, ctx.actor, ctx.contest)
    problem_label = _label(submission.problem.ordinal)
    test_results = active_judgment.test_results if can_see_test_results and active_judgment is not None else None
    highlight_assets = submission_highlight_assets(submission.language.id)
    language_limit = next(
        (limit for limit in submission.problem.language_limits if limit.language_id == submission.language.id),
        None,
    )
    effective_problem_limits = {
        "time_limit_ms": language_limit.time_limit_ms if language_limit else submission.problem.time_limit_ms,
        "memory_limit_kb": language_limit.memory_limit_kb if language_limit else submission.problem.memory_limit_kb,
        "pids_limit": language_limit.pids_limit if language_limit else submission.problem.pids_limit,
        "output_limit_in_bytes": (
            language_limit.output_limit_in_bytes if language_limit else submission.problem.output_limit_in_bytes
        ),
        "is_override": language_limit is not None,
    }

    return _html(
        templates.TemplateResponse(
            request,
            "submissions/submission_review.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "submission": submission,
                "active_judgment": active_judgment,
                "review_lock": review_lock,
                "review_lock_holder_name": review_lock_holder_name,
                "lock_service_available": lock_service_available,
                "test_results": test_results,
                "judging_history": judging_history,
                "panel": panel,
                "is_judge": is_judge,
                "is_chief_judge": is_chief_judge,
                "is_privileged": is_privileged,
                "has_confirmed": has_confirmed,
                "can_confirm": can_confirm,
                "can_override": can_override,
                "can_rejudge": can_rejudge,
                "can_see_test_results": can_see_test_results,
                "verdicts": list(Verdict),
                "problem_label": problem_label,
                "effective_problem_limits": effective_problem_limits,
                "highlight_theme_path": "highlight/styles/github.min.css",
                "highlight_core_path": "highlight/highlight.min.js",
                "highlight_line_numbers_path": "highlight/plugins/highlightjs-line-numbers.min.js",
                **highlight_assets,
            },
        )
    )
