#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import DBAPIError, IntegrityError

from shared.enumerations import RoleEnum
from shared.services.security_events import record_request_security_event
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_user_edit import edit_user_submit, export_users  # noqa: F401
from web.routes.contest_admin_user_helpers import (
    _build_contest_login_url,
    _credentials_payload,
    _friendly_user_create_error,
    _html,
    _render_download_json,
)
from web.services.assorted_utils import slugfy
from web.services.contest_user_service import (
    create_user,
    get_user_by_username_in_contest,
    list_contest_sites_for_form,
    validate_create_user_form,
)
from web.services.user_credentials_email_service import (
    build_user_credentials_email_content,
    send_credentials_email,
)

router = APIRouter(prefix="/c/{slug}/admin/users", tags=["contest_admin_users"])

_EMPTY_FORM = {"username": "", "fullname": "", "role": "", "password": "", "email": "", "site_id": ""}
_ROLE_LABELS = {
    RoleEnum.ADMIN: "admin",
    RoleEnum.JUDGE: "judge",
    RoleEnum.STAFF: "staff",
    RoleEnum.TEAM: "team",
    RoleEnum.USER: "user",
}


async def _record_credential_email_event(
    request: Request,
    ctx: ContestAdminContext,
    *,
    event_type: str,
    target_username: str,
) -> None:
    """Record a contest credential-email audit event without storing secrets."""
    await record_request_security_event(
        ctx.session,
        request,
        module="web",
        event_type=event_type,
        severity="info" if event_type == "credential_email_sent" else "warning",
        actor_user_id=ctx.actor.id,
        metadata={
            "scope": "contest_user",
            "contest_slug": ctx.contest.login_slug,
            "target_username": target_username,
        },
    )
    await ctx.session.commit()


@router.get("/new", response_class=HTMLResponse)
async def add_user_form(
    request: Request, ctx: ContestAdminContext = Depends(get_contest_admin_context)
) -> HTMLResponse:
    templates = request.app.state.templates
    sites = await list_contest_sites_for_form(ctx.session, ctx.contest)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/users/add.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "is_locked": ctx.contest.is_past,
                "success": False,
                "credentials": None,
                "errors": [],
                "form_data": dict(_EMPTY_FORM),
                "sites": sites,
                "email_delivery_message": None,
            },
        )
    )


@router.post("/new", response_class=HTMLResponse)
async def add_user_submit(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    username: str = Form(""),
    fullname: str = Form(""),
    role: str = Form(""),
    password: str = Form(""),
    email: str = Form(""),
    site_id: str = Form(""),
) -> HTMLResponse:
    templates = request.app.state.templates
    normalized_username, cleaned_fullname, normalized_email, role_enum, errors = validate_create_user_form(
        username,
        fullname,
        role,
        email,
    )
    form_data = {
        "username": normalized_username,
        "fullname": cleaned_fullname,
        "role": role,
        "password": password,
        "email": email,
        "site_id": site_id,
    }
    is_locked = ctx.contest.is_past
    sites = await list_contest_sites_for_form(ctx.session, ctx.contest)
    site_name_by_id = {current_site_id: site_name for current_site_id, site_name in sites}

    def _render(
        errors: list[str],
        success: bool = False,
        credentials: dict[str, str | None] | None = None,
        email_delivery_message: str | None = None,
    ) -> HTMLResponse:
        return _html(
            templates.TemplateResponse(
                request,
                "admin/users/add.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "is_locked": is_locked,
                    "success": success,
                    "credentials": credentials,
                    "errors": errors,
                    "form_data": form_data if not success else dict(_EMPTY_FORM),
                    "sites": sites,
                    "email_delivery_message": email_delivery_message,
                },
                status_code=422 if errors else 200,
            )
        )

    if errors:
        return _render(errors)
    assert role_enum is not None

    if await get_user_by_username_in_contest(ctx.session, ctx.contest, normalized_username):
        return _render(["A user with that username already exists in this contest."])

    try:
        new_user, plaintext_pw = await create_user(
            ctx.session,
            ctx.contest,
            ctx.actor,
            username=normalized_username,
            fullname=cleaned_fullname,
            role=role_enum,
            password=password.strip() or None,
            email=normalized_email,
            site_id=site_id.strip() or None,
        )
    except HTTPException as exc:
        return _render([str(exc.detail)])
    except ValueError as exc:
        return _render([str(exc)])
    except (IntegrityError, DBAPIError) as exc:
        await ctx.session.rollback()
        return _render([_friendly_user_create_error(exc)])
    except Exception:
        await ctx.session.rollback()
        return _render(["Could not create user. Please try again."])

    credentials = {
        "username": new_user.username,
        "fullname": new_user.fullname,
        "role": _ROLE_LABELS[new_user.role],
        "password": plaintext_pw,
        "email": new_user.email_normalizado,
        "site": site_name_by_id.get(new_user.site_id or "", ""),
    }
    return _render([], success=True, credentials=credentials)


@router.post("/credentials.json")
async def download_user_credentials(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    username: str = Form(...),
    fullname: str = Form(...),
    role: str = Form(...),
    password: str = Form(...),
    email: str = Form(""),
    site: str = Form(""),
    location: str = Form(""),
) -> Response:
    content = _credentials_payload(
        ctx.contest.login_slug,
        [
            {
                "username": username,
                "fullname": fullname,
                "role": role,
                "password": password,
                "email": email.strip() or None,
                "site": site.strip() or None,
                "location": location.strip() or None,
            }
        ],
    )
    safe_slug = slugfy(ctx.contest.login_slug)
    safe_user = slugfy(username, fallback="user")
    return _render_download_json(content, f"noca-credentials-{safe_slug}-{safe_user}.json")


@router.post("/credentials/email", response_class=HTMLResponse)
async def send_single_user_credentials_email(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    username: str = Form(...),
    fullname: str = Form(...),
    role: str = Form(...),
    password: str = Form(...),
    email: str = Form(""),
    site: str = Form(""),
    location: str = Form(""),
) -> HTMLResponse:
    templates = request.app.state.templates
    sites = await list_contest_sites_for_form(ctx.session, ctx.contest)
    credentials = {
        "username": username,
        "fullname": fullname,
        "role": role,
        "password": password,
        "email": email.strip() or "",
        "site": site,
        "location": location,
    }
    if not email.strip():
        await _record_credential_email_event(
            request,
            ctx,
            event_type="credential_email_skipped",
            target_username=username,
        )
        return _html(
            templates.TemplateResponse(
                request,
                "admin/users/add.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "is_locked": ctx.contest.is_past,
                    "success": True,
                    "credentials": credentials,
                    "errors": [],
                    "form_data": dict(_EMPTY_FORM),
                    "sites": sites,
                    "email_delivery_message": "User has no email address.",
                    "email_delivery_success": False,
                },
                status_code=422,
            )
        )

    email_service = request.app.state.email_service
    delivery = send_credentials_email(
        email_service,
        to_email=email.strip(),
        fullname=fullname,
        content=build_user_credentials_email_content(
            fullname=fullname,
            contest_name=ctx.contest.contest_name,
            contest_login_url=_build_contest_login_url(request, ctx.contest.login_slug),
            username=username,
            password=password,
            sender_name=email_service.default_from_name or "Noca Contest",
        ),
    )
    await _record_credential_email_event(
        request,
        ctx,
        event_type="credential_email_sent" if delivery.success else "credential_email_failed",
        target_username=username,
    )
    return _html(
        templates.TemplateResponse(
            request,
            "admin/users/add.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "is_locked": ctx.contest.is_past,
                "success": True,
                "credentials": credentials,
                "errors": [],
                "form_data": dict(_EMPTY_FORM),
                "sites": sites,
                "email_delivery_message": delivery.detail,
                "email_delivery_success": delivery.success,
            },
            status_code=200 if delivery.success else 422,
        )
    )
