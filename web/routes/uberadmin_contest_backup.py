#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Uberadmin routes for full contest backup export and import."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from shared.services.admin_audit import record_admin_action
from web.config import settings
from web.database import get_db
from web.dependencies import get_uberadmin
from web.models.contest import Contest
from web.models.users import UberAdmin
from web.services.contest_backup_service import (
    MAX_ARCHIVE_BYTES,
    ContestBackupError,
    backup_filename,
    build_contest_backup,
    ensure_contest_exportable,
    import_contest_backup,
)
from web.services.contest_service import get_contest_by_id
from web.services.password_confirm_throttle import confirm_password, render_lockout

router = APIRouter(prefix="/uberadmin", tags=["uberadmin"])
UberAdminDep = Annotated[UberAdmin, Depends(get_uberadmin)]


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


async def _prepare_export_snapshot(session: AsyncSession) -> None:
    """Use one repeatable PostgreSQL snapshot for all backup metadata reads."""
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))


async def _record_export_audit(
    session: AsyncSession,
    request: Request,
    uberadmin: UberAdmin,
    contest: Contest,
    *,
    want_hashes: bool,
    want_media: bool,
) -> bool:
    """Audit any sensitive-payload backup export; return whether a row was written.

    Password hashes and user media both carry sensitive data (offline-crackable
    credentials and participant PII respectively), so each opt-in is recorded as
    its own admin-action event once the archive is known to have built.
    """
    audited = False
    if want_hashes:
        await record_admin_action(
            session,
            request,
            module="web",
            actor_user_id=uberadmin.id,
            actor_label=uberadmin.username,
            action="contest_backup_export_password_hashes",
            target_type="contest",
            target_id=contest.id,
            detail=f"Created contest backup including password hashes for {contest.login_slug!r}.",
            severity="warning",
        )
        audited = True
    if want_media:
        await record_admin_action(
            session,
            request,
            module="web",
            actor_user_id=uberadmin.id,
            actor_label=uberadmin.username,
            action="contest_backup_export_user_media",
            target_type="contest",
            target_id=contest.id,
            detail=f"Created contest backup including user media for {contest.login_slug!r}.",
            severity="warning",
        )
        audited = True
    return audited


async def _save_upload_limited(upload: UploadFile, handle: int) -> None:
    """Copy an upload to an open file descriptor with a compressed-size ceiling."""
    written = 0
    with os.fdopen(handle, "wb") as buffer:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_ARCHIVE_BYTES:
                raise ContestBackupError("Backup ZIP exceeds the compressed upload size limit.")
            buffer.write(chunk)


@router.get("/contests/{contest_id}/export", response_class=HTMLResponse, name="uberadmin_export_contest_form")
async def uberadmin_export_contest_form(
    request: Request,
    contest_id: str,
    uberadmin: UberAdminDep,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the contest backup export options form."""
    contest = await get_contest_by_id(session, contest_id)
    if contest is None:
        return _templates(request).TemplateResponse(
            request, "uberadmin/export_contest.html", {"contest": None}, status_code=404
        )
    try:
        ensure_contest_exportable(contest)
    except ContestBackupError as exc:
        return _templates(request).TemplateResponse(
            request,
            "uberadmin/export_contest.html",
            {"current_user": uberadmin, "contest": contest, "error": str(exc)},
            status_code=409,
        )
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/export_contest.html",
        {"current_user": uberadmin, "contest": contest, "error": None},
    )


@router.post("/contests/{contest_id}/export", name="uberadmin_export_contest")
async def uberadmin_export_contest(
    request: Request,
    contest_id: str,
    uberadmin: UberAdminDep,
    include_password_hashes: Annotated[str, Form()] = "no",
    include_media: Annotated[str, Form()] = "no",
    reconfirm_password: Annotated[str, Form()] = "",
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Build and stream the contest backup archive as a file download."""
    want_hashes = include_password_hashes == "yes"
    want_media = include_media == "yes"

    await _prepare_export_snapshot(session)
    contest = await get_contest_by_id(session, contest_id)
    if contest is None:
        return _templates(request).TemplateResponse(
            request, "uberadmin/export_contest.html", {"contest": None}, status_code=404
        )

    try:
        ensure_contest_exportable(contest)
    except ContestBackupError as exc:
        return _templates(request).TemplateResponse(
            request,
            "uberadmin/export_contest.html",
            {"current_user": uberadmin, "contest": contest, "error": str(exc)},
            status_code=409,
        )

    if want_hashes:
        confirmation = await confirm_password(
            request, session, actor=uberadmin, password=reconfirm_password, action="contest_export_hashes"
        )
        if confirmation.locked:
            return render_lockout(
                request,
                retry_after_seconds=confirmation.retry_after_seconds,
                back_url=str(request.url_for("uberadmin_export_contest_form", contest_id=contest_id)),
                back_label="Back to the export form",
            )
    else:
        confirmation = None
    if confirmation is not None and not confirmation.ok:
        return _templates(request).TemplateResponse(
            request,
            "uberadmin/export_contest.html",
            {
                "current_user": uberadmin,
                "contest": contest,
                "error": "Password reconfirmation failed. Exporting password hashes requires your password.",
            },
            status_code=422,
        )

    handle, temp_name = tempfile.mkstemp(suffix=".zip", prefix="noca-contest-backup-")
    os.close(handle)
    dest_path = Path(temp_name)
    try:
        await build_contest_backup(
            session,
            contest,
            dest_path,
            include_password_hashes=want_hashes,
            include_media=want_media,
        )
        if await _record_export_audit(
            session, request, uberadmin, contest, want_hashes=want_hashes, want_media=want_media
        ):
            await session.commit()
    except ContestBackupError as exc:
        dest_path.unlink(missing_ok=True)
        return _templates(request).TemplateResponse(
            request,
            "uberadmin/export_contest.html",
            {"current_user": uberadmin, "contest": contest, "error": str(exc)},
            status_code=422,
        )
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise

    filename = backup_filename(contest.login_slug)

    return FileResponse(
        path=str(dest_path),
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(dest_path.unlink, missing_ok=True),
    )


@router.get("/contests/import", response_class=HTMLResponse, name="uberadmin_import_contest_form")
async def uberadmin_import_contest_form(
    request: Request,
    uberadmin: UberAdminDep,
) -> HTMLResponse:
    """Render the contest backup import form."""
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/import_contest.html",
        {"current_user": uberadmin, "form_data": {"new_name": "", "new_slug": ""}, "error": None},
    )


@router.post("/contests/import", name="uberadmin_import_contest")
async def uberadmin_import_contest(
    request: Request,
    flash: FlashDep,
    uberadmin: UberAdminDep,
    backup_file: Annotated[UploadFile, File()],
    new_name: Annotated[str, Form()] = "",
    new_slug: Annotated[str, Form()] = "",
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Restore a contest from an uploaded backup archive under a new name/slug."""
    handle, temp_name = tempfile.mkstemp(suffix=".zip", prefix="noca-contest-import-")
    zip_path = Path(temp_name)
    try:
        await _save_upload_limited(backup_file, handle)

        result = await import_contest_backup(
            session,
            zip_path,
            actor_uberadmin=uberadmin,
            new_name=new_name,
            new_slug=new_slug,
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
            statement_dir=settings.PROBLEM_STATEMENT_DIR,
        )
    except ContestBackupError as exc:
        return _templates(request).TemplateResponse(
            request,
            "uberadmin/import_contest.html",
            {
                "current_user": uberadmin,
                "form_data": {"new_name": new_name, "new_slug": new_slug},
                "error": str(exc),
            },
            status_code=422,
        )
    finally:
        await backup_file.close()
        zip_path.unlink(missing_ok=True)

    flash(
        f"Contest {result.contest_name!r} restored with {result.problem_count} problems, "
        f"{result.user_count} users, and {result.submission_count} submissions.",
        FlashCategory.SUCCESS,
    )
    return RedirectResponse(url=str(request.url_for("uberadmin_dashboard")), status_code=303)
