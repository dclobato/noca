#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Contest judgment-data test-cases page and its actions.

Judgment data is edited on its own pages rather than in a pane of the problem
form, because a problem can hold many large test cases and loading them into a
document that mostly hides them helps nobody.

Everything here applies immediately, through the staged swap that keeps rows and
files in step (:mod:`web.services.problem_case_actions`). The one exception is
the rows an author types inline: those are the only state the page holds that the
server has not seen, and they are submitted by this page's own Save.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select

from shared.enumerations import ProblemValidatorType
from shared.services.editor_urls import editor_url
from shared.services.problem_editor_save import lock_problem_row
from shared.services.problem_package.testcase_archive import parse_testcases_zip
from shared.services.problem_save_errors import PendingOpsError, unsupported_strategy_message
from shared.services.public_export_generation import bump_public_export_generation
from shared.services.testcase_pending_ops import (
    CaseContent,
    PendingTestCaseOps,
    SubmittedCaseRow,
    case_content_from_single_archive,
    case_contents_from_bulk_archive,
    first_oversized_submitted_case,
    parse_inline_added_cases,
    submitted_case_rows,
)
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES, parse_single_testcase_zip
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.problem import Problem, ProblemTestCase
from web.routes.contest_admin_problem_helpers import _html, _is_edit_allowed, _redirect
from web.routes.contest_admin_problem_judgment_urls import judgment_page_url
from web.routes.contest_admin_problem_judgment_view import build_judgment_context
from web.services.problem_case_actions import ProblemVanished, apply_case_action
from web.services.problem_service import get_problem_in_contest

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


@router.get("/{problem_id}/judgment", name="problem_judgment_home")
async def problem_judgment_home(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    """Send the author to the page they most likely want.

    An interactive problem with no validator cannot judge anything whatever its
    test cases look like, so that is the page to open first.
    """
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")
    slug = ctx.contest.login_slug
    if problem.validator_type is ProblemValidatorType.INTERACTIVE and problem.custom_validator is None:
        return _redirect(judgment_page_url(request, slug, problem_id, "validator"))
    return _redirect(judgment_page_url(request, slug, problem_id, "test-cases"))


@router.get("/{problem_id}/judgment/test-cases", response_class=HTMLResponse, name="problem_judgment_cases")
async def problem_judgment_cases(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    """Render the test-case list, its inline add rows, and the upload controls."""
    templates = request.app.state.templates
    context = await build_judgment_context(request, ctx, problem_id, active_page="test-cases")
    return _html(templates.TemplateResponse(request, "admin/problems/judgment_cases.html", context))


@router.post("/{problem_id}/judgment/test-cases", name="problem_judgment_cases_save")
async def problem_judgment_cases_save(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    """Add the rows typed inline on the page.

    This is the page's only deferred action, because typed text is the only thing
    the browser holds that the server has not seen.
    """
    page_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "test-cases")
    problem = await _editable_problem(ctx, problem_id, flash)
    if problem is None:
        return _redirect(page_url)

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    form = await request.form()
    submitted = submitted_case_rows(form)
    added = parse_inline_added_cases(form, interactive=interactive)
    if not added:
        return await _render_rejected_cases(
            request,
            ctx,
            problem_id,
            submitted,
            "Nothing to add: type a test case first.",
        )

    oversized = first_oversized_submitted_case(submitted, interactive=interactive)
    if oversized is not None:
        index, side = oversized
        limit_kb = MAX_INLINE_TESTCASE_BYTES // 1024
        message = f"{side.capitalize()} exceeds the {limit_kb} KB inline limit; upload this case as a ZIP instead."
        errored = tuple(
            replace(row, error=message, error_field=side) if row.index == index else row for row in submitted
        )
        return await _render_rejected_cases(
            request,
            ctx,
            problem_id,
            errored,
            "Correct the highlighted test case and save again.",
        )

    try:
        await apply_case_action(
            ctx.session,
            problem,
            PendingTestCaseOps(added=added),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except ProblemVanished:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    except PendingOpsError as exc:
        return await _render_rejected_cases(
            request,
            ctx,
            problem_id,
            submitted,
            str(exc),
        )

    flash(f"{len(added)} test case(s) added.", FlashCategory.SUCCESS)
    return _redirect(page_url)


@router.post("/{problem_id}/judgment/test-cases/upload", name="problem_judgment_case_upload")
async def problem_judgment_case_upload(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    tc_add_zip: list[UploadFile] = File(default=[]),
) -> RedirectResponse:
    """Append one or more cases from single-case archives."""
    page_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "test-cases")
    problem = await _editable_problem(ctx, problem_id, flash)
    if problem is None:
        return _redirect(page_url)

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    added: list[CaseContent] = []
    for position, upload in enumerate(tc_add_zip, start=1):
        if not upload.filename:
            continue
        try:
            single = parse_single_testcase_zip(await upload.read(), require_output=not interactive)
        except ValueError as exc:
            flash(f"Test case ZIP #{position}: {exc}", FlashCategory.DANGER)
            return _redirect(page_url)
        added.append(case_content_from_single_archive(single, interactive=interactive))

    if not added:
        flash("Choose at least one ZIP to upload.", FlashCategory.WARNING)
        return _redirect(page_url)

    try:
        await apply_case_action(
            ctx.session,
            problem,
            PendingTestCaseOps(added=tuple(added)),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except ProblemVanished:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    except PendingOpsError as exc:
        # The action named a case that was deleted before it could take the
        # lock. A stated refusal, not a 500.
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(page_url)

    flash(f"{len(added)} test case(s) added from ZIP.", FlashCategory.SUCCESS)
    return _redirect(page_url)


@router.post("/{problem_id}/judgment/test-cases/bulk", name="problem_judgment_cases_replace_all")
async def problem_judgment_cases_replace_all(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    tc_bulk_zip: UploadFile = File(...),
) -> RedirectResponse:
    """Replace every test case from one archive."""
    page_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "test-cases")
    problem = await _editable_problem(ctx, problem_id, flash)
    if problem is None:
        return _redirect(page_url)

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    try:
        parsed = parse_testcases_zip(await tc_bulk_zip.read(), require_output=not interactive)
    except ValueError as exc:
        flash(f"Test case ZIP error: {exc}", FlashCategory.DANGER)
        return _redirect(page_url)

    cases = case_contents_from_bulk_archive(parsed, interactive=interactive)
    try:
        await apply_case_action(
            ctx.session,
            problem,
            PendingTestCaseOps(bulk_cases=cases),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except ProblemVanished:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    except PendingOpsError as exc:
        # The action named a case that was deleted before it could take the
        # lock. A stated refusal, not a 500.
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(page_url)

    flash(f"Test cases replaced: {len(cases)} case(s) imported.", FlashCategory.SUCCESS)
    return _redirect(page_url)


@router.post("/{problem_id}/judgment/test-cases/{tc_id}/toggle-sample", name="problem_judgment_case_toggle_sample")
async def problem_judgment_case_toggle_sample(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    """Flip one case between sample and secret.

    This changes no file, so it commits directly rather than opening a swap:
    staging a whole directory to change a boolean would copy the problem's entire
    test data. The row lock still applies, so it cannot interleave with an action
    that is staging that directory.
    """
    page_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "test-cases")
    problem = await _editable_problem(ctx, problem_id, flash)
    if problem is None:
        return _redirect(page_url)
    if problem.validator_type is ProblemValidatorType.INTERACTIVE:
        flash(
            "Interactive problems present sample interactions instead of sample test cases.",
            FlashCategory.DANGER,
        )
        return _redirect(page_url)

    if not await lock_problem_row(ctx.session, "contest", problem.id):
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    # Read the case *after* the lock, and flip the value it holds now. Deciding
    # from the value loaded before the lock makes two concurrent toggles collapse
    # into one -- both read "secret", both write "sample" -- and would flip a case
    # another request had already deleted. `populate_existing` because the row is
    # already in the identity map, loaded with the problem, and would otherwise
    # come back with its pre-lock values.
    test_case = (
        await ctx.session.execute(
            select(ProblemTestCase)
            .where(ProblemTestCase.id == tc_id, ProblemTestCase.problem_id == problem.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if test_case is None:
        flash("That test case no longer exists; reload the page and try again.", FlashCategory.DANGER)
        return _redirect(page_url)

    test_case.is_sample = not test_case.is_sample
    kind = "sample" if test_case.is_sample else "secret"
    ordinal = test_case.ordinal
    # A sample/secret flip changes which cases the public package ships.
    await bump_public_export_generation(ctx.session, "contest", problem.id)
    await ctx.session.commit()
    flash(f"Test case #{ordinal} is now a {kind} case.", FlashCategory.SUCCESS)
    return _redirect(editor_url(page_url, anchor=f"tc-{tc_id}"))


@router.post("/{problem_id}/judgment/test-cases/{tc_id}/replace", name="problem_judgment_case_replace")
async def problem_judgment_case_replace(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    zip_file: UploadFile = File(...),
) -> RedirectResponse:
    """Replace one case from a single-case archive, with no size cap."""
    page_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "test-cases")
    problem = await _editable_problem(ctx, problem_id, flash)
    if problem is None:
        return _redirect(page_url)

    test_case = next((item for item in problem.test_cases if item.id == tc_id), None)
    if test_case is None:
        flash("Test case not found.", FlashCategory.DANGER)
        return _redirect(page_url)

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    try:
        single = parse_single_testcase_zip(await zip_file.read(), require_output=not interactive)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(page_url)

    ordinal = test_case.ordinal
    was_sample = test_case.is_sample
    try:
        await apply_case_action(
            ctx.session,
            problem,
            PendingTestCaseOps(
                replacements={
                    tc_id: case_content_from_single_archive(
                        single,
                        interactive=interactive,
                        is_sample=was_sample,
                    )
                }
            ),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except ProblemVanished:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    except PendingOpsError as exc:
        # The action named a case that was deleted before it could take the
        # lock. A stated refusal, not a 500.
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(page_url)

    flash(f"Test case #{ordinal} replaced.", FlashCategory.SUCCESS)
    return _redirect(editor_url(page_url, anchor=f"tc-{tc_id}"))


@router.post("/{problem_id}/judgment/test-cases/{tc_id}/delete", name="problem_judgment_case_delete")
async def problem_judgment_case_delete(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    """Delete one case and close the ordinal gap it leaves."""
    page_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "test-cases")
    problem = await _editable_problem(ctx, problem_id, flash)
    if problem is None:
        return _redirect(page_url)

    test_case = next((item for item in problem.test_cases if item.id == tc_id), None)
    if test_case is None:
        flash("Test case not found.", FlashCategory.DANGER)
        return _redirect(page_url)

    # Removing the last case leaves an incomplete draft, which is allowed: the
    # problem simply stops being judgeable until a case is added back.
    ordinal = test_case.ordinal
    try:
        await apply_case_action(
            ctx.session,
            problem,
            PendingTestCaseOps(removals=frozenset({tc_id})),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except ProblemVanished:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    except PendingOpsError as exc:
        # The action named a case that was deleted before it could take the
        # lock. A stated refusal, not a 500.
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(page_url)

    flash(f"Test case #{ordinal} removed.", FlashCategory.SUCCESS)
    return _redirect(page_url)


async def _render_rejected_cases(
    request: Request,
    ctx: ContestAdminContext,
    problem_id: str,
    rows: tuple[SubmittedCaseRow, ...],
    message: str,
) -> HTMLResponse:
    """Render submitted testcase rows beside their validation error."""
    context = await build_judgment_context(request, ctx, problem_id, active_page="test-cases")
    context |= {
        "submitted_case_rows": rows,
        "pending_error": message,
        "pending_next_index": max((row.index for row in rows), default=-1) + 1,
    }
    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/judgment_cases.html",
            context,
            status_code=422,
        )
    )


async def _editable_problem(
    ctx: ContestAdminContext,
    problem_id: str,
    flash: FlashDep,
) -> Problem | None:
    """Load the problem and refuse every reason it may not be edited.

    Returns:
        The problem, or ``None`` when a flash has been set and the caller should
        redirect: the contest is not editable, the problem is gone, or its stored
        strategy is one this build cannot judge.
    """
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return None
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return None
    unsupported = unsupported_strategy_message(problem.validator_type)
    if unsupported is not None:
        flash(unsupported, FlashCategory.DANGER)
        return None
    return problem
