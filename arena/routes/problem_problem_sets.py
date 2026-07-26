#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem-detail route for assigning a problem to a teacher's problem set."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import require_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import arena_problem_assignment_service
from arena.services.arena_problem_assignment_service import (
    ProblemAssignmentPermissionError,
    ProblemAssignmentProblemNotFoundError,
    ProblemAssignmentSelectionError,
)

router = APIRouter(tags=["arena-problems"])

_DEFAULT_SORT = "number_asc"


def _problem_detail_url(
    request: Request,
    *,
    arena_number: int,
    back_page: str,
    back_search: str,
    back_sort_by: str,
    back_category_slugs: list[str],
) -> str:
    """Build the problem detail redirect URL with list-return parameters."""
    params: dict[str, str] = {}
    if back_page and back_page != "1":
        params["back_page"] = back_page
    if back_search:
        params["back_search"] = back_search
    if back_sort_by and back_sort_by != _DEFAULT_SORT:
        params["back_sort_by"] = back_sort_by
    base_url = str(request.url_for("arena_problem_detail", arena_number=arena_number))
    scalar_query = urlencode(params)
    category_query = urlencode(
        {"back_category_slugs": back_category_slugs},
        doseq=True,
    )
    query_parts = [part for part in (scalar_query, category_query) if part]
    return f"{base_url}?{'&'.join(query_parts)}" if query_parts else base_url


@router.post(
    "/problems/{arena_number:int}/problem-sets",
    name="arena_problem_problem_set_add",
)
async def arena_problem_problem_set_add(
    request: Request,
    arena_number: int,
    flash: FlashDep,
    problem_set_id: Annotated[str, Form()],
    back_page: Annotated[str, Form()] = "1",
    back_search: Annotated[str, Form()] = "",
    back_sort_by: Annotated[str, Form()] = _DEFAULT_SORT,
    back_category_slugs: Annotated[list[str] | None, Form()] = None,
    current_user: ArenaUser = Depends(require_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Add the current problem to an eligible teacher-owned problem set."""
    redirect_url = _problem_detail_url(
        request,
        arena_number=arena_number,
        back_page=back_page,
        back_search=back_search,
        back_sort_by=back_sort_by,
        back_category_slugs=back_category_slugs or [],
    )
    now = datetime.now(UTC)
    try:
        await arena_problem_assignment_service.add_problem_to_problem_set(
            session,
            actor_id=current_user.id,
            actor_role=current_user.role,
            arena_number=arena_number,
            problem_set_id=problem_set_id,
            today=now.date(),
            now=now,
        )
        await session.commit()
        flash("Problem added to the problem set.", FlashCategory.SUCCESS)
    except ProblemAssignmentPermissionError as exc:
        await session.rollback()
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    except ProblemAssignmentProblemNotFoundError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Problem not found") from exc
    except ProblemAssignmentSelectionError as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.WARNING)
    return RedirectResponse(url=redirect_url, status_code=303)
