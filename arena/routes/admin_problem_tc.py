#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The single-test-case pages: view, inline edit, download, replace, reorder.

The list-level actions -- add, upload, replace-all, delete, sample toggle -- live
on the judgment-data test-cases page (:mod:`arena.routes.admin_problem_judgment`).
What is left here is everything that concerns *one* case on a page of its own,
because a case can be far too large to edit in a row.

All routes are scoped to ``/admin/problems/{problem_id}/testcases/`` and require
at least ``ARENA_JUDGE`` access. ARENA_JUDGE users may only manage test cases for
their own problems; ARENA_ADMIN users may manage any problem's test cases.
"""

from __future__ import annotations

from typing import Any, cast

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_problems import ArenaProblem, ArenaTestCase
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_form_views import build_testcase_row_views
from arena.routes.admin_problem_judgment_urls import judgment_page_url
from arena.services import admin_problem_service, admin_problem_tc_service
from arena.services.admin_problem_case_actions import ProblemVanished, apply_case_action
from arena.services.admin_problem_tc_service import check_inline_size
from shared.enumerations import ArenaRole, ProblemValidatorType
from shared.http_params import PG_INT32_MAX
from shared.services.editor_urls import editor_url
from shared.services.problem_save_errors import unsupported_strategy_message
from shared.services.testcase_files import read_testcase_full
from shared.services.testcase_pending_ops import CaseContent, PendingTestCaseOps, case_content_from_single_archive
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES, build_single_testcase_zip, parse_single_testcase_zip

router = APIRouter(prefix="/admin", tags=["arena-admin"])


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


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


async def _get_problem_or_403(
    problem_id: str,
    current_user: ArenaUser,
    session: AsyncSession,
) -> ArenaProblem:
    """Fetch a problem and enforce ownership for judges.

    Args:
        problem_id: UUID of the problem to fetch.
        current_user: The authenticated user making the request.
        session: Active async database session.

    Returns:
        ArenaProblem: The fetched problem.

    Raises:
        HTTPException: 404 if problem not found; 403 if a judge accesses another's problem.
    """
    is_admin = current_user.role == ArenaRole.ARENA_ADMIN
    problem = await admin_problem_service.get_problem(session, problem_id, caller_id=current_user.id, is_admin=is_admin)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return problem


@router.get(
    "/problems/{problem_id}/testcases/{tc_id}/edit",
    response_class=HTMLResponse,
    name="arena_admin_problem_tc_edit",
)
async def admin_problem_tc_edit(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the edit form for a single test case."""
    problem = await _get_problem_or_403(problem_id, current_user, session)
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    templates = request.app.state.arena_templates

    in_size = tc.input_size_bytes or 0
    out_size = tc.output_size_bytes or 0
    offline = in_size > MAX_INLINE_TESTCASE_BYTES or out_size > MAX_INLINE_TESTCASE_BYTES

    input_content = ""
    output_content = ""
    if not offline:
        input_content, output_content = await anyio.to_thread.run_sync(
            read_testcase_full, problem.id, tc.ordinal, settings.PROBLEM_TESTCASE_DIR
        )

    return _html(
        templates.TemplateResponse(
            request,
            "admin/problem_tc_form.html",
            {
                "problem": problem,
                "tc": tc,
                "interactive": problem.validator_type is ProblemValidatorType.INTERACTIVE,
                "offline": offline,
                "input_size_bytes": in_size,
                "output_size_bytes": out_size,
                "download_url": str(
                    request.url_for("arena_admin_problem_tc_download", problem_id=problem_id, tc_id=tc.id)
                ),
                "replace_url": str(
                    request.url_for("arena_admin_problem_tc_replace", problem_id=problem_id, tc_id=tc.id)
                ),
                "form": {
                    "input_content": input_content,
                    "output_content": output_content,
                    "explanation": tc.explanation or "",
                    "is_sample": tc.is_sample,
                },
                "back_url": editor_url(
                    request.url_for("arena_admin_problem_judgment_cases", problem_id=problem_id).path,
                    anchor=f"tc-{tc.id}",
                ),
                "current_user": current_user,
            },
        )
    )


@router.post(
    "/problems/{problem_id}/testcases/{tc_id}/edit",
    name="arena_admin_problem_tc_update",
)
async def admin_problem_tc_update(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    input_content: str = Form(""),
    output_content: str = Form(""),
    explanation: str = Form(""),
    is_sample: bool = Form(False),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Submit updates to a single test case."""
    problem = await _get_problem_or_403(problem_id, current_user, session)
    edit_url = judgment_page_url(request, problem_id)
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    explanation_value, exp_error = _clean_explanation(explanation)
    if exp_error:
        flash(exp_error, FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)
    unsupported = unsupported_strategy_message(problem.validator_type)
    if unsupported is not None:
        flash(unsupported, FlashCategory.DANGER)
        return _tab_redirect(request, problem_id)
    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    ordinal = tc.ordinal
    # An inline edit and an offline ZIP replace are the same operation on the same
    # row, so they take the same path: the case is replaced in place, in staging.
    try:
        check_inline_size(
            input_content.encode("utf-8"),
            None if interactive else output_content.encode("utf-8"),
        )
        await apply_case_action(
            session,
            problem,
            PendingTestCaseOps(
                replacements={
                    tc_id: CaseContent(
                        input_bytes=input_content.encode("utf-8"),
                        output_bytes=None if interactive else output_content.encode("utf-8"),
                        explanation=explanation_value,
                        is_sample=not interactive and is_sample,
                        # This form states both: an unchecked box makes the case
                        # secret and an emptied box clears the explanation. A
                        # replacement archive states neither and keeps them.
                        states_metadata=True,
                    )
                }
            ),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except (ValueError, ProblemVanished) as exc:
        flash(str(exc) or "Problem not found.", FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)
    flash(f"Test case #{ordinal} updated.", FlashCategory.SUCCESS)
    return RedirectResponse(url=editor_url(edit_url, anchor=f"tc-{tc_id}"), status_code=303)


@router.post(
    "/problems/{problem_id}/testcases/{tc_id}/move",
    response_class=HTMLResponse,
    name="arena_admin_problem_tc_move",
)
async def admin_problem_tc_move(
    request: Request,
    problem_id: str,
    tc_id: str,
    new_ordinal: int = Query(..., ge=1, le=PG_INT32_MAX),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Move a test case to a new ordinal and return the refreshed list partial."""
    problem = await _get_problem_or_403(problem_id, current_user, session)
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")

    unsupported = unsupported_strategy_message(problem.validator_type)
    if unsupported is not None:
        # This endpoint answers a `fetch`, not a form post: a redirect would be
        # followed and swapped into the list, so a refusal must be a status code.
        raise HTTPException(status_code=400, detail=unsupported)

    # Reordering permutes files, so it goes through staging like every other
    # file-touching action. It used to permute the live directory first and commit
    # afterwards, which left permuted files against unpermuted rows on a failure.
    current = await admin_problem_tc_service.list_testcases(session, problem.id)
    try:
        await apply_case_action(
            session,
            problem,
            PendingTestCaseOps(order=_reordered_ids(current, tc_id, new_ordinal)),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except (ValueError, ProblemVanished) as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "Problem not found.") from exc
    test_cases = await admin_problem_tc_service.list_testcase_views(session, problem.id, settings.PROBLEM_TESTCASE_DIR)
    rows = build_testcase_row_views(request, problem.id, test_cases)
    templates = request.app.state.arena_templates
    is_interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    return _html(
        templates.TemplateResponse(
            request,
            "_partials/testcase_list_table.html",
            {
                "rows": rows,
                "is_edit_allowed": True,
                "interactive": is_interactive,
            },
        )
    )


@router.get(
    "/problems/{problem_id}/testcases/{tc_id}/download",
    name="arena_admin_problem_tc_download",
)
async def admin_problem_tc_download(
    request: Request,
    problem_id: str,
    tc_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Download a single test case as a ZIP (``input.txt`` / ``output.txt``)."""
    problem = await _get_problem_or_403(problem_id, current_user, session)
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")

    input_text, output_text = await anyio.to_thread.run_sync(
        read_testcase_full, problem.id, tc.ordinal, settings.PROBLEM_TESTCASE_DIR
    )
    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    zip_bytes = build_single_testcase_zip(
        input_text.encode("utf-8"),
        None if interactive else output_text.encode("utf-8"),
        tc.explanation,
    )
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="tc-{tc.ordinal:03d}.zip"'},
    )


@router.post(
    "/problems/{problem_id}/testcases/{tc_id}/replace",
    name="arena_admin_problem_tc_replace",
)
async def admin_problem_tc_replace(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    zip_file: UploadFile = File(...),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Replace a single test case from an uploaded single-case ZIP (no size cap)."""
    problem = await _get_problem_or_403(problem_id, current_user, session)
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")

    zip_bytes = await zip_file.read()
    unsupported = unsupported_strategy_message(problem.validator_type)
    if unsupported is not None:
        flash(unsupported, FlashCategory.DANGER)
        return _tab_redirect(request, problem_id)
    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    try:
        single = parse_single_testcase_zip(zip_bytes, require_output=not interactive)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _tab_redirect(request, problem_id, tc_id)

    ordinal = tc.ordinal
    # An archive with no `explanation.txt` leaves the stored text alone rather
    # than erasing it, which is what `set_explanation` on the plan expresses.
    try:
        await apply_case_action(
            session,
            problem,
            PendingTestCaseOps(
                replacements={
                    tc_id: case_content_from_single_archive(
                        single,
                        interactive=interactive,
                        is_sample=tc.is_sample,
                    )
                }
            ),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except (ValueError, ProblemVanished) as exc:
        flash(str(exc) or "Problem not found.", FlashCategory.DANGER)
        return _tab_redirect(request, problem_id, tc_id)
    flash(f"Test case #{ordinal} replaced.", FlashCategory.SUCCESS)
    return _tab_redirect(request, problem_id, tc_id)


def _reordered_ids(test_cases: list[ArenaTestCase], tc_id: str, new_ordinal: int) -> tuple[str, ...]:
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


def _tab_redirect(request: Request, problem_id: str, tc_id: str | None = None) -> RedirectResponse:
    """Return the editor redirect these retained endpoints always answer with.

    Success or failure, the operator lands on the Test cases tab: nothing in the
    editor points here any more, so someone who reaches one of these directly
    should still end up looking at what it changed -- or did not.
    """
    url = judgment_page_url(request, problem_id)
    return RedirectResponse(
        url=url if tc_id is None else editor_url(url, anchor=f"tc-{tc_id}"),
        status_code=303,
    )
