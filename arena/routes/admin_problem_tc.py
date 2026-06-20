#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin routes for test-case management.

All routes are scoped to ``/admin/problems/{problem_id}/testcases/`` and require
at least ``ARENA_JUDGE`` access.  ARENA_JUDGE users may only manage test cases
for their own problems; ARENA_ADMIN users may manage any problem's test cases.
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
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_form_views import build_testcase_row_views
from arena.services import admin_problem_service, admin_problem_tc_service
from shared.enumerations import ArenaRole
from shared.services.testcase_files import read_testcase_full
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


@router.post(
    "/problems/{problem_id}/testcases/add",
    name="arena_admin_problem_tc_add",
)
async def admin_problem_tc_add(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    input_content: str = Form(""),
    output_content: str = Form(""),
    explanation: str = Form(""),
    is_sample: bool = Form(False),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Append a single test case to a problem.

    Redirects back to the problem edit page with a flash message.
    """
    problem = await _get_problem_or_403(problem_id, current_user, session)
    edit_url = str(request.url_for("arena_admin_problem_edit", problem_id=problem_id))
    explanation_value, exp_error = _clean_explanation(explanation)
    if exp_error:
        flash(exp_error, FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)
    try:
        _tc, write_files = await admin_problem_tc_service.create_testcase(
            session,
            problem,
            input_content=input_content,
            output_content=output_content,
            is_sample=is_sample,
            explanation=explanation_value,
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)
    await session.commit()
    await anyio.to_thread.run_sync(write_files)
    flash("Test case added.", FlashCategory.SUCCESS)
    return RedirectResponse(url=edit_url, status_code=303)


@router.post(
    "/problems/{problem_id}/testcases/add-zip",
    name="arena_admin_problem_tc_add_from_zip",
)
async def admin_problem_tc_add_from_zip(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    zip_file: UploadFile = File(...),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Add a single new test case from an uploaded single-case ZIP.

    Args:
        request: The incoming HTTP request.
        problem_id: UUID of the problem to add the test case to.
        flash: Flash message dependency.
        zip_file: The uploaded ZIP file (``input.txt`` + ``output.txt``).
        current_user: The authenticated Arena user.
        session: Active async database session.

    Returns:
        Response: Redirect to the problem edit page.
    """
    problem = await _get_problem_or_403(problem_id, current_user, session)
    edit_url = str(request.url_for("arena_admin_problem_edit", problem_id=problem_id))
    zip_bytes = await zip_file.read()
    try:
        single = parse_single_testcase_zip(zip_bytes)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)
    try:
        _tc, write_files = await admin_problem_tc_service.create_testcase(
            session,
            problem,
            input_content=single.input_bytes.decode("utf-8"),
            output_content=single.output_bytes.decode("utf-8"),
            is_sample=False,
            explanation=single.explanation,
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)
    await session.commit()
    await anyio.to_thread.run_sync(write_files)
    flash("Test case added from ZIP.", FlashCategory.SUCCESS)
    return RedirectResponse(url=edit_url, status_code=303)


@router.get(
    "/problems/{problem_id}/testcases/new",
    response_class=HTMLResponse,
    name="arena_admin_problem_tc_new",
)
async def admin_problem_tc_new(
    request: Request,
    problem_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the form for a new test case."""
    problem = await _get_problem_or_403(problem_id, current_user, session)
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problem_tc_form.html",
            {
                "problem": problem,
                "tc": None,
                "form": {
                    "input_content": "",
                    "output_content": "",
                    "explanation": "",
                    "is_sample": False,
                },
                "back_url": str(request.url_for("arena_admin_problem_edit", problem_id=problem_id)),
                "current_user": current_user,
            },
        )
    )


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
                "back_url": str(request.url_for("arena_admin_problem_edit", problem_id=problem_id)),
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
    edit_url = str(request.url_for("arena_admin_problem_edit", problem_id=problem_id))
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    explanation_value, exp_error = _clean_explanation(explanation)
    if exp_error:
        flash(exp_error, FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)
    try:
        _tc, write_files = await admin_problem_tc_service.update_testcase(
            session,
            tc,
            input_content=input_content,
            output_content=output_content,
            is_sample=is_sample,
            explanation=explanation_value,
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)
    await session.commit()
    await anyio.to_thread.run_sync(write_files)
    flash(f"Test case #{tc.ordinal} updated.", FlashCategory.SUCCESS)
    return RedirectResponse(url=edit_url, status_code=303)


@router.post(
    "/problems/{problem_id}/testcases/{tc_id}/toggle-sample",
    name="arena_admin_problem_tc_toggle_sample",
)
async def admin_problem_tc_toggle_sample(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Flip a test case between sample and secret and return to the edit page.

    Redirects to the problem edit page anchored to the affected row so the
    row-highlight pattern highlights it.
    """
    problem = await _get_problem_or_403(problem_id, current_user, session)
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    await admin_problem_tc_service.toggle_sample(session, tc)
    await session.commit()
    kind = "sample" if tc.is_sample else "secret"
    flash(f"Test case #{tc.ordinal} is now a {kind} case.", FlashCategory.SUCCESS)
    edit_url = str(request.url_for("arena_admin_problem_edit", problem_id=problem_id))
    return RedirectResponse(url=f"{edit_url}#tc-{tc_id}", status_code=303)


@router.post(
    "/problems/{problem_id}/testcases/{tc_id}/move",
    response_class=HTMLResponse,
    name="arena_admin_problem_tc_move",
)
async def admin_problem_tc_move(
    request: Request,
    problem_id: str,
    tc_id: str,
    new_ordinal: int = Query(..., ge=1),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Move a test case to a new ordinal and return the refreshed list partial."""
    problem = await _get_problem_or_403(problem_id, current_user, session)
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")

    await admin_problem_tc_service.move_testcase(session, tc, new_ordinal, testcase_dir=settings.PROBLEM_TESTCASE_DIR)
    await session.commit()
    test_cases = await admin_problem_tc_service.list_testcase_views(session, problem.id, settings.PROBLEM_TESTCASE_DIR)
    rows = build_testcase_row_views(request, problem.id, test_cases)
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "_partials/testcase_list_table.html",
            {
                "rows": rows,
                "is_edit_allowed": True,
            },
        )
    )


@router.post(
    "/problems/{problem_id}/testcases/zip-replace",
    name="arena_admin_problem_tc_zip_replace",
)
async def admin_problem_tc_zip_replace(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    zip_file: UploadFile = File(...),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Replace all test cases for a problem from an uploaded ZIP archive.

    The ZIP must follow the standard format:
    ``in/001.in`` + ``out/001.out`` or flat ``001.in`` + ``001.out``.
    """
    problem = await _get_problem_or_403(problem_id, current_user, session)
    zip_bytes = await zip_file.read()
    try:
        count, apply_files = await admin_problem_tc_service.replace_all_from_zip(
            session, problem, zip_bytes, testcase_dir=settings.PROBLEM_TESTCASE_DIR
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(
            url=str(request.url_for("arena_admin_problem_edit", problem_id=problem_id)),
            status_code=303,
        )
    await session.commit()
    await anyio.to_thread.run_sync(apply_files)
    flash(f"Test cases replaced: {count} case(s) imported.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=str(request.url_for("arena_admin_problem_edit", problem_id=problem_id)),
        status_code=303,
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
    zip_bytes = build_single_testcase_zip(input_text.encode("utf-8"), output_text.encode("utf-8"), tc.explanation)
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
    tc_edit_url = str(request.url_for("arena_admin_problem_tc_edit", problem_id=problem_id, tc_id=tc_id))
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if tc is None:
        raise HTTPException(status_code=404, detail="Test case not found")

    zip_bytes = await zip_file.read()
    try:
        single = parse_single_testcase_zip(zip_bytes)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=tc_edit_url, status_code=303)

    _tc, write_files = await admin_problem_tc_service.replace_single_testcase(
        session,
        tc,
        input_bytes=single.input_bytes,
        output_bytes=single.output_bytes,
        explanation=single.explanation,
        testcase_dir=settings.PROBLEM_TESTCASE_DIR,
    )
    await session.commit()
    await anyio.to_thread.run_sync(write_files)
    flash(f"Test case #{tc.ordinal} replaced.", FlashCategory.SUCCESS)
    problem_edit_url = str(request.url_for("arena_admin_problem_edit", problem_id=problem_id))
    return RedirectResponse(url=f"{problem_edit_url}#tc-{tc_id}", status_code=303)
