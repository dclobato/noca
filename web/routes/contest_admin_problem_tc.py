#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.services.testcase_files import read_testcase_full, read_testcase_sizes
from shared.tc_zip import (
    MAX_INLINE_TESTCASE_BYTES,
    build_single_testcase_zip,
    normalize_testcase_bytes,
    parse_single_testcase_zip,
)
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.problem import ProblemTestCase
from web.routes.contest_admin_problem_helpers import (
    _delete_testcase_files_for,
    _html,
    _is_edit_allowed,
    _read_testcase_full_for,
    _read_testcase_preview_for,
    _redirect,
    _renumber_testcase_files_for,
    _run_sync0,
    _save_testcase_files_for,
    build_testcase_row_views,
)
from web.services.problem_service import (
    append_test_case,
    get_problem_in_contest,
    move_test_case,
    parse_testcases_zip,
    remove_test_case_and_resequence,
    reorder_testcase_files,
)

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


def _inline_size_error(tc_in: str, tc_out: str) -> str | None:
    """Return an error message if either inline side exceeds the gate threshold."""
    limit_kb = MAX_INLINE_TESTCASE_BYTES // 1024
    if len(normalize_testcase_bytes(tc_in.encode("utf-8"))) > MAX_INLINE_TESTCASE_BYTES:
        return f"Input exceeds the {limit_kb} KB inline-edit limit; edit this case offline via download/replace."
    if len(normalize_testcase_bytes(tc_out.encode("utf-8"))) > MAX_INLINE_TESTCASE_BYTES:
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
    edit_url = str(request.url_for("edit_problem_form", slug=slug, problem_id=problem_id))
    return f"{edit_url}?tab=content#tc-{tc_id}"


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


def _reorder_testcase_files_for(
    problem_id: str,
    ordinal_map: dict[int, int],
    testcase_dir: Path,
) -> Callable[[], None]:
    """Build a sync closure to reorder testcase files."""

    def _reorder() -> None:
        reorder_testcase_files(problem_id, ordinal_map, testcase_dir)

    return _reorder


# ---------------------------------------------------------------------------
# Upload test case ZIP (replaces all existing)
# ---------------------------------------------------------------------------


@router.post("/{problem_id}/test-cases/zip", name="upload_testcase_zip")
async def upload_testcase_zip(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    testcases_zip: UploadFile = File(...),
) -> RedirectResponse:
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(edit_url)

    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(edit_url)

    zip_bytes = await testcases_zip.read()
    try:
        parsed = parse_testcases_zip(zip_bytes)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(edit_url)

    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    pid = problem.id

    # DB: delete all existing rows, capture their ordinals for later file cleanup.
    old_ordinals = [tc.ordinal for tc in problem.test_cases]
    for tc in list(problem.test_cases):
        await ctx.session.delete(tc)
    await ctx.session.flush()
    await ctx.session.refresh(problem, attribute_names=["test_cases"])

    # DB: insert new rows with sizes computed in memory (no file I/O yet).
    new_file_data: list[tuple[int, bytes, bytes]] = []
    for source_ordinal, (in_b, out_b) in sorted(parsed.pairs.items()):
        tc = ProblemTestCase(is_sample=False, explanation=parsed.explanations.get(source_ordinal))
        await append_test_case(ctx.session, problem, tc)
        ordinal = tc.ordinal
        tc.input_size_bytes = len(normalize_testcase_bytes(in_b))
        tc.output_size_bytes = len(normalize_testcase_bytes(out_b))
        new_file_data.append((ordinal, in_b, out_b))

    await ctx.session.commit()

    # File I/O after commit: delete old files, then write new ones.
    for old_ordinal in old_ordinals:
        await anyio.to_thread.run_sync(_run_sync0(_delete_testcase_files_for(pid, old_ordinal, testcase_dir)))
    for ordinal, in_b, out_b in new_file_data:
        await anyio.to_thread.run_sync(_run_sync0(_save_testcase_files_for(pid, ordinal, in_b, out_b, testcase_dir)))

    flash("Test cases updated successfully.", FlashCategory.SUCCESS)
    return _redirect(edit_url)


# ---------------------------------------------------------------------------
# New test case (dedicated page)
# ---------------------------------------------------------------------------


@router.get(
    "/{problem_id}/test-cases/new",
    response_class=HTMLResponse,
    response_model=None,
    name="new_test_case_form",
)
async def new_test_case_form(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    templates = request.app.state.templates
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    if not _is_edit_allowed(ctx.contest):
        return _redirect(str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id)))

    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/testcase_edit.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem": problem,
                "tc": None,
                "form_data": {},
                "errors": [],
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
            },
        )
    )


# ---------------------------------------------------------------------------
# Edit test case (dedicated page)
# ---------------------------------------------------------------------------


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
    in_size: int = tc.input_size_bytes or 0
    out_size: int = tc.output_size_bytes or 0
    if tc.input_size_bytes is None or tc.output_size_bytes is None:
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
            },
        )
    )


# ---------------------------------------------------------------------------
# Add / edit / remove / move test cases
# ---------------------------------------------------------------------------


@router.post("/{problem_id}/test-cases/add", name="add_test_case")
async def add_test_case(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    tc_in: str = Form(""),
    tc_out: str = Form(""),
    explanation: str = Form(""),
    is_sample: str | None = Form(None),
) -> RedirectResponse:
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
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

    tc = ProblemTestCase(is_sample=is_sample is not None, explanation=explanation_value)
    await append_test_case(ctx.session, problem, tc)
    ordinal = tc.ordinal
    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    in_bytes = tc_in.encode()
    out_bytes = tc_out.encode()
    tc.input_size_bytes = len(normalize_testcase_bytes(in_bytes))
    tc.output_size_bytes = len(normalize_testcase_bytes(out_bytes))
    await ctx.session.commit()
    await anyio.to_thread.run_sync(
        _run_sync0(_save_testcase_files_for(problem.id, ordinal, in_bytes, out_bytes, testcase_dir))
    )
    flash("Test case added successfully.", FlashCategory.SUCCESS)
    return _redirect(edit_url)


@router.post("/{problem_id}/test-cases/add-zip", name="add_test_case_zip")
async def add_test_case_zip(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    zip_file: UploadFile = File(...),
) -> RedirectResponse:
    """Add a single new test case from an uploaded single-case ZIP.

    Args:
        request: The incoming HTTP request.
        problem_id: UUID of the problem.
        flash: Flash message dependency.
        ctx: Contest admin context.
        zip_file: Uploaded ZIP with ``input.txt`` and ``output.txt``.

    Returns:
        RedirectResponse: Redirect to the problem edit page.
    """
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(edit_url)

    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(edit_url)

    zip_bytes = await zip_file.read()
    try:
        single = parse_single_testcase_zip(zip_bytes)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(edit_url)

    tc = ProblemTestCase(is_sample=False, explanation=single.explanation)
    await append_test_case(ctx.session, problem, tc)
    ordinal = tc.ordinal
    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    tc.input_size_bytes = len(single.input_bytes)
    tc.output_size_bytes = len(single.output_bytes)
    await ctx.session.commit()
    await anyio.to_thread.run_sync(
        _run_sync0(_save_testcase_files_for(problem.id, ordinal, single.input_bytes, single.output_bytes, testcase_dir))
    )
    flash("Test case added from ZIP.", FlashCategory.SUCCESS)
    return _redirect(edit_url)


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
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
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

    tc.is_sample = is_sample is not None
    tc.explanation = explanation_value
    ordinal = tc.ordinal
    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    in_bytes = tc_in.encode()
    out_bytes = tc_out.encode()
    tc.input_size_bytes = len(normalize_testcase_bytes(in_bytes))
    tc.output_size_bytes = len(normalize_testcase_bytes(out_bytes))
    await ctx.session.commit()
    await anyio.to_thread.run_sync(
        _run_sync0(_save_testcase_files_for(problem.id, ordinal, in_bytes, out_bytes, testcase_dir))
    )
    flash("Test case updated successfully.", FlashCategory.SUCCESS)
    return _redirect(_testcase_edit_return_url(request, ctx.contest.login_slug, problem_id, tc_id))


@router.post("/{problem_id}/test-cases/{tc_id}/remove", name="remove_test_case_route")
async def remove_test_case_route(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
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

    if len(problem.test_cases) <= 1:
        flash("Cannot remove the only remaining test case.", FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)

    removed_ordinal = tc.ordinal
    total = len(problem.test_cases)
    pid = problem.id
    testcase_dir = settings.PROBLEM_TESTCASE_DIR

    await remove_test_case_and_resequence(ctx.session, problem, tc)
    await ctx.session.commit()

    await anyio.to_thread.run_sync(_run_sync0(_delete_testcase_files_for(pid, removed_ordinal, testcase_dir)))
    for old_ord in range(removed_ordinal + 1, total + 1):
        await anyio.to_thread.run_sync(
            _run_sync0(_renumber_testcase_files_for(pid, old_ord, old_ord - 1, testcase_dir))
        )

    flash("Test case removed successfully.", FlashCategory.SUCCESS)
    return _redirect(edit_url)


@router.post("/{problem_id}/test-cases/{tc_id}/toggle-sample", name="toggle_test_case_sample")
async def toggle_test_case_sample(
    request: Request,
    problem_id: str,
    tc_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
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

    tc.is_sample = not tc.is_sample
    await ctx.session.commit()
    kind = "sample" if tc.is_sample else "secret"
    flash(f"Test case #{tc.ordinal} is now a {kind} case.", FlashCategory.SUCCESS)
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
    new_ordinal: int | None = Query(None),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse | RedirectResponse:
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
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
    ordinal_map = _testcase_file_ordinal_map(list(problem.test_cases), tc.id, new_ordinal)
    await anyio.to_thread.run_sync(_run_sync0(_reorder_testcase_files_for(problem.id, ordinal_map, testcase_dir)))

    await move_test_case(ctx.session, problem, tc, new_ordinal)
    await ctx.session.commit()

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
    zip_bytes = build_single_testcase_zip(tc_in.encode("utf-8"), tc_out.encode("utf-8"), tc.explanation)
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
    tc_edit_url = str(
        request.url_for("edit_test_case_form", slug=ctx.contest.login_slug, problem_id=problem_id, tc_id=tc_id)
    )
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
    try:
        single = parse_single_testcase_zip(zip_bytes)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(tc_edit_url)

    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    pid = problem.id
    ordinal = tc.ordinal
    in_bytes = single.input_bytes
    out_bytes = single.output_bytes
    tc.input_size_bytes = len(normalize_testcase_bytes(in_bytes))
    tc.output_size_bytes = len(normalize_testcase_bytes(out_bytes))
    if single.explanation is not None:
        tc.explanation = single.explanation
    await ctx.session.commit()
    await anyio.to_thread.run_sync(
        _run_sync0(_save_testcase_files_for(pid, ordinal, in_bytes, out_bytes, testcase_dir))
    )
    flash(f"Test case #{tc.ordinal} replaced.", FlashCategory.SUCCESS)
    problem_edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
    return _redirect(f"{problem_edit_url}?tab=content#tc-{tc_id}")
