#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from pydantic import ValidationError

from shared.timezone import Timezone
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_helpers import _clear_frozen_scoreboard_snapshot, _html
from web.services.contest_service import (
    ContestMetadataInput,
    build_contest_metadata_view_with_sites,
    contest_metadata_validation_errors,
    get_active_languages,
    get_contest_language_ids,
    update_contest_metadata,
)
from web.services.problem_service import get_contest_languages
from web.services.site_service import list_contest_site_entries, parse_site_names_payload

router = APIRouter(prefix="/c/{slug}/admin", tags=["contest_admin"])


@router.get("/metadata", response_class=HTMLResponse)
async def edit_metadata(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    view_data = await build_contest_metadata_view_with_sites(ctx.session, ctx.contest)
    site_entries = await list_contest_site_entries(ctx.session, ctx.contest.id)
    languages = await get_contest_languages(ctx.session, ctx.contest)
    available_languages = await get_active_languages(ctx.session)

    return _html(
        templates.TemplateResponse(
            request,
            "admin/edit_metadata.html",
            {
                "form_data": view_data.form_data,
                "contest": ctx.contest,
                "current_user": ctx.actor,
                "timezone_choices": Timezone.choices(),
                "is_locked": view_data.is_locked,
                "is_running": view_data.is_running,
                "site_names": view_data.site_names,
                "site_entries": site_entries,
                "languages": languages,
                "available_languages": available_languages,
                "selected_language_ids": view_data.selected_language_ids,
            },
        )
    )


@router.post("/metadata", response_class=HTMLResponse)
async def edit_metadata_submit(
    request: Request,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    contest_url: str = Form(""),
    start_time: str = Form(""),
    contest_timezone: str = Form(""),
    duration_minutes: str = Form(""),
    stop_answers_after: str = Form(""),
    stop_updating_scoreboard: str = Form(""),
    clarifications_timeout_minutes: str = Form(""),
    tasks_timeout_minutes: str = Form(""),
    review_timeout_minutes: str = Form(""),
    max_problem_file_size_bytes: str = Form(""),
    wa_penalty: str = Form(""),
    show_limits: str = Form("no"),
    autojudge_only: str = Form("no"),
    allow_print_requests: str = Form("no"),
    accept_pe: str = Form("no"),
    ce_adds_penalty: str = Form("no"),
    site_names: str = Form("[]"),
    language_ids: list[str] = Form([]),
) -> Response:
    templates = request.app.state.templates
    languages = await get_contest_languages(ctx.session, ctx.contest)
    available_languages = await get_active_languages(ctx.session)
    is_locked = ctx.contest.is_running or ctx.contest.is_past
    locked_form_data = (await build_contest_metadata_view_with_sites(ctx.session, ctx.contest)).form_data
    current_language_ids = await get_contest_language_ids(ctx.session, ctx.contest)
    selected_language_ids = current_language_ids if is_locked or not ctx.contest.active else language_ids
    try:
        pending_site_names = parse_site_names_payload(site_names)
    except ValueError as exc:
        pending_site_names = []
        site_payload_errors = [str(exc)]
    else:
        site_payload_errors = []

    try:
        metadata = ContestMetadataInput.model_validate(
            {
                "contest_url": contest_url if not is_locked else str(locked_form_data["contest_url"]),
                "start_time": start_time if not is_locked else str(locked_form_data["start_time"]),
                "contest_timezone": contest_timezone if not is_locked else str(locked_form_data["contest_timezone"]),
                "duration_minutes": duration_minutes,
                "stop_answers_after": stop_answers_after,
                "stop_updating_scoreboard": stop_updating_scoreboard,
                "clarifications_timeout_minutes": clarifications_timeout_minutes,
                "tasks_timeout_minutes": tasks_timeout_minutes,
                "review_timeout_minutes": review_timeout_minutes,
                "max_problem_file_size_bytes": (
                    max_problem_file_size_bytes
                    if not is_locked
                    else str(locked_form_data["max_problem_file_size_bytes"])
                ),
                "wa_penalty": wa_penalty if not is_locked else str(locked_form_data["wa_penalty"]),
                "show_limits": (show_limits == "yes") if not is_locked else bool(locked_form_data["show_limits"]),
                "autojudge_only": (autojudge_only == "yes")
                if not is_locked
                else bool(locked_form_data["autojudge_only"]),
                "allow_print_requests": allow_print_requests == "yes",
                "accept_pe": (accept_pe == "yes") if not is_locked else bool(locked_form_data["accept_pe"]),
                "ce_adds_penalty": (
                    (ce_adds_penalty == "yes") if not is_locked else bool(locked_form_data["ce_adds_penalty"])
                ),
            }
        )
    except ValidationError as exc:
        submitted_form_data = {
            "contest_name": ctx.contest.contest_name,
            "login_slug": ctx.contest.login_slug,
            "contest_url": contest_url if not is_locked else locked_form_data["contest_url"],
            "start_time": start_time if not is_locked else locked_form_data["start_time"],
            "contest_timezone": contest_timezone if not is_locked else locked_form_data["contest_timezone"],
            "duration_minutes": duration_minutes,
            "stop_answers_after": stop_answers_after,
            "stop_updating_scoreboard": stop_updating_scoreboard,
            "clarifications_timeout_minutes": clarifications_timeout_minutes,
            "tasks_timeout_minutes": tasks_timeout_minutes,
            "review_timeout_minutes": review_timeout_minutes,
            "max_problem_file_size_bytes": (
                max_problem_file_size_bytes if not is_locked else locked_form_data["max_problem_file_size_bytes"]
            ),
            "wa_penalty": wa_penalty if not is_locked else locked_form_data["wa_penalty"],
            "show_limits": (show_limits == "yes") if not is_locked else locked_form_data["show_limits"],
            "autojudge_only": (autojudge_only == "yes") if not is_locked else locked_form_data["autojudge_only"],
            "allow_print_requests": allow_print_requests == "yes",
            "accept_pe": (accept_pe == "yes") if not is_locked else locked_form_data["accept_pe"],
            "ce_adds_penalty": ((ce_adds_penalty == "yes") if not is_locked else locked_form_data["ce_adds_penalty"]),
        }
        for error in site_payload_errors + contest_metadata_validation_errors(exc):
            flash(error, FlashCategory.DANGER)
        site_entries: list[dict[str, int | str]] = [{"name": name, "user_count": 0} for name in pending_site_names]
        return _html(
            templates.TemplateResponse(
                request,
                "admin/edit_metadata.html",
                {
                    "form_data": submitted_form_data,
                    "contest": ctx.contest,
                    "current_user": ctx.actor,
                    "timezone_choices": Timezone.choices(),
                    "is_locked": is_locked,
                    "is_running": ctx.contest.is_running,
                    "site_names": pending_site_names,
                    "site_entries": site_entries,
                    "languages": languages,
                    "available_languages": available_languages,
                    "selected_language_ids": selected_language_ids,
                },
                status_code=422,
            )
        )

    if site_payload_errors:
        for error in site_payload_errors:
            flash(error, FlashCategory.DANGER)
        site_entries = [{"name": name, "user_count": 0} for name in pending_site_names]
        return _html(
            templates.TemplateResponse(
                request,
                "admin/edit_metadata.html",
                {
                    "form_data": metadata.to_form_data(
                        contest_name=ctx.contest.contest_name,
                        login_slug=ctx.contest.login_slug,
                    ),
                    "contest": ctx.contest,
                    "current_user": ctx.actor,
                    "timezone_choices": Timezone.choices(),
                    "is_locked": is_locked,
                    "is_running": ctx.contest.is_running,
                    "site_names": pending_site_names,
                    "site_entries": site_entries,
                    "languages": languages,
                    "available_languages": available_languages,
                    "selected_language_ids": selected_language_ids,
                },
                status_code=422,
            )
        )

    result = await update_contest_metadata(
        ctx.session,
        ctx.contest,
        ctx.actor,
        metadata=metadata,
        site_names=pending_site_names,
        language_ids=selected_language_ids,
    )

    if result.errors:
        for error in result.errors:
            flash(error, FlashCategory.DANGER)
        site_entries = await list_contest_site_entries(ctx.session, ctx.contest.id)
        return _html(
            templates.TemplateResponse(
                request,
                "admin/edit_metadata.html",
                {
                    "form_data": result.view.form_data,
                    "contest": ctx.contest,
                    "current_user": ctx.actor,
                    "timezone_choices": Timezone.choices(),
                    "is_locked": result.view.is_locked,
                    "is_running": result.view.is_running,
                    "site_names": result.view.site_names,
                    "site_entries": site_entries,
                    "languages": languages,
                    "available_languages": available_languages,
                    "selected_language_ids": result.view.selected_language_ids,
                },
                status_code=422,
            )
        )

    await _clear_frozen_scoreboard_snapshot(request, str(ctx.contest.id))
    flash("Changes saved successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(request.url_for("edit_metadata", slug=ctx.contest.login_slug), status_code=303)
