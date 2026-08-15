#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The single-test-case actions: inline edit, download, replace, reorder.

The list-level actions -- add, upload, replace-all, delete, sample toggle -- live
on the judgment-data test-cases page
(:mod:`web.routes.contest_admin_problem_judgment_tc`). What is left here is
everything that concerns *one* case, which gets a page of its own because a case
can be far too large to edit in a row.
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import ProblemValidatorType
from shared.http_params import PG_INT32_MAX
from shared.services.custom_validator import status_view
from shared.services.editor_urls import editor_url
from shared.services.problem_save_errors import PendingOpsError, unsupported_strategy_message
from shared.services.testcase_files import read_testcase_full
from shared.services.testcase_pending_ops import CaseContent, PendingTestCaseOps, case_content_from_single_archive
from shared.tc_zip import (
    MAX_INLINE_TESTCASE_BYTES,
    build_single_testcase_zip,
    inline_oversized_side,
    parse_single_testcase_zip,
)
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.problem import ProblemTestCase
from web.routes.contest_admin_problem_helpers import (
    _html,
    _is_edit_allowed,
    _read_testcase_preview_for,
    _redirect,
    build_testcase_row_views,
)
from web.routes.contest_admin_problem_judgment_urls import judgment_page_url
from web.services.problem_case_actions import ProblemVanished, apply_case_action
from web.services.problem_service import (
    get_problem_in_contest,
)

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


def _inline_size_error(tc_in: str, tc_out: str) -> str | None:
    """Return an error message if either inline side exceeds the gate threshold."""
    limit_kb = MAX_INLINE_TESTCASE_BYTES // 1024
    oversized = inline_oversized_side(tc_in.encode("utf-8"), tc_out.encode("utf-8"))
    if oversized == "input":
        return f"Input exceeds the {limit_kb} KB inline-edit limit; edit this case offline via download/replace."
    if oversized == "output":
        return f"Output exceeds the {limit_kb} KB inline-edit limit; edit this case offline via download/replace."
    return None


def _clean_explanation(raw: str) -> tuple[str | None, str | None]:
    """Normalize a submitted explanation value.

    Args:
        raw: Raw form value.

    Returns:
        tuple[str | None, str | None]: ``(value, error_message)``. ``value`` is
        the stripped text or ``None`` when blank; ``error_message`` is always
        ``None`` (the explanation length is unbounded) and kept for a stable
        call-site signature.
    """
    text = raw.strip()
    return (text or None), None


def _testcase_edit_return_url(request: Request, slug: str, problem_id: str, tc_id: str) -> str:
    """Build the problem edit URL anchored to an edited test-case row."""
    return editor_url(judgment_page_url(request, slug, problem_id), anchor=f"tc-{tc_id}")


def _testcase_file_ordinal_map(test_cases: list[ProblemTestCase], tc_id: str, new_ordinal: int) -> dict[int, int]:
    """Map current testcase file ordinals to final ordinals for a move."""
    ordered_test_cases = sorted(test_cases, key=lambda item: (item.ordinal, item.id))
    current_index = next((index for index, item in enumerate(ordered_test_cases) if item.id == tc_id), None)
    if current_index is None:
        raise ValueError("Test case not found.")

    moving_test_case = ordered_test_cases.pop(current_index)
    destination_index = max(0, min(new_ordinal - 1, len(ordered_test_cases)))
    ordered_test_cases.insert(destination_index, moving_test_case)
    return {item.ordinal: ordinal for ordinal, item in enumerate(ordered_test_cases, start=1)}


def _reordered_ids(test_cases: list[ProblemTestCase], tc_id: str, new_ordinal: int) -> tuple[str, ...]:
    """Return every case id in the order a move leaves them in.

    Expressing a move as the resulting *order* rather than as a file permutation
    is what lets it share the staged swap with every other action: the planner
    renumbers, and ``materialize`` performs the renames inside staging.

    Raises:
        ValueError: If ``tc_id`` names no case of this problem.
    """
    ordered = sorted(test_cases, key=lambda item: (item.ordinal, item.id))
    current_index = next((index for index, item in enumerate(ordered) if item.id == tc_id), None)
    if current_index is None:
        raise ValueError("Test case not found.")
    moving = ordered.pop(current_index)
    ordered.insert(max(0, min(new_ordinal - 1, len(ordered))), moving)
    return tuple(item.id for item in ordered)


# ---------------------------------------------------------------------------
# Upload test case ZIP (replaces all existing)
# ---------------------------------------------------------------------------


@router.post("/{problem_id}/test-cases/{tc_id}/edit", name="edit_test_case")
async def edit_test_case(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    tc_in: str = Form(""),
    tc_out: str = Form(""),
    explanation: str = Form(""),
    is_sample: str | None = Form(None),
) -> RedirectResponse:
    edit_url = judgment_page_url(request, ctx.contest.login_slug, problem_id)
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(edit_url)

    explanation_value, exp_error = _clean_explanation(explanation)
    if exp_error:
        flash(exp_error, FlashCategory.DANGER)
        return _redirect(edit_url)

    size_error = _inline_size_error(tc_in, tc_out)
    if size_error:
        flash(size_error, FlashCategory.DANGER)
        return _redirect(edit_url)

    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(edit_url)

    tc = next((t for t in problem.test_cases if t.id == tc_id), None)
    if tc is None:
        flash("Test case not found.", FlashCategory.DANGER)
        return _redirect(edit_url)

    unsupported = unsupported_strategy_message(problem.validator_type)
    if unsupported is not None:
        flash(unsupported, FlashCategory.DANGER)
        return _redirect(edit_url)
    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    # An inline edit and an offline ZIP replace are the same operation on the same
    # row, so they take the same path: the case is replaced in place, in staging.
    # They differ in one respect the plan needs told: this form *states* the
    # sample flag and the explanation, so an unchecked box makes the case secret
    # and an emptied box clears the text. An archive states neither.
    ops = PendingTestCaseOps(
        replacements={
            tc_id: CaseContent(
                input_bytes=tc_in.encode(),
                output_bytes=None if interactive else tc_out.encode(),
                explanation=explanation_value,
                is_sample=not interactive and is_sample is not None,
                states_metadata=True,
            )
        },
        sample_toggles=frozenset(),
    )
    try:
        await apply_case_action(ctx.session, problem, ops, testcase_dir=settings.PROBLEM_TESTCASE_DIR)
    except ProblemVanished:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(edit_url)
    except PendingOpsError as exc:
        # The action named a case that was deleted before it could take the
        # lock. A stated refusal, not a 500.
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(edit_url)
    flash("Test case updated successfully.", FlashCategory.SUCCESS)
    return _redirect(_testcase_edit_return_url(request, ctx.contest.login_slug, problem_id, tc_id))


@router.post(
    "/{problem_id}/test-cases/{tc_id}/move",
    response_class=HTMLResponse,
    response_model=None,
    name="move_test_case_route",
)
async def move_test_case_route(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    direction: str | None = Query(None),
    new_ordinal: int | None = Query(None, le=PG_INT32_MAX),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse | RedirectResponse:
    edit_url = judgment_page_url(request, ctx.contest.login_slug, problem_id)
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(edit_url)

    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(edit_url)

    tc = next((t for t in problem.test_cases if t.id == tc_id), None)
    if tc is None:
        flash("Test case not found.", FlashCategory.DANGER)
        return _redirect(edit_url)

    if new_ordinal is None:
        if direction == "up":
            new_ordinal = tc.ordinal - 1
        elif direction == "down":
            new_ordinal = tc.ordinal + 1
        else:
            raise HTTPException(status_code=400, detail="Provide new_ordinal or direction=up|down.")

    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    unsupported = unsupported_strategy_message(problem.validator_type)
    if unsupported is not None:
        flash(unsupported, FlashCategory.DANGER)
        return _redirect(edit_url)

    # Reordering permutes files, so it goes through staging like every other
    # file-touching action. It used to permute the live directory first and commit
    # afterwards, which left permuted files against unpermuted rows on a failure.
    try:
        await apply_case_action(
            ctx.session,
            problem,
            PendingTestCaseOps(order=_reordered_ids(list(problem.test_cases), tc.id, new_ordinal)),
            testcase_dir=testcase_dir,
        )
    except ProblemVanished:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(edit_url)
    except PendingOpsError as exc:
        # The action named a case that was deleted before it could take the
        # lock. A stated refusal, not a 500.
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(edit_url)

    # Reload and return the HTMX partial
    templates = request.app.state.templates
    await ctx.session.refresh(problem, attribute_names=["test_cases"])
    testcase_previews: dict[str, tuple[str, str]] = {}
    for t in problem.test_cases:
        preview = await anyio.to_thread.run_sync(_read_testcase_preview_for(problem.id, t.ordinal, testcase_dir))
        testcase_previews[t.id] = preview

    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/testcases_table.html",
            {
                "request": request,
                "contest": ctx.contest,
                "current_user": ctx.actor,
                "problem": problem,
                "rows": build_testcase_row_views(request, ctx.contest, list(problem.test_cases), testcase_previews),
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
                "validator_status": status_view(problem.custom_validator),
                "is_interactive": problem.validator_type is ProblemValidatorType.INTERACTIVE,
            },
        )
    )


# ---------------------------------------------------------------------------
# Single test-case download / replace (offline round-trip for large cases)
# ---------------------------------------------------------------------------


@router.get("/{problem_id}/test-cases/{tc_id}/download", name="download_test_case")
async def download_test_case(
    request: Request,
    problem_id: str,
    tc_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    """Download a single test case as a ZIP (``input.txt`` / ``output.txt``)."""
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    tc = next((t for t in problem.test_cases if t.id == tc_id), None)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")

    tc_in, tc_out = await anyio.to_thread.run_sync(
        read_testcase_full, problem.id, tc.ordinal, settings.PROBLEM_TESTCASE_DIR
    )
    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    zip_bytes = build_single_testcase_zip(
        tc_in.encode("utf-8"),
        None if interactive else tc_out.encode("utf-8"),
        tc.explanation,
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="tc-{tc.ordinal:03d}.zip"'},
    )


@router.post("/{problem_id}/test-cases/{tc_id}/replace", name="replace_test_case")
async def replace_test_case(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    zip_file: UploadFile = File(...),
) -> RedirectResponse:
    """Replace a single test case from a single-case ZIP upload (no size cap)."""
    # Every outcome returns to the Test cases tab, success or failure: this
    # endpoint is no longer reachable from the editor, and an operator who reaches
    # it directly should still land on the list it changed -- or did not.
    tc_edit_url = _tab_url(request, ctx.contest.login_slug, problem_id, tc_id)
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(tc_edit_url)

    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(tc_edit_url)
    tc = next((t for t in problem.test_cases if t.id == tc_id), None)
    if tc is None:
        flash("Test case not found.", FlashCategory.DANGER)
        return _redirect(tc_edit_url)

    zip_bytes = await zip_file.read()
    unsupported = unsupported_strategy_message(problem.validator_type)
    if unsupported is not None:
        flash(unsupported, FlashCategory.DANGER)
        return _redirect(tc_edit_url)
    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    try:
        single = parse_single_testcase_zip(zip_bytes, require_output=not interactive)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(tc_edit_url)

    ordinal = tc.ordinal
    # An archive with no `explanation.txt` leaves the stored text alone rather
    # than erasing it, which is what `set_explanation` on the plan expresses.
    ops = PendingTestCaseOps(
        replacements={
            tc_id: case_content_from_single_archive(
                single,
                interactive=interactive,
                is_sample=tc.is_sample,
            )
        }
    )
    try:
        await apply_case_action(ctx.session, problem, ops, testcase_dir=settings.PROBLEM_TESTCASE_DIR)
    except ProblemVanished:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(tc_edit_url)
    except PendingOpsError as exc:
        # The action named a case that was deleted before it could take the
        # lock. A stated refusal, not a 500.
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(tc_edit_url)
    flash(f"Test case #{ordinal} replaced.", FlashCategory.SUCCESS)
    return _redirect(tc_edit_url)


def _tab_url(request: Request, slug: str, problem_id: str, tc_id: str | None = None) -> str:
    """Return the editor URL these retained endpoints send the operator back to.

    Every outcome, success or failure, lands on the Test cases tab: these routes
    are no longer reachable from the editor UI, and an operator who reaches one
    directly should still end up looking at what it changed -- or did not.
    """
    url = judgment_page_url(request, slug, problem_id)
    return url if tc_id is None else editor_url(url, anchor=f"tc-{tc_id}")
