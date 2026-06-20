#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.models import Contest, UberAdmin, User
from web.services.htmx_redirect_service import build_auth_redirect_exception
from web.services.session_service import (
    build_session_auth_redirect_exception,
    get_validated_auth_token,
    mark_auth_refresh_eligible,
)


async def get_actor_from_token(
    request: Request,
    contest: Contest,
    session: AsyncSession,
) -> UberAdmin | User:
    """Resolve the authenticated actor for a contest-scoped route.

    Args:
        request: Incoming FastAPI request used to read the auth cookie and app auth service.
        contest: Contest the route is scoped to. Contest users must belong to this contest.
        session: Database session used to load the authenticated actor.

    Returns:
        The authenticated actor as either an `UberAdmin` or a contest `User`.

    Raises:
        HTTPException: With status code `302` redirecting to `/contests` when the auth cookie
            is missing, invalid, refers to another contest, or the actor cannot be found.

    Notes:
        UberAdmins are allowed on any contest route using this dependency. Contest users are
        only accepted when the token `contest_id` matches the provided contest.
    """
    from shared.enumerations import ALL_CONTEST_ROLES

    result = get_validated_auth_token(request)
    if result is None:
        raise await build_session_auth_redirect_exception(request, session, "/contests")

    if result.aud == RoleEnum.UBERADMIN.value:
        uberadmin = (
            await session.execute(select(UberAdmin).where(UberAdmin.username == result.sub))
        ).scalar_one_or_none()
        if uberadmin:
            mark_auth_refresh_eligible(request)
            return uberadmin
    elif result.aud in [role.value for role in ALL_CONTEST_ROLES]:
        if (result.extra_data or {}).get("contest_id") != contest.id:
            raise build_auth_redirect_exception(request, "/contests")
        user = (
            await session.execute(select(User).where(User.username == result.sub, User.contest_id == contest.id))
        ).scalar_one_or_none()
        if user:
            mark_auth_refresh_eligible(request)
            return user

    raise await build_session_auth_redirect_exception(request, session, "/contests")
