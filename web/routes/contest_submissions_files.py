#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.config import settings
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models import Submission, SubmissionJudgment, SubmissionTestResult, User
from web.routes.contest_admin_problem_helpers import _label
from web.routes.contest_submissions_helpers import _html, load_submission_in_contest
from web.services.judgment_utils import get_active_judgment
from web.services.problem_service import read_testcase_full

router = APIRouter(prefix="/c/{slug}/submissions", tags=["contest_submissions"])


@router.get(
    "/{submission_id}/test-cases/{test_case_id}/detail",
    response_class=HTMLResponse,
    name="submission_tc_detail",
)
async def test_case_detail(
    request: Request,
    submission_id: str,
    test_case_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> HTMLResponse:
    """Render a side-by-side view of input, expected output, and team output for a test case."""
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE))

    submission = await load_submission_in_contest(
        ctx.session,
        submission_id,
        ctx.contest.id,
        selectinload(Submission.judgments)
        .selectinload(SubmissionJudgment.test_results)
        .selectinload(SubmissionTestResult.test_case),
        selectinload(Submission.language),
        selectinload(Submission.problem),
    )
    if submission is None:
        raise HTTPException(status_code=404)

    active_judgment = get_active_judgment(submission)
    if active_judgment is None:
        raise HTTPException(status_code=404)

    tc_result = next((row for row in active_judgment.test_results if row.test_case_id == test_case_id), None)
    if tc_result is None or tc_result.test_case is None:
        raise HTTPException(status_code=404)

    ordinal = tc_result.test_case.ordinal
    tc_input, tc_expected = await anyio.to_thread.run_sync(
        lambda: read_testcase_full(submission.problem_id, ordinal, settings.PROBLEM_TESTCASE_DIR)
    )
    tc_team_output = tc_result.stdout_excerpt or ""
    problem_label = _label(submission.problem.ordinal)
    back_url = str(request.url_for("submission_review", slug=ctx.contest.login_slug, submission_id=submission_id))

    return _html(
        templates.TemplateResponse(
            request,
            "submissions/tc_detail.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "submission": submission,
                "tc_ordinal": ordinal,
                "tc_input": tc_input,
                "tc_expected": tc_expected,
                "tc_team_output": tc_team_output,
                "back_url": back_url,
                "back_label": "Submission Review",
                "problem_label": problem_label,
            },
        )
    )


@router.get("/{submission_id}/source", name="submission_source_download")
async def download_source(
    submission_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Download the submitted source code as a plain-text file."""
    ensure_allowed_role(ctx.actor, (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.TEAM))

    submission = await load_submission_in_contest(
        ctx.session,
        submission_id,
        ctx.contest.id,
        selectinload(Submission.language),
        selectinload(Submission.problem),
    )
    if submission is None:
        raise HTTPException(status_code=404)
    if isinstance(ctx.actor, User) and ctx.actor.role == RoleEnum.TEAM and submission.team_id != ctx.actor.id:
        raise HTTPException(status_code=403)

    return Response(
        content=submission.source_code.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{submission.language.source_filename}"'},
    )


@router.get(
    "/{submission_id}/test-cases/{test_case_id}/download",
    name="submission_test_case_download",
)
async def download_test_case_file(
    submission_id: str,
    test_case_id: str,
    file: str = Query(..., pattern="^(input|expected_output|team_output)$"),
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Download input, expected output, or team stdout for a single test case result."""
    ensure_allowed_role(ctx.actor, (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE))

    submission = await load_submission_in_contest(
        ctx.session,
        submission_id,
        ctx.contest.id,
        selectinload(Submission.judgments)
        .selectinload(SubmissionJudgment.test_results)
        .selectinload(SubmissionTestResult.test_case),
        selectinload(Submission.problem),
    )
    if submission is None:
        raise HTTPException(status_code=404)

    active_judgment = get_active_judgment(submission)
    if active_judgment is None:
        raise HTTPException(status_code=404)

    tc_result = next((row for row in active_judgment.test_results if row.test_case_id == test_case_id), None)
    if tc_result is None or tc_result.test_case is None:
        raise HTTPException(status_code=404)

    if file == "team_output":
        content = (tc_result.stdout_excerpt or "").encode("utf-8")
        filename = "team_output"
    else:
        ordinal = tc_result.test_case.ordinal
        tc_input, tc_expected = await anyio.to_thread.run_sync(
            lambda: read_testcase_full(submission.problem_id, ordinal, settings.PROBLEM_TESTCASE_DIR)
        )
        if file == "input":
            content = tc_input.encode("utf-8")
            filename = "input"
        else:
            content = tc_expected.encode("utf-8")
            filename = "expected_output"

    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
