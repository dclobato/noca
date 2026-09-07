#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import json
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from shared.services.contest_report_cache import invalidate_contest_report_cache
from shared.services.security_events import record_request_security_event
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_user_helpers import _build_contest_login_url, _html, _render_download_json
from web.services.assorted_utils import slugfy
from web.services.contest_user_service import batch_import_users, parse_batch_upload
from web.services.user_credentials_email_service import (
    CredentialEmailSendResult,
    build_user_credentials_email_content,
    email_actor_key,
    send_credentials_email,
)

router = APIRouter(prefix="/c/{slug}/admin/users", tags=["contest_admin_users"])


@router.get("/batch", response_class=HTMLResponse)
async def batch_import_form(
    request: Request, ctx: ContestAdminContext = Depends(get_contest_admin_context)
) -> HTMLResponse:
    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/users/batch_import.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "result": None,
                "download_json_str": None,
                "error": None,
                "is_locked": ctx.contest.is_past,
                "has_emailable_credentials": False,
                "email_delivery_summary": None,
            },
        )
    )


@router.post("/batch", response_class=HTMLResponse)
async def batch_import_submit(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    file: UploadFile = File(...),
    restrict_session: str = Form(""),
) -> HTMLResponse:
    templates = request.app.state.templates
    content = await file.read()

    def _render_error(error_msg: str) -> HTMLResponse:
        return _html(
            templates.TemplateResponse(
                request,
                "admin/users/batch_import.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "result": None,
                    "download_json_str": None,
                    "error": error_msg,
                    "is_locked": ctx.contest.is_past,
                    "has_emailable_credentials": False,
                    "email_delivery_summary": None,
                },
                status_code=422,
            )
        )

    try:
        users_data = parse_batch_upload(ctx.contest.login_slug, file.filename or "", content)
        result = await batch_import_users(
            ctx.session,
            ctx.contest,
            ctx.actor,
            users_data,
            allow_concurrent_login=not restrict_session,
        )
    except HTTPException as exc:
        return _render_error(str(exc.detail))
    except ValueError as exc:
        return _render_error(str(exc))

    await invalidate_contest_report_cache(getattr(request.app.state, "valkey_runtime", None), str(ctx.contest.id))

    downloadable_users = [
        {
            "username": entry.username,
            "fullname": entry.fullname,
            "role": entry.role,
            "password": entry.password,
            "email": entry.email,
            "site": entry.site,
            "location": entry.location,
        }
        for entry in result.results
        if entry.status in {"created", "updated"} and entry.password is not None
    ]
    results_payload = {
        "contest-slug": ctx.contest.login_slug,
        "created": result.created,
        "updated": result.updated,
        "failed": result.failed,
        "skipped": result.skipped,
        "results": [asdict(entry) for entry in result.results],
        "downloadable_users": downloadable_users,
    }
    download_json_str = json.dumps(results_payload, ensure_ascii=True, indent=2)
    has_emailable_credentials = any(user.get("email") and user.get("password") for user in downloadable_users)

    return _html(
        templates.TemplateResponse(
            request,
            "admin/users/batch_import.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "result": result,
                "download_json_str": download_json_str,
                "error": None,
                "is_locked": ctx.contest.is_past,
                "has_emailable_credentials": has_emailable_credentials,
                "email_delivery_summary": None,
            },
        )
    )


@router.post("/batch/results.json")
async def download_batch_results(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    results_json: str = Form(...),
) -> Response:
    try:
        payload = json.loads(results_json)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON in results_json.") from exc

    if not isinstance(payload, dict) or payload.get("contest-slug") != ctx.contest.login_slug:
        raise HTTPException(status_code=400, detail="Invalid results payload.")

    safe_slug = slugfy(ctx.contest.login_slug)
    content = json.dumps(payload, ensure_ascii=True, indent=2)
    return _render_download_json(content, f"noca-batch-{safe_slug}.json")


@router.post("/batch/credentials/email", response_class=HTMLResponse)
async def send_batch_credentials_email(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    results_json: str = Form(...),
) -> HTMLResponse:
    templates = request.app.state.templates
    try:
        payload = json.loads(results_json)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON in results_json.") from exc

    if not isinstance(payload, dict) or payload.get("contest-slug") != ctx.contest.login_slug:
        raise HTTPException(status_code=400, detail="Invalid results payload.")

    downloadable_users = payload.get("downloadable_users")
    if not isinstance(downloadable_users, list):
        raise HTTPException(status_code=400, detail="Invalid downloadable users payload.")

    email_service = request.app.state.email_service
    actor_key = email_actor_key(ctx.actor)
    sent = 0
    queued = 0
    failed = 0
    skipped = 0
    budget_stop: CredentialEmailSendResult | None = None
    failure_messages: list[str] = []
    delivery_by_username: dict[str, tuple[str, str]] = {}

    for index, user_data in enumerate(downloadable_users):
        if not isinstance(user_data, dict):
            skipped += 1
            continue
        to_email = str(user_data.get("email") or "").strip()
        password = str(user_data.get("password") or "").strip()
        fullname = str(user_data.get("fullname") or "").strip()
        username = str(user_data.get("username") or "").strip()
        if not to_email or not password:
            skipped += 1
            continue
        if budget_stop is not None:
            # The budget refused an earlier row: the rest of the batch is not even
            # attempted, and each row says why so the retry button can pick it up.
            skipped += 1
            downloadable_users[index]["email_delivery_status"] = "budget_exceeded"
            downloadable_users[index]["email_delivery_detail"] = budget_stop.detail
            delivery_by_username[username] = ("budget_exceeded", budget_stop.detail)
            continue

        send_result = await send_credentials_email(
            email_service,
            to_email=to_email,
            fullname=fullname,
            content=build_user_credentials_email_content(
                fullname=fullname,
                contest_name=ctx.contest.contest_name,
                contest_login_url=_build_contest_login_url(request, ctx.contest.login_slug),
                username=username,
                password=password,
                sender_name=email_service.default_from_name or settings.BRAND_NAME,
            ),
            actor_key=actor_key,
        )
        if send_result.budget_exceeded:
            budget_stop = send_result
            skipped += 1
            failure_messages.append(
                f"Email budget exceeded after {sent + queued} messages; "
                f"try again in {send_result.retry_after_seconds} s."
            )
            downloadable_users[index]["email_delivery_status"] = "budget_exceeded"
            downloadable_users[index]["email_delivery_detail"] = send_result.detail
            delivery_by_username[username] = ("budget_exceeded", send_result.detail)
        elif send_result.success:
            status = "queued" if send_result.queued else "sent"
            if send_result.queued:
                queued += 1
            else:
                sent += 1
            downloadable_users[index]["email_delivery_status"] = status
            delivery_by_username[username] = (status, send_result.detail)
        else:
            failed += 1
            failure_messages.append(f"{username}: {send_result.detail}")
            downloadable_users[index]["email_delivery_status"] = "failed"
            downloadable_users[index]["email_delivery_detail"] = send_result.detail
            delivery_by_username[username] = ("failed", send_result.detail)

    for r in payload.get("results", []):
        if not isinstance(r, dict):
            continue
        uname = str(r.get("username") or "")
        if uname in delivery_by_username:
            dstatus, ddetail = delivery_by_username[uname]
            r["email_delivery_status"] = dstatus
            r["email_delivery_detail"] = ddetail if dstatus in ("failed", "budget_exceeded") else None

    summary = f"Email delivery: {sent} sent, {queued} queued, {failed} failed, {skipped} skipped."
    if failure_messages:
        summary = f"{summary} Failures: {'; '.join(failure_messages[:5])}"
    await record_request_security_event(
        ctx.session,
        request,
        module="web",
        event_type="credential_email_batch_completed",
        severity="warning" if failed or budget_stop is not None else "info",
        actor_user_id=ctx.actor.id,
        actor_label=ctx.actor.username,
        metadata={
            "scope": "contest_user_batch",
            "contest_slug": ctx.contest.login_slug,
            "sent": sent,
            "queued": queued,
            "failed": failed,
            "skipped": skipped,
            "budget_exceeded": budget_stop is not None,
        },
    )
    await ctx.session.commit()

    payload["downloadable_users"] = downloadable_users
    payload_str = json.dumps(payload, ensure_ascii=True, indent=2)
    has_emailable_credentials = any(
        isinstance(user_data, dict)
        and str(user_data.get("email") or "").strip()
        and str(user_data.get("password") or "").strip()
        and user_data.get("email_delivery_status") not in ("sent", "queued")
        for user_data in downloadable_users
    )
    return _html(
        templates.TemplateResponse(
            request,
            "admin/users/batch_import.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "result": {
                    "created": payload.get("created", 0),
                    "updated": payload.get("updated", 0),
                    "failed": payload.get("failed", 0),
                    "skipped": payload.get("skipped", 0),
                    "results": payload.get("results", []),
                },
                "download_json_str": payload_str,
                "error": None,
                "is_locked": ctx.contest.is_past,
                "has_emailable_credentials": has_emailable_credentials,
                "email_delivery_summary": summary,
            },
            status_code=200,
        )
    )
