#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""UberAdmin route for permanent inactive-contest removal."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi_flash import FlashCategory, FlashDep

from shared.services.valkey_service import ContestValkeyPurgeError
from web.config import settings
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
from web.services.password_service import password_matches

router = APIRouter(prefix="/uberadmin", tags=["uberadmin"])
logger = logging.getLogger(__name__)


@router.post("/contests/{contest_id}/remove", name="uberadmin_remove_contest")
async def remove_contest(
    request: Request,
    contest_id: str,
    flash: FlashDep,
    password: Annotated[str, Form()] = "",
    uberadmin: UberAdmin = Depends(get_uberadmin),
) -> RedirectResponse:
    """Reconfirm the actor and permanently remove one inactive contest."""
    redirect = RedirectResponse(
        url=str(request.url_for("uberadmin_inactive_contests")),
        status_code=303,
    )
    if not password_matches(uberadmin, password):
        flash("Password confirmation is incorrect. No data was removed.", FlashCategory.DANGER)
        return redirect

    try:
        async with request.app.state.db_session() as session:
            result = await remove_inactive_contest(
                session,
                contest_id=contest_id,
                actor_uberadmin_id=uberadmin.id,
                valkey_runtime=request.app.state.valkey_runtime,
                statement_dir=settings.PROBLEM_STATEMENT_DIR,
                testcase_dir=settings.PROBLEM_TESTCASE_DIR,
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
