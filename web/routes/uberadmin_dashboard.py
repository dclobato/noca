#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from shared.services.security_events import record_request_security_event
from shared.timezone import Timezone
from web.config import settings
from web.dependencies import get_uberadmin
from web.models.users import UberAdmin
from web.services.assorted_utils import slugfy
from web.services.contest_service import (
    ContestMetadataInput,
    build_blank_contest_form,
    contest_metadata_validation_errors,
    create_contest_with_owner,
    deactivate_past_contest,
    get_active_contests_grouped,
    get_inactive_contests,
)
from web.services.problem_service import get_active_languages
from web.services.uberadmin_service import create_uberadmin_account
from web.services.user_credentials_email_service import (
    build_uberadmin_credentials_email_content,
    build_user_credentials_email_content,
    send_credentials_email,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uberadmin", tags=["uberadmin"])


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


def _five_days_ahead_at_0900() -> str:
    five_days_ahead = datetime.now() + timedelta(days=5)
    return five_days_ahead.strftime("%Y-%m-%dT09:00")


def _build_contest_login_url(request: Request, slug: str) -> str:
    """Build an absolute contest login URL for credential emails."""
    url = request.url_for("contest_login_get", slug=slug)
    if settings.WEB_URL_BASE:
        return settings.WEB_URL_BASE + str(url.path)
    return str(url)


def _build_uberadmin_login_url(request: Request) -> str:
    """Build an absolute uberadmin login URL for credential emails."""
    url = request.url_for("login_get")
    if settings.WEB_URL_BASE:
        return settings.WEB_URL_BASE + str(url.path)
    return str(url)


async def _record_credential_email_event(
    request: Request,
    *,
    actor_user_id: str,
    event_type: str,
    scope: str,
    target_username: str,
    contest_slug: str | None = None,
) -> None:
    """Record a Web credential-email audit event without storing secrets."""
    metadata = {"scope": scope, "target_username": target_username}
    if contest_slug is not None:
        metadata["contest_slug"] = contest_slug
    async with request.app.state.db_session() as session:
        await record_request_security_event(
            session,
            request,
            module="web",
            event_type=event_type,
            severity="info" if event_type == "credential_email_sent" else "warning",
            actor_user_id=actor_user_id,
            metadata=metadata,
        )
        await session.commit()


def _contest_creation_context(
    *,
    form_data: dict[str, Any],
    languages: list[Any],
    errors: list[str],
    created_credential: dict[str, str] | None,
    email_delivery_message: str | None = None,
    email_delivery_success: bool | None = None,
) -> dict[str, object]:
    """Build the template context for the contest creation page."""
    return {
        "form_data": form_data,
        "languages": languages,
        "timezone_choices": Timezone.choices(),
        "errors": errors,
        "created_credential": created_credential,
        "email_delivery_message": email_delivery_message,
        "email_delivery_success": email_delivery_success,
    }


@router.get("/", response_class=HTMLResponse, name="uberadmin_dashboard")
async def dashboard(
    request: Request,
    uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    async with request.app.state.db_session() as session:
        contests = await get_active_contests_grouped(session)

    return _templates(request).TemplateResponse(
        request,
        "uberadmin/dashboard.html",
        {
            "current_user": uberadmin,
            "past_contests": contests.past_contests,
            "live_contests": contests.live_contests,
            "upcoming_contests": contests.upcoming_contests,
        },
    )


@router.get("/contests/inactive", response_class=HTMLResponse, name="uberadmin_inactive_contests")
async def inactive_contests(
    request: Request,
    uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    async with request.app.state.db_session() as session:
        contests = await get_inactive_contests(session)

    return _templates(request).TemplateResponse(
        request,
        "uberadmin/inactive_contests.html",
        {
            "current_user": uberadmin,
            "inactive_contests": contests,
        },
    )


@router.post("/contests/{contest_id}/deactivate", name="uberadmin_deactivate_contest")
async def deactivate_contest(
    request: Request,
    contest_id: str,
    _uberadmin: UberAdmin = Depends(get_uberadmin),
) -> RedirectResponse:
    async with request.app.state.db_session() as session:
        await deactivate_past_contest(session, contest_id)
    return RedirectResponse(url=str(request.url_for("uberadmin_dashboard")), status_code=303)


def _add_uberadmin_context(
    *,
    form_data: dict[str, Any],
    error: str,
    created_credential: dict[str, str] | None,
    email_delivery_message: str | None = None,
    email_delivery_success: bool | None = None,
) -> dict[str, object]:
    """Build the template context for the add UberAdmin page."""
    return {
        "form_data": form_data,
        "error": error,
        "created_credential": created_credential,
        "email_delivery_message": email_delivery_message,
        "email_delivery_success": email_delivery_success,
    }


@router.get("/uberadmins/new", response_class=HTMLResponse)
async def add_uberadmin(
    request: Request,
    _uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/add_useradmin.html",
        _add_uberadmin_context(
            form_data={"fullname": "", "email": "", "username": ""},
            error="",
            created_credential=None,
        ),
    )


@router.post("/uberadmins/new", response_class=HTMLResponse)
async def add_uberadmin_submit(
    request: Request,
    fullname: str = Form(""),
    email: str = Form(""),
    username: str = Form(""),
    uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    async with request.app.state.db_session() as session:
        result = await create_uberadmin_account(
            session,
            creator_username=uberadmin.username,
            fullname=fullname,
            email=email,
            username=username,
        )

    return _templates(request).TemplateResponse(
        request,
        "uberadmin/add_useradmin.html",
        _add_uberadmin_context(
            form_data=result.form_data,
            error=result.error,
            created_credential=result.created_credential,
        ),
        status_code=422 if result.error else 200,
    )


@router.post("/uberadmins/credentials/email", response_class=HTMLResponse, name="send_uberadmin_credentials_email")
async def send_uberadmin_credentials_email_route(
    request: Request,
    fullname: str = Form(...),
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    _uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    credentials = {
        "username": username,
        "password": password,
        "fullname": fullname,
        "email": email.strip(),
    }
    if not email.strip():
        await _record_credential_email_event(
            request,
            actor_user_id=_uberadmin.id,
            event_type="credential_email_skipped",
            scope="uberadmin",
            target_username=username,
        )
        return _templates(request).TemplateResponse(
            request,
            "uberadmin/add_useradmin.html",
            _add_uberadmin_context(
                form_data={"fullname": "", "email": "", "username": ""},
                error="",
                created_credential=credentials,
                email_delivery_message="UberAdmin has no email address.",
                email_delivery_success=False,
            ),
            status_code=422,
        )

    email_service = request.app.state.email_service
    delivery = send_credentials_email(
        email_service,
        to_email=email.strip(),
        fullname=fullname,
        content=build_uberadmin_credentials_email_content(
            fullname=fullname,
            login_url=_build_uberadmin_login_url(request),
            username=username,
            password=password,
            sender_name=email_service.default_from_name or "Noca Contest",
        ),
    )
    await _record_credential_email_event(
        request,
        actor_user_id=_uberadmin.id,
        event_type="credential_email_sent" if delivery.success else "credential_email_failed",
        scope="uberadmin",
        target_username=username,
    )
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/add_useradmin.html",
        _add_uberadmin_context(
            form_data={"fullname": "", "email": "", "username": ""},
            error="",
            created_credential=credentials,
            email_delivery_message=delivery.detail,
            email_delivery_success=delivery.success,
        ),
        status_code=200 if delivery.success else 422,
    )


@router.post("/uberadmins/credentials.json")
async def download_uberadmin_credentials_json(
    username: str = Form(...),
    password: str = Form(...),
    _uberadmin: UberAdmin = Depends(get_uberadmin),
) -> Response:
    payload = {"username": username, "password": password}
    content = json.dumps(payload, ensure_ascii=True, indent=2)
    safe_username = slugfy(username, fallback="uberadmin")
    filename = f"noca-credentials-{safe_username}.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/contests/new", response_class=HTMLResponse)
async def add_contest(
    request: Request,
    _uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    form_data = build_blank_contest_form(_five_days_ahead_at_0900())
    async with request.app.state.db_session() as session:
        languages = await get_active_languages(session)
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/add_contest.html",
        _contest_creation_context(
            form_data=form_data,
            languages=languages,
            errors=[],
            created_credential=None,
        ),
    )


@router.post("/contests/new", response_class=HTMLResponse)
async def add_contest_submit(
    request: Request,
    contest_name: str = Form(""),
    login_slug: str = Form(""),
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
    owner_username: str = Form(""),
    owner_fullname: str = Form(""),
    owner_email: str = Form(""),
    owner_password: str = Form(""),
    language_ids: list[str] = Form(default_factory=list),
    uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    async with request.app.state.db_session() as _lang_session:
        languages = await get_active_languages(_lang_session)

    try:
        metadata = ContestMetadataInput.model_validate(
            {
                "contest_url": contest_url,
                "start_time": start_time,
                "contest_timezone": contest_timezone,
                "duration_minutes": duration_minutes,
                "stop_answers_after": stop_answers_after,
                "stop_updating_scoreboard": stop_updating_scoreboard,
                "clarifications_timeout_minutes": clarifications_timeout_minutes,
                "tasks_timeout_minutes": tasks_timeout_minutes,
                "review_timeout_minutes": review_timeout_minutes,
                "max_problem_file_size_bytes": max_problem_file_size_bytes,
                "wa_penalty": wa_penalty,
                "show_limits": show_limits == "yes",
                "autojudge_only": autojudge_only == "yes",
                "allow_print_requests": allow_print_requests == "yes",
                "accept_pe": accept_pe == "yes",
                "ce_adds_penalty": ce_adds_penalty == "yes",
            }
        )
    except ValidationError as exc:
        return _templates(request).TemplateResponse(
            request,
            "uberadmin/add_contest.html",
            _contest_creation_context(
                form_data={
                    "contest_name": contest_name,
                    "login_slug": login_slug,
                    "contest_url": contest_url,
                    "start_time": start_time,
                    "contest_timezone": contest_timezone,
                    "duration_minutes": duration_minutes,
                    "stop_answers_after": stop_answers_after,
                    "stop_updating_scoreboard": stop_updating_scoreboard,
                    "clarifications_timeout_minutes": clarifications_timeout_minutes,
                    "tasks_timeout_minutes": tasks_timeout_minutes,
                    "review_timeout_minutes": review_timeout_minutes,
                    "max_problem_file_size_bytes": max_problem_file_size_bytes,
                    "wa_penalty": wa_penalty,
                    "show_limits": show_limits == "yes",
                    "autojudge_only": autojudge_only == "yes",
                    "allow_print_requests": allow_print_requests == "yes",
                    "accept_pe": accept_pe == "yes",
                    "ce_adds_penalty": ce_adds_penalty == "yes",
                    "owner_username": owner_username,
                    "owner_fullname": owner_fullname,
                    "owner_email": owner_email,
                    "owner_password": owner_password,
                    "language_ids": language_ids,
                },
                languages=languages,
                errors=contest_metadata_validation_errors(exc),
                created_credential=None,
            ),
            status_code=422,
        )

    async with request.app.state.db_session() as session:
        result = await create_contest_with_owner(
            session,
            creator_username=uberadmin.username,
            contest_name=contest_name,
            login_slug=login_slug,
            metadata=metadata,
            owner_username=owner_username,
            owner_fullname=owner_fullname,
            owner_email=owner_email,
            owner_password=owner_password,
            language_ids=language_ids,
        )

    success_form = build_blank_contest_form(_five_days_ahead_at_0900())
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/add_contest.html",
        _contest_creation_context(
            form_data=success_form if result.success else result.form_data,
            languages=languages,
            errors=result.errors,
            created_credential=result.created_credential,
        ),
        status_code=422 if result.errors else 200,
    )


@router.post("/contests/credentials.json")
async def download_contest_credentials_json(
    contest_slug: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    _uberadmin: UberAdmin = Depends(get_uberadmin),
) -> Response:
    payload = {"contest_slug": contest_slug, "username": username, "password": password}
    content = json.dumps(payload, ensure_ascii=True, indent=2)
    safe_slug = slugfy(contest_slug, fallback="contest")
    safe_username = slugfy(username, fallback="admin")
    filename = f"noca-credentials-{safe_slug}-{safe_username}.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/contests/credentials/email", response_class=HTMLResponse)
async def send_contest_credentials_email(
    request: Request,
    contest_slug: str = Form(...),
    contest_name: str = Form(...),
    fullname: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    email: str = Form(""),
    _uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    async with request.app.state.db_session() as session:
        languages = await get_active_languages(session)

    credentials = {
        "contest_slug": contest_slug,
        "contest_name": contest_name,
        "fullname": fullname,
        "username": username,
        "password": password,
        "email": email.strip(),
    }
    if not email.strip():
        await _record_credential_email_event(
            request,
            actor_user_id=_uberadmin.id,
            event_type="credential_email_skipped",
            scope="contest_owner",
            target_username=username,
            contest_slug=contest_slug,
        )
        return _templates(request).TemplateResponse(
            request,
            "uberadmin/add_contest.html",
            _contest_creation_context(
                form_data=build_blank_contest_form(_five_days_ahead_at_0900()),
                languages=languages,
                errors=[],
                created_credential=credentials,
                email_delivery_message="Contest owner has no email address.",
                email_delivery_success=False,
            ),
            status_code=422,
        )

    email_service = request.app.state.email_service
    delivery = send_credentials_email(
        email_service,
        to_email=email.strip(),
        fullname=fullname,
        content=build_user_credentials_email_content(
            fullname=fullname,
            contest_name=contest_name,
            contest_login_url=_build_contest_login_url(request, contest_slug),
            username=username,
            password=password,
            sender_name=email_service.default_from_name or "Noca Contest",
        ),
    )
    await _record_credential_email_event(
        request,
        actor_user_id=_uberadmin.id,
        event_type="credential_email_sent" if delivery.success else "credential_email_failed",
        scope="contest_owner",
        target_username=username,
        contest_slug=contest_slug,
    )
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/add_contest.html",
        _contest_creation_context(
            form_data=build_blank_contest_form(_five_days_ahead_at_0900()),
            languages=languages,
            errors=[],
            created_credential=credentials,
            email_delivery_message=delivery.detail,
            email_delivery_success=delivery.success,
        ),
        status_code=200 if delivery.success else 422,
    )
