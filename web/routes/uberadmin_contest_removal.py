#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""UberAdmin route for permanent inactive-contest removal."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.problem_export_cache import export_cache_dir
from shared.services.valkey_service import ContestValkeyPurgeError
from web.config import settings
from web.database import get_db
from web.dependencies import get_uberadmin
from web.models.users import UberAdmin
from web.services.contest_removal_files import (
    ContestRemovalFinalizationError,
    ContestRemovalPathError,
)
from web.services.contest_removal_service import (
    ContestRemovalActiveError,
    ContestRemovalError,
    ContestRemovalNotFoundError,
    remove_inactive_contest,
)
from web.services.password_confirm_throttle import confirm_password, render_lockout

router = APIRouter(prefix="/uberadmin", tags=["uberadmin"])
logger = logging.getLogger(__name__)


@router.post("/contests/{contest_id}/remove", name="uberadmin_remove_contest")
async def remove_contest(
    request: Request,
    contest_id: str,
    flash: FlashDep,
    password: Annotated[str, Form()] = "",
    uberadmin: UberAdmin = Depends(get_uberadmin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Reconfirm the actor and permanently remove one inactive contest.

    The reconfirmation runs inside the session scope so its security-event row
    is persisted before any removal work, and a locked actor is refused before
    the password is even checked.
    """
    back_url = str(request.url_for("uberadmin_inactive_contests"))
    redirect = RedirectResponse(url=back_url, status_code=303)

    try:
        confirmation = await confirm_password(
            request, session, actor=uberadmin, password=password, action="contest_remove"
        )
        if confirmation.locked:
            return render_lockout(
                request,
                retry_after_seconds=confirmation.retry_after_seconds,
                back_url=back_url,
                back_label="Back to inactive contests",
            )
        if not confirmation.ok:
            flash("Password confirmation is incorrect. No data was removed.", FlashCategory.DANGER)
            return redirect

        result = await remove_inactive_contest(
            session,
            contest_id=contest_id,
            actor_uberadmin_id=uberadmin.id,
            actor_uberadmin_label=uberadmin.username,
            valkey_runtime=request.app.state.valkey_runtime,
            statement_dir=settings.PROBLEM_STATEMENT_DIR,
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
            export_cache_dir=(
                None
                if settings.PUBLIC_PROBLEM_PACK_PATH is None
                else export_cache_dir(settings.PUBLIC_PROBLEM_PACK_PATH)
            ),
        )
    except ContestRemovalNotFoundError:
        flash("Contest not found. No data was removed.", FlashCategory.DANGER)
    except ContestRemovalActiveError:
        flash("Active contests cannot be removed. No data was removed.", FlashCategory.DANGER)
    except ContestValkeyPurgeError:
        flash(
            "Valkey cleanup could not be verified. The contest and its files were not removed.",
            FlashCategory.DANGER,
        )
    except ContestRemovalFinalizationError:
        logger.exception("Contest file quarantine finalization failed for contest_id=%s", contest_id)
        flash(
            "The contest was removed, but its file quarantine could not be erased. "
            "Check the Web logs and storage permissions.",
            FlashCategory.WARNING,
        )
    except ContestRemovalPathError, ContestRemovalError, OSError:
        logger.exception("Contest removal failed for contest_id=%s", contest_id)
        flash("Contest removal failed. Database changes and files were restored.", FlashCategory.DANGER)
    else:
        flash(
            f"Contest removed permanently ({result.problems_removed} problems, "
            f"{result.users_removed} users, {result.submissions_removed} submissions).",
            FlashCategory.SUCCESS,
        )
    return redirect
