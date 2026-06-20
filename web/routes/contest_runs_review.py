#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select

from shared.enumerations import RoleEnum, Verdict
from shared.queue_schema import JudgeJob, VerdictEvent
from shared.services.scoreboard_cache import invalidate_scoreboard_cache
from web.config import settings
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.problem import Problem
from web.models.users import User
from web.services.judging_service import (
    JudgmentNotDoneError,
    SameVerdictError,
    create_balloon_task_if_needed,
    override_verdict,
)
from web.services.problem_service import get_contest_languages
from web.services.submission_service import (
    DuplicateSubmissionError,
    SubmissionRateLimitError,
    create_submission,
)
from web.services.valkey_service import enqueue_job, publish_verdict

router = APIRouter(prefix="/c/{slug}/runs", tags=["contest_runs"])


@router.post("/submit", response_class=HTMLResponse, response_model=None, name="contest_runs_submit")
async def submit(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    problem_id: str = Form(""),
    language_id: str = Form(""),
    source_file: UploadFile = File(...),
) -> Response:
    ensure_allowed_role(ctx.actor, (RoleEnum.TEAM,))
    assert isinstance(ctx.actor, User)
    slug = ctx.contest.login_slug

    if not ctx.contest.is_running:
        flash("Submissions are only accepted while the contest is running.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)

    errors: list[str] = []
    if not problem_id.strip():
        errors.append("A problem must be selected.")
    if not language_id.strip():
        errors.append("A language must be selected.")
    if errors:
        for err in errors:
            flash(err, FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)

    problem_result = await ctx.session.execute(
        select(Problem).where(Problem.id == problem_id, Problem.contest_id == ctx.contest.id)
    )
    if problem_result.scalar_one_or_none() is None:
        flash("The selected problem does not belong to this contest.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)

    contest_lang_ids = {lang.id for lang in await get_contest_languages(ctx.session, ctx.contest)}
    if language_id not in contest_lang_ids:
        flash("The selected language is not available for this contest.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)

    source_bytes = await source_file.read()
    if not source_bytes:
        flash("The uploaded file is empty.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)

    max_size = ctx.contest.max_problem_file_size_bytes
    if max_size > 0 and len(source_bytes) > max_size:
        flash(f"Source file exceeds the maximum allowed size of {max_size} bytes.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)

    # Reject binary uploads (e.g. a ZIP). NUL bytes are valid UTF-8 and survive
    # decoding with errors="replace", but PostgreSQL cannot store them in text
    # columns; legitimate source files never contain them.
    if b"\x00" in source_bytes:
        flash("The uploaded file is not a text source file.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)

    source_code = source_bytes.decode("utf-8", errors="replace")
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    try:
        submission, judgment = await create_submission(
            ctx.session,
            ctx.actor,
            ctx.contest,
            problem_id=problem_id.strip(),
            language_id=language_id.strip(),
            source_code=source_code,
            source_hash=source_hash,
            source_size=len(source_bytes),
            rate_limit_window_seconds=settings.WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS,
            rate_limit_max_submissions=settings.WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS,
        )
    except SubmissionRateLimitError as exc:
        next_time = exc.next_allowed_at.astimezone().strftime("%H:%M:%S")
        flash(
            f"Submission limit reached. You can submit again after {next_time}.",
            FlashCategory.DANGER,
        )
        return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)
    except DuplicateSubmissionError:
        flash("Duplicated submission.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)

    await ctx.session.commit()
    await enqueue_job(
        request.app.state.valkey_runtime,
        JudgeJob(
            judgment_id=judgment.id,
            contest_id=str(ctx.contest.id),
            submission_id=str(submission.id),
            is_rejudge=False,
            requeue_count=0,
        ),
        priority=ctx.contest.is_running,
    )
    flash("Submission received and queued for judgment.", FlashCategory.SUCCESS)
    return RedirectResponse(url=f"/c/{slug}/runs", status_code=303)


@router.post(
    "/{submission_id}/override",
    response_class=HTMLResponse,
    response_model=None,
    name="contest_runs_override",
)
async def override_submission_verdict(
    request: Request,
    flash: FlashDep,
    submission_id: str,
    ctx: ContestContext = Depends(get_contest_context),
    new_verdict: str = Form(""),
    reason: str = Form(""),
) -> Response:
    review_url = str(request.url_for("submission_review", slug=ctx.contest.login_slug, submission_id=submission_id))
    runs_url = str(request.url_for("contest_runs", slug=ctx.contest.login_slug))

    errors: list[str] = []
    parsed_verdict: Verdict | None = None
    if not isinstance(ctx.actor, User) or ctx.actor.id != ctx.contest.chief_judge_id:
        errors.append("Only the contest chief judge may override a verdict.")
    try:
        parsed_verdict = Verdict(new_verdict)
    except ValueError:
        errors.append("Please choose a valid verdict.")

    trimmed_reason = reason.strip()
    if len(trimmed_reason) < 10:
        errors.append("Reason must contain at least 10 characters.")
    if len(reason) > 1000:
        errors.append("Reason must contain at most 1000 characters.")
    if errors:
        for error in errors:
            flash(error, FlashCategory.DANGER)
        return RedirectResponse(url=review_url, status_code=303)

    assert parsed_verdict is not None
    assert isinstance(ctx.actor, User)
    try:
        override = await override_verdict(
            ctx.session,
            submission_id,
            parsed_verdict,
            trimmed_reason,
            ctx.actor,
            ctx.contest,
        )
    except JudgmentNotDoneError:
        flash("Only DONE judgments can be overridden.", FlashCategory.DANGER)
        return RedirectResponse(url=review_url, status_code=303)
    except SameVerdictError:
        flash("The selected verdict is already the final verdict.", FlashCategory.DANGER)
        return RedirectResponse(url=review_url, status_code=303)
    except HTTPException as exc:
        if exc.status_code == 403:
            flash("Only the contest chief judge may override a verdict.", FlashCategory.DANGER)
            return RedirectResponse(url=review_url, status_code=303)
        if exc.status_code == 404:
            flash("Submission not found for this contest.", FlashCategory.DANGER)
            return RedirectResponse(url=runs_url, status_code=303)
        if exc.status_code == 400:
            flash(str(exc.detail), FlashCategory.DANGER)
            return RedirectResponse(url=review_url, status_code=303)
        raise

    is_accepted = parsed_verdict == Verdict.AC or (parsed_verdict == Verdict.PE and ctx.contest.accept_pe)
    if is_accepted:
        await create_balloon_task_if_needed(ctx.session, override.submission_id, ctx.contest)

    await ctx.session.commit()
    await publish_verdict(
        request.app.state.valkey_runtime,
        VerdictEvent(
            submission_id=override.submission_id,
            judgment_id=override.judgment_id,
            verdict=parsed_verdict.value,
            contest_id=str(ctx.contest.id),
            team_id=str(override.submission.team_id) if override.submission is not None else None,
            problem_id=str(override.submission.problem_id) if override.submission is not None else None,
            update_kind="override",
        ),
    )
    await invalidate_scoreboard_cache(request.app.state.valkey_runtime, str(ctx.contest.id))

    flash("Verdict overridden successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url=review_url, status_code=303)
