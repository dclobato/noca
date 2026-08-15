#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Contest editor's dedicated per-test-case page.

One form: the page for editing one existing case, which is also the offline
round-trip's landing place for a case too large to edit inline. The page for
typing a *new* case retired with the split -- new cases are typed as rows on the
judgment-data test-cases page and saved there.
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from shared.enumerations import ProblemValidatorType
from shared.services.editor_urls import editor_url
from shared.services.testcase_files import read_testcase_sizes
from shared.tc_zip import (
    MAX_INLINE_TESTCASE_BYTES,
)
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_problem_helpers import (
    _html,
    _is_edit_allowed,
    _read_testcase_full_for,
)
from web.services.problem_service import (
    get_problem_in_contest,
)

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


@router.get("/{problem_id}/test-cases/{tc_id}/edit", response_class=HTMLResponse, name="edit_test_case_form")
async def edit_test_case_form(
    request: Request,
    problem_id: str,
    tc_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")
    tc = next((t for t in problem.test_cases if t.id == tc_id), None)
    if tc is None:
        raise Exception("Test case not found")

    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    in_size: int = tc.input_size_bytes or 0
    out_size: int = tc.output_size_bytes or 0
    # An interactive case has no expected output, so a null output size is the
    # normal state there rather than a pre-backfill row to read off disk.
    if tc.input_size_bytes is None or (tc.output_size_bytes is None and not interactive):
        in_size, out_size = await anyio.to_thread.run_sync(read_testcase_sizes, problem.id, tc.ordinal, testcase_dir)
    offline = in_size > MAX_INLINE_TESTCASE_BYTES or out_size > MAX_INLINE_TESTCASE_BYTES

    tc_in = ""
    tc_out = ""
    if not offline:
        tc_in, tc_out = await anyio.to_thread.run_sync(_read_testcase_full_for(problem.id, tc.ordinal, testcase_dir))
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/testcase_edit.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem": problem,
                "tc": tc,
                "interactive": problem.validator_type is ProblemValidatorType.INTERACTIVE,
                "offline": offline,
                "input_size_bytes": in_size,
                "output_size_bytes": out_size,
                "download_url": str(
                    request.url_for(
                        "download_test_case", slug=ctx.contest.login_slug, problem_id=problem.id, tc_id=tc.id
                    )
                ),
                "replace_url": str(
                    request.url_for(
                        "replace_test_case", slug=ctx.contest.login_slug, problem_id=problem.id, tc_id=tc.id
                    )
                ),
                "form_data": {
                    "tc_in": tc_in,
                    "tc_out": tc_out,
                    "is_sample": tc.is_sample,
                    "explanation": tc.explanation or "",
                },
                "errors": [],
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
                "back_url": editor_url(
                    request.url_for(
                        "problem_judgment_cases",
                        slug=ctx.contest.login_slug,
                        problem_id=problem.id,
                    ).path,
                    anchor=f"tc-{tc.id}",
                ),
            },
        )
    )


# ---------------------------------------------------------------------------
# Add / edit / remove / move test cases
# ---------------------------------------------------------------------------
