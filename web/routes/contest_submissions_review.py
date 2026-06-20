#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum, Verdict
from shared.queue_schema import JudgeJob, VerdictEvent
from shared.services.scoreboard_cache import invalidate_scoreboard_cache
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models import Submission, SubmissionJudgment, User
from web.routes.contest_submissions_helpers import load_submission_in_contest
from web.services.judging_service import (
    AlreadyConfirmedError,
    JudgmentNotReadyError,
    NoFinalVerdictError,
    ReviewAcquisitionTimeoutError,
    ReviewAlreadyLockedError,
    ReviewLockUnavailableError,
    ReviewNotHeldByActorError,
    acquire_submission_review,
    confirm_verdict,
    create_balloon_task_if_needed,
    rejudge_submission,
    release_submission_review,
)
from web.services.judgment_utils import get_active_judgment
from web.services.valkey_service import enqueue_job, publish_verdict

router = APIRouter(prefix="/c/{slug}/submissions", tags=["contest_submissions"])


@router.post(
    "/{submission_id}/acquire-review",
    response_class=HTMLResponse,
    response_model=None,
    name="submission_acquire_review",
)
async def post_acquire_submission_review(
    request: Request,
    flash: FlashDep,
    submission_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    ensure_allowed_role(ctx.actor, (RoleEnum.JUDGE,))

    if ctx.contest.autojudge_only:
        raise HTTPException(status_code=404)
    if not isinstance(ctx.actor, User):
        raise HTTPException(status_code=403)

    review_url = str(request.url_for("submission_review", slug=ctx.contest.login_slug, submission_id=submission_id))
    runs_url = str(request.url_for("contest_runs", slug=ctx.contest.login_slug))
    submission = await load_submission_in_contest(
        ctx.session,
        submission_id,
        ctx.contest.id,
        selectinload(Submission.judgments).selectinload(SubmissionJudgment.confirmations),
        selectinload(Submission.problem),
    )
    if submission is None:
        flash("Submission not found for this contest.", FlashCategory.DANGER)
        return RedirectResponse(url=runs_url, status_code=303)

    active_judgment = get_active_judgment(submission)
    if active_judgment is None:
        flash("Submission does not have an active judgment.", FlashCategory.DANGER)
        return RedirectResponse(url=runs_url, status_code=303)

    if not request.app.state.valkey_runtime.is_available:
        flash("Lock service unavailable. Possible double work/rework.", FlashCategory.WARNING)
        return RedirectResponse(url=review_url, status_code=303)

    try:
        await acquire_submission_review(
            ctx.session,
            active_judgment,
            ctx.actor,
            ctx.contest,
            request.app.state.valkey_runtime,
        )
    except AlreadyConfirmedError:
        flash("You have already submitted a confirmation for this judgment.", FlashCategory.DANGER)
        return RedirectResponse(url=review_url, status_code=303)
    except JudgmentNotReadyError:
        flash("The autojudge has not yet finished. Please try again later.", FlashCategory.DANGER)
        return RedirectResponse(url=review_url, status_code=303)
    except ReviewAlreadyLockedError:
        flash("This submission review is already locked by another judge.", FlashCategory.WARNING)
        return RedirectResponse(url=runs_url, status_code=303)
    except ReviewLockUnavailableError:
        flash("Lock service unavailable. Possible double work/rework.", FlashCategory.WARNING)
        return RedirectResponse(url=review_url, status_code=303)

    await ctx.session.commit()
    return RedirectResponse(url=review_url, status_code=303)


@router.post(
    "/{submission_id}/release-review",
    response_class=HTMLResponse,
    response_model=None,
    name="submission_release_review",
)
async def post_release_submission_review(
    request: Request,
    flash: FlashDep,
    submission_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    ensure_allowed_role(ctx.actor, (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE))

    review_url = str(request.url_for("submission_review", slug=ctx.contest.login_slug, submission_id=submission_id))
    runs_url = str(request.url_for("contest_runs", slug=ctx.contest.login_slug))
    submission = await load_submission_in_contest(
        ctx.session,
        submission_id,
        ctx.contest.id,
        selectinload(Submission.problem),
    )
    if submission is None:
        flash("Submission not found for this contest.", FlashCategory.DANGER)
        return RedirectResponse(url=runs_url, status_code=303)

    active_judgment = get_active_judgment(submission)
    if active_judgment is None:
        flash("Submission does not have an active judgment.", FlashCategory.DANGER)
        return RedirectResponse(url=runs_url, status_code=303)

    is_chief_judge = (
        ctx.contest.chief_judge_id is not None and getattr(ctx.actor, "id", None) == ctx.contest.chief_judge_id
    )
    force = ctx.actor.role in {RoleEnum.UBERADMIN, RoleEnum.ADMIN} or is_chief_judge

    try:
        if not request.app.state.valkey_runtime.is_available:
            flash("Lock service unavailable. Possible double work/rework.", FlashCategory.WARNING)
            return RedirectResponse(url=review_url, status_code=303)
        await release_submission_review(
            ctx.session,
            active_judgment,
            ctx.actor,
            ctx.contest,
            request.app.state.valkey_runtime,
            force=force,
        )
    except ReviewNotHeldByActorError:
        flash("You do not hold this review lock.", FlashCategory.DANGER)
        return RedirectResponse(url=review_url, status_code=303)

    await ctx.session.commit()
    flash("Review lock released.", FlashCategory.SUCCESS)
    return RedirectResponse(url=runs_url, status_code=303)


@router.post(
    "/{submission_id}/confirm",
    response_class=HTMLResponse,
    response_model=None,
    name="submission_confirm_post",
)
async def post_confirm_submission_verdict(
    request: Request,
    flash: FlashDep,
    submission_id: str,
    verdict: str = Form(""),
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Submit a human verdict confirmation for a judgment."""
    ensure_allowed_role(ctx.actor, (RoleEnum.JUDGE,))

    if ctx.contest.autojudge_only:
        raise HTTPException(status_code=404)
    if not isinstance(ctx.actor, User):
        raise HTTPException(status_code=403)

    confirm_url = str(request.url_for("submission_review", slug=ctx.contest.login_slug, submission_id=submission_id))
    try:
        parsed_verdict = Verdict(verdict)
    except ValueError:
        flash("Please select a valid verdict.", FlashCategory.DANGER)
        return RedirectResponse(url=confirm_url, status_code=303)

    try:
        confirmation = await confirm_verdict(
            ctx.session,
            submission_id,
            parsed_verdict,
            ctx.actor,
            ctx.contest,
            request.app.state.valkey_runtime,
        )
    except JudgmentNotReadyError:
        flash("The autojudge has not yet finished. Please try again later.", FlashCategory.DANGER)
        return RedirectResponse(url=confirm_url, status_code=303)
    except AlreadyConfirmedError:
        flash("You have already submitted a confirmation for this judgment.", FlashCategory.DANGER)
        return RedirectResponse(url=confirm_url, status_code=303)
    except ReviewNotHeldByActorError:
        flash("You must acquire this review before confirming it.", FlashCategory.DANGER)
        return RedirectResponse(url=confirm_url, status_code=303)
    except ReviewAcquisitionTimeoutError:
        await ctx.session.commit()
        flash("Your review lock expired before confirmation. Please acquire it again.", FlashCategory.DANGER)
        return RedirectResponse(url=confirm_url, status_code=303)
    except HTTPException as exc:
        if exc.status_code == 404:
            flash("Submission not found for this contest.", FlashCategory.DANGER)
            return RedirectResponse(
                url=str(request.url_for("contest_runs", slug=ctx.contest.login_slug)),
                status_code=303,
            )
        raise

    active_judgment = confirmation.judgment
    final_verdict = active_judgment.final_verdict if active_judgment is not None else None
    judgment_id = active_judgment.id if active_judgment is not None else None
    judgment_submission_id = active_judgment.submission_id if active_judgment is not None else None

    event_team_id: str | None = None
    event_problem_id: str | None = None
    if active_judgment is not None:
        submission_for_event = await ctx.session.get(Submission, active_judgment.submission_id)
        if submission_for_event is not None:
            event_team_id = str(submission_for_event.team_id)
            event_problem_id = str(submission_for_event.problem_id)

    if final_verdict is not None:
        is_accepted = final_verdict == Verdict.AC or (final_verdict == Verdict.PE and ctx.contest.accept_pe)
        if is_accepted and judgment_submission_id is not None:
            await create_balloon_task_if_needed(ctx.session, str(judgment_submission_id), ctx.contest)

    await ctx.session.commit()

    if final_verdict is not None and judgment_id is not None and judgment_submission_id is not None:
        await publish_verdict(
            request.app.state.valkey_runtime,
            VerdictEvent(
                submission_id=str(judgment_submission_id),
                judgment_id=str(judgment_id),
                verdict=final_verdict.value,
                contest_id=str(ctx.contest.id),
                team_id=event_team_id,
                problem_id=event_problem_id,
                update_kind="confirmation",
            ),
        )
        await invalidate_scoreboard_cache(request.app.state.valkey_runtime, str(ctx.contest.id))

    flash("Confirmation submitted successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url=confirm_url, status_code=303)


@router.post(
    "/{submission_id}/rejudge",
    response_class=HTMLResponse,
    response_model=None,
    name="submission_rejudge_post",
)
async def post_rejudge_submission(
    request: Request,
    flash: FlashDep,
    submission_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Supersede the current judgment and create a new queued judgment for rejudging."""
    ensure_allowed_role(ctx.actor, (RoleEnum.JUDGE,))

    review_url = str(request.url_for("submission_review", slug=ctx.contest.login_slug, submission_id=submission_id))
    if not isinstance(ctx.actor, User) or ctx.actor.id != ctx.contest.chief_judge_id:
        flash("Only the contest chief judge may request a rejudge.", FlashCategory.DANGER)
        return RedirectResponse(url=review_url, status_code=303)

    try:
        new_judgment = await rejudge_submission(
            ctx.session,
            submission_id,
            ctx.actor,
            ctx.contest,
            request.app.state.valkey_runtime,
        )
    except NoFinalVerdictError:
        flash("Cannot rejudge: the submission does not have a final verdict yet.", FlashCategory.DANGER)
        return RedirectResponse(url=review_url, status_code=303)
    except HTTPException as exc:
        if exc.status_code == 404:
            flash("Submission not found for this contest.", FlashCategory.DANGER)
            return RedirectResponse(
                url=str(request.url_for("contest_runs", slug=ctx.contest.login_slug)),
                status_code=303,
            )
        raise

    await ctx.session.commit()
    await enqueue_job(
        request.app.state.valkey_runtime,
        JudgeJob(
            judgment_id=new_judgment.id,
            contest_id=str(ctx.contest.id),
            submission_id=str(new_judgment.submission_id),
            is_rejudge=True,
            requeue_count=0,
        ),
        priority=True,
    )
    await invalidate_scoreboard_cache(request.app.state.valkey_runtime, str(ctx.contest.id))

    flash("Submission queued for rejudging.", FlashCategory.SUCCESS)
    return RedirectResponse(url=review_url, status_code=303)
