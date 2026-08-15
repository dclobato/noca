#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Arena judgment-data pages and their actions.

The Arena mirror of ``web.routes.contest_admin_problem_judgment_*``: test cases,
the custom validator and the sample interactions are edited on their own pages,
and everything on them applies immediately -- except the rows an author types
inline, which are the only state the browser holds that the server has not seen.

Ownership is enforced by a dependency rather than a call, so a route that forgets
it fails to resolve rather than silently letting an editor touch someone else's
problem.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_common import get_problem_or_403
from arena.routes.admin_problem_judgment_urls import judgment_page_url
from arena.routes.admin_problem_judgment_view import build_judgment_context
from arena.services import admin_problem_interaction_service, admin_problem_tc_service
from arena.services.admin_problem_case_actions import ProblemVanished, apply_case_action
from shared.enumerations import ProblemValidatorType
from shared.services.editor_urls import editor_url
from shared.services.interaction_pending_ops import (
    SubmittedInteractionError,
    SubmittedInteractionRow,
    parse_pending_interactions,
    submitted_interaction_rows,
)
from shared.services.problem_editor_save import lock_problem_row
from shared.services.problem_package.testcase_archive import parse_testcases_zip
from shared.services.problem_save_errors import unsupported_strategy_message
from shared.services.sample_interactions import MAX_SAMPLE_INTERACTIONS
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

router = APIRouter(prefix="/admin", tags=["arena-admin-problems"])


async def _owned_problem(
    problem_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> ArenaProblem:
    """Resolve the problem this editor is allowed to change.

    A dependency rather than a call: twelve routes is twelve chances to forget an
    ownership check, and a missing dependency is a signature error rather than a
    silent authorization hole.
    """
    return await get_problem_or_403(problem_id, current_user, session)


OwnedProblem = Annotated[ArenaProblem, Depends(_owned_problem)]


def _html(response: Response) -> HTMLResponse:
    """Narrow a template response for the return type."""
    assert isinstance(response, HTMLResponse)
    return response


def _to(request: Request, problem_id: str, page: str = "test-cases", anchor: str | None = None) -> RedirectResponse:
    """Redirect back to one judgment page, optionally at a row."""
    url = judgment_page_url(request, problem_id, page)
    return RedirectResponse(url=editor_url(url, anchor=anchor) if anchor else url, status_code=303)


def _refuse_unsupported(problem: ArenaProblem, flash: FlashDep, request: Request) -> RedirectResponse | None:
    """Refuse a stored strategy this build cannot judge."""
    unsupported = unsupported_strategy_message(problem.validator_type)
    if unsupported is None:
        return None
    flash(unsupported, FlashCategory.DANGER)
    return _to(request, problem.id)


async def _render_rejected_cases(
    request: Request,
    session: AsyncSession,
    problem: ArenaProblem,
    current_user: ArenaUser,
    rows: tuple[SubmittedCaseRow, ...],
    message: str,
) -> HTMLResponse:
    """Render submitted testcase rows beside their validation error."""
    problem = await get_problem_or_403(problem.id, current_user, session)
    context = await build_judgment_context(
        request,
        session,
        problem,
        active_page="test-cases",
        current_user=current_user,
    )
    context |= {
        "submitted_case_rows": rows,
        "pending_error": message,
        "pending_next_index": max((row.index for row in rows), default=-1) + 1,
    }
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problem_judgment_cases.html",
            context,
            status_code=422,
        )
    )


async def _render_rejected_interactions(
    request: Request,
    session: AsyncSession,
    problem: ArenaProblem,
    current_user: ArenaUser,
    rows: tuple[SubmittedInteractionRow, ...],
    message: str,
) -> HTMLResponse:
    """Render submitted interaction rows beside their validation error."""
    problem = await get_problem_or_403(problem.id, current_user, session)
    context = await build_judgment_context(
        request,
        session,
        problem,
        active_page="interactions",
        current_user=current_user,
    )
    context |= {
        "submitted_interaction_rows": rows,
        "pending_error": message,
        "pending_next_index": max((row.index for row in rows), default=-1) + 1,
    }
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problem_judgment_interactions.html",
            context,
            status_code=422,
        )
    )


@router.get("/problems/{problem_id}/judgment", name="arena_admin_problem_judgment")
async def admin_problem_judgment(request: Request, problem: OwnedProblem) -> Response:
    """Open the page the author most likely wants."""
    if problem.validator_type is ProblemValidatorType.INTERACTIVE and problem.custom_validator is None:
        return _to(request, problem.id, "validator")
    return _to(request, problem.id)


@router.get(
    "/problems/{problem_id}/judgment/test-cases",
    response_class=HTMLResponse,
    response_model=None,
    name="arena_admin_problem_judgment_cases",
)
async def admin_problem_judgment_cases(
    request: Request,
    problem: OwnedProblem,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the test-case list, its inline add rows, and the upload controls."""
    templates = request.app.state.arena_templates
    context = await build_judgment_context(
        request, session, problem, active_page="test-cases", current_user=current_user
    )
    return _html(templates.TemplateResponse(request, "admin/problem_judgment_cases.html", context))


@router.post("/problems/{problem_id}/judgment/test-cases", name="arena_admin_problem_judgment_cases_save")
async def admin_problem_judgment_cases_save(
    request: Request,
    problem: OwnedProblem,
    flash: FlashDep,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Add the rows typed inline on the page."""
    refusal = _refuse_unsupported(problem, flash, request)
    if refusal is not None:
        return refusal

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    form = await request.form()
    submitted = submitted_case_rows(form)
    added = parse_inline_added_cases(form, interactive=interactive)
    if not added:
        return await _render_rejected_cases(
            request,
            session,
            problem,
            current_user,
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
            session,
            problem,
            current_user,
            errored,
            "Correct the highlighted test case and save again.",
        )

    try:
        await apply_case_action(
            session,
            problem,
            PendingTestCaseOps(added=added),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except ProblemVanished:
        flash("Problem not found.", FlashCategory.DANGER)
        return _to(request, problem.id)
    except ValueError as exc:
        return await _render_rejected_cases(
            request,
            session,
            problem,
            current_user,
            submitted,
            str(exc),
        )

    flash(f"{len(added)} test case(s) added.", FlashCategory.SUCCESS)
    return _to(request, problem.id)


@router.post("/problems/{problem_id}/judgment/test-cases/upload", name="arena_admin_problem_judgment_case_upload")
async def admin_problem_judgment_case_upload(
    request: Request,
    problem: OwnedProblem,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    tc_add_zip: list[UploadFile] = File(default=[]),
) -> Response:
    """Append one or more cases from single-case archives."""
    refusal = _refuse_unsupported(problem, flash, request)
    if refusal is not None:
        return refusal

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    added: list[CaseContent] = []
    for position, upload in enumerate(tc_add_zip, start=1):
        if not upload.filename:
            continue
        try:
            single = parse_single_testcase_zip(await upload.read(), require_output=not interactive)
        except ValueError as exc:
            flash(f"Test case ZIP #{position}: {exc}", FlashCategory.DANGER)
            return _to(request, problem.id)
        added.append(case_content_from_single_archive(single, interactive=interactive))

    if not added:
        flash("Choose at least one ZIP to upload.", FlashCategory.WARNING)
        return _to(request, problem.id)

    try:
        await apply_case_action(
            session,
            problem,
            PendingTestCaseOps(added=tuple(added)),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except (ValueError, ProblemVanished) as exc:
        flash(str(exc) or "Problem not found.", FlashCategory.DANGER)
        return _to(request, problem.id)

    flash(f"{len(added)} test case(s) added from ZIP.", FlashCategory.SUCCESS)
    return _to(request, problem.id)


@router.post("/problems/{problem_id}/judgment/test-cases/bulk", name="arena_admin_problem_judgment_cases_replace_all")
async def admin_problem_judgment_cases_replace_all(
    request: Request,
    problem: OwnedProblem,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    tc_bulk_zip: UploadFile = File(...),
) -> Response:
    """Replace every test case from one archive."""
    refusal = _refuse_unsupported(problem, flash, request)
    if refusal is not None:
        return refusal

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    try:
        parsed = parse_testcases_zip(await tc_bulk_zip.read(), require_output=not interactive)
        cases = case_contents_from_bulk_archive(parsed, interactive=interactive)
        await apply_case_action(
            session,
            problem,
            PendingTestCaseOps(bulk_cases=cases),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except (ValueError, ProblemVanished) as exc:
        flash(str(exc) or "Problem not found.", FlashCategory.DANGER)
        return _to(request, problem.id)

    flash(f"Test cases replaced: {len(cases)} case(s) imported.", FlashCategory.SUCCESS)
    return _to(request, problem.id)


@router.post(
    "/problems/{problem_id}/judgment/test-cases/{tc_id}/toggle-sample",
    name="arena_admin_problem_judgment_case_toggle_sample",
)
async def admin_problem_judgment_case_toggle_sample(
    request: Request,
    tc_id: str,
    problem: OwnedProblem,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Flip one case between sample and secret.

    No file changes, so no swap: staging a whole directory to change a boolean
    would copy the problem's entire test data. The row lock still applies.
    """
    refusal = _refuse_unsupported(problem, flash, request)
    if refusal is not None:
        return refusal

    if not await lock_problem_row(session, "arena", problem.id):
        raise HTTPException(status_code=404, detail="Problem not found")
    # Read the case *after* the lock, and flip the value it holds now. Deciding
    # from a value read before the lock makes two concurrent toggles collapse into
    # one -- both read "secret", both write "sample" -- and would flip a case
    # another request had already deleted.
    test_case = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if test_case is None:
        flash("That test case no longer exists; reload the page and try again.", FlashCategory.DANGER)
        return _to(request, problem.id)

    try:
        await admin_problem_tc_service.toggle_sample(session, test_case)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _to(request, problem.id, anchor=f"tc-{tc_id}")
    kind = "sample" if test_case.is_sample else "secret"
    ordinal = test_case.ordinal
    await session.commit()

    flash(f"Test case #{ordinal} is now a {kind} case.", FlashCategory.SUCCESS)
    return _to(request, problem.id, anchor=f"tc-{tc_id}")


@router.post(
    "/problems/{problem_id}/judgment/test-cases/{tc_id}/replace",
    name="arena_admin_problem_judgment_case_replace",
)
async def admin_problem_judgment_case_replace(
    request: Request,
    tc_id: str,
    problem: OwnedProblem,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    zip_file: UploadFile = File(...),
) -> Response:
    """Replace one case from a single-case archive, with no size cap."""
    refusal = _refuse_unsupported(problem, flash, request)
    if refusal is not None:
        return refusal

    test_case = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if test_case is None:
        raise HTTPException(status_code=404, detail="Test case not found")

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    ordinal = test_case.ordinal
    was_sample = test_case.is_sample
    try:
        single = parse_single_testcase_zip(await zip_file.read(), require_output=not interactive)
        await apply_case_action(
            session,
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
    except (ValueError, ProblemVanished) as exc:
        flash(str(exc) or "Problem not found.", FlashCategory.DANGER)
        return _to(request, problem.id, anchor=f"tc-{tc_id}")

    flash(f"Test case #{ordinal} replaced.", FlashCategory.SUCCESS)
    return _to(request, problem.id, anchor=f"tc-{tc_id}")


@router.post(
    "/problems/{problem_id}/judgment/test-cases/{tc_id}/delete",
    name="arena_admin_problem_judgment_case_delete",
)
async def admin_problem_judgment_case_delete(
    request: Request,
    tc_id: str,
    problem: OwnedProblem,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete one case and close the ordinal gap it leaves."""
    refusal = _refuse_unsupported(problem, flash, request)
    if refusal is not None:
        return refusal

    test_case = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem.id)
    if test_case is None:
        raise HTTPException(status_code=404, detail="Test case not found")

    # Removing the last case leaves an incomplete draft, which is allowed: the
    # problem simply stops being judgeable until a case is added back.
    ordinal = test_case.ordinal
    try:
        await apply_case_action(
            session,
            problem,
            PendingTestCaseOps(removals=frozenset({tc_id})),
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except (ValueError, ProblemVanished) as exc:
        flash(str(exc) or "Problem not found.", FlashCategory.DANGER)
        return _to(request, problem.id)

    flash(f"Test case #{ordinal} removed.", FlashCategory.SUCCESS)
    return _to(request, problem.id)


@router.get(
    "/problems/{problem_id}/judgment/validator",
    response_class=HTMLResponse,
    response_model=None,
    name="arena_admin_problem_judgment_validator",
)
async def admin_problem_judgment_validator(
    request: Request,
    problem: OwnedProblem,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the validator page, for an interactive problem."""
    if problem.validator_type is not ProblemValidatorType.INTERACTIVE:
        return _to(request, problem.id)
    templates = request.app.state.arena_templates
    context = await build_judgment_context(
        request, session, problem, active_page="validator", current_user=current_user
    )
    return _html(templates.TemplateResponse(request, "admin/problem_judgment_validator.html", context))


@router.get(
    "/problems/{problem_id}/judgment/interactions",
    response_class=HTMLResponse,
    response_model=None,
    name="arena_admin_problem_judgment_interactions",
)
async def admin_problem_judgment_interactions(
    request: Request,
    problem: OwnedProblem,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the sample-interactions page, for an interactive problem."""
    if problem.validator_type is not ProblemValidatorType.INTERACTIVE:
        return _to(request, problem.id)
    templates = request.app.state.arena_templates
    context = await build_judgment_context(
        request, session, problem, active_page="interactions", current_user=current_user
    )
    return _html(templates.TemplateResponse(request, "admin/problem_judgment_interactions.html", context))


@router.post(
    "/problems/{problem_id}/judgment/interactions",
    name="arena_admin_problem_judgment_interactions_save",
)
async def admin_problem_judgment_interactions_save(
    request: Request,
    problem: OwnedProblem,
    flash: FlashDep,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Add the transcripts typed inline on the page."""
    if problem.validator_type is not ProblemValidatorType.INTERACTIVE:
        return _to(request, problem.id)

    form = await request.form()
    submitted = submitted_interaction_rows(form)
    try:
        pending = parse_pending_interactions(form)
    except SubmittedInteractionError as exc:
        errored = tuple(replace(row, error=str(exc)) if row.index == exc.index else row for row in submitted)
        return await _render_rejected_interactions(
            request,
            session,
            problem,
            current_user,
            errored,
            "Correct the highlighted interaction and save again.",
        )
    if not pending:
        return await _render_rejected_interactions(
            request,
            session,
            problem,
            current_user,
            submitted,
            "Nothing to add: write a transcript first.",
        )

    existing = await admin_problem_interaction_service.list_interactions(session, problem.id)
    if len(existing) + len(pending) > MAX_SAMPLE_INTERACTIONS:
        message = f"A problem may have at most {MAX_SAMPLE_INTERACTIONS} sample interactions."
        last_index = pending[-1].index
        errored = tuple(replace(row, error=message) if row.index == last_index else row for row in submitted)
        return await _render_rejected_interactions(
            request,
            session,
            problem,
            current_user,
            errored,
            "Remove a submitted row or an existing interaction before saving.",
        )

    if not await lock_problem_row(session, "arena", problem.id):
        raise HTTPException(status_code=404, detail="Problem not found")
    for item in pending:
        try:
            await admin_problem_interaction_service.create_interaction(
                session,
                problem,
                transcript=item.transcript,
                explanation=item.explanation,
            )
        except ValueError as exc:
            await session.rollback()
            errored = tuple(replace(row, error=str(exc)) if row.index == item.index else row for row in submitted)
            return await _render_rejected_interactions(
                request,
                session,
                problem,
                current_user,
                errored,
                "Correct the highlighted interaction and save again.",
            )
    await session.commit()

    flash(f"{len(pending)} sample interaction(s) added.", FlashCategory.SUCCESS)
    return _to(request, problem.id, "interactions")


@router.post(
    "/problems/{problem_id}/judgment/interactions/{si_id}/delete",
    name="arena_admin_problem_judgment_interaction_delete",
)
async def admin_problem_judgment_interaction_delete(
    request: Request,
    si_id: str,
    problem: OwnedProblem,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Delete one sample interaction immediately."""
    if not await lock_problem_row(session, "arena", problem.id):
        raise HTTPException(status_code=404, detail="Problem not found")
    interactions = await admin_problem_interaction_service.list_interactions(session, problem.id)
    interaction = next((item for item in interactions if item.id == si_id), None)
    if interaction is None:
        raise HTTPException(status_code=404, detail="Sample interaction not found")

    await admin_problem_interaction_service.delete_interaction(session, interaction)
    await session.commit()

    flash("Sample interaction removed.", FlashCategory.SUCCESS)
    return _to(request, problem.id, "interactions")
