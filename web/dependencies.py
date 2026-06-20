#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ALL_CONTEST_ROLES, RoleEnum
from web.database import get_db
from web.models import Contest, UberAdmin, User
from web.services.actor_service import get_actor_from_token
from web.services.contest_service import ensure_contest_admin_or_uberadmin, get_contest_by_slug
from web.services.session_service import (
    build_session_auth_redirect_exception,
    get_validated_auth_token,
    mark_auth_refresh_eligible,
)


async def get_request_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> User:
    """Return the authenticated contest user for profile routes or redirect to `/login`."""
    result = get_validated_auth_token(request)
    if result is None or result.aud not in [role.value for role in ALL_CONTEST_ROLES]:
        raise await build_session_auth_redirect_exception(request, session, "/login")

    contest_id = (result.extra_data or {}).get("contest_id")
    if not contest_id:
        raise await build_session_auth_redirect_exception(request, session, "/login")

    user = (
        await session.execute(select(User).where(User.username == result.sub, User.contest_id == contest_id))
    ).scalar_one_or_none()
    if not user:
        raise await build_session_auth_redirect_exception(request, session, "/login")

    mark_auth_refresh_eligible(request)
    return user


async def get_avatar_viewer(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> tuple[UberAdmin | User, bool]:
    """Return the authenticated avatar viewer and whether that viewer is an UberAdmin."""
    result = get_validated_auth_token(request)
    if result is None:
        raise await build_session_auth_redirect_exception(request, session, "/login")

    if result.aud == RoleEnum.UBERADMIN.value:
        uberadmin = (
            await session.execute(select(UberAdmin).where(UberAdmin.username == result.sub))
        ).scalar_one_or_none()
        if not uberadmin:
            raise await build_session_auth_redirect_exception(request, session, "/login")
        if not uberadmin.is_enabled:
            raise await build_session_auth_redirect_exception(request, session, "/login")
        mark_auth_refresh_eligible(request)
        return uberadmin, True

    if result.aud not in [role.value for role in ALL_CONTEST_ROLES]:
        raise await build_session_auth_redirect_exception(request, session, "/login")

    contest_id = (result.extra_data or {}).get("contest_id")
    if not contest_id:
        raise await build_session_auth_redirect_exception(request, session, "/login")

    user = (
        await session.execute(select(User).where(User.username == result.sub, User.contest_id == contest_id))
    ).scalar_one_or_none()
    if not user:
        raise await build_session_auth_redirect_exception(request, session, "/login")

    mark_auth_refresh_eligible(request)
    return user, False


async def get_visible_user(
    request: Request,
    user_id: str,
    session: AsyncSession = Depends(get_db),
) -> User:
    """Load a user visible to the current viewer, enforcing same-contest access for non-UberAdmins."""
    viewer, is_uberadmin = await get_avatar_viewer(request, session)
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404)
    if not is_uberadmin:
        assert isinstance(viewer, User)
        if user.contest_id != viewer.contest_id:
            raise HTTPException(status_code=403)
    return user


@dataclass
class UserPhotoContext:
    """Resolved actor/target context for user photo and avatar routes.

    Args:
        actor: Authenticated actor viewing or mutating the target user's image.
        target_user: User whose image is being accessed.
        is_uberadmin: Whether the authenticated actor is an UberAdmin.
        is_self: Whether the authenticated actor is the same contest user as `target_user`.

    Returns:
        A dataclass instance containing the resolved actor and target.

    Raises:
        None.

    Notes:
        Same-contest visibility for non-UberAdmins is enforced before the
        context object is returned.
    """

    actor: UberAdmin | User
    target_user: User
    is_uberadmin: bool
    is_self: bool


async def get_user_photo_context(
    request: Request,
    user_id: str,
    session: AsyncSession = Depends(get_db),
) -> UserPhotoContext:
    """Resolve the actor and target user for photo and avatar access.

    Args:
        request: Incoming FastAPI request carrying the auth cookie.
        user_id: Target user primary key from the route path.
        session: Database session used to load the actor and target user.

    Returns:
        A `UserPhotoContext` describing the authenticated actor and target user.

    Raises:
        HTTPException: With status code `404` when the target user does not
            exist.
        HTTPException: With status code `403` when a non-UberAdmin tries to
            access a user from another contest.

    Notes:
        Authentication and redirect behavior are delegated to
        `get_avatar_viewer()`.
    """
    actor, is_uberadmin = await get_avatar_viewer(request, session)
    target_user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404)
    is_self = isinstance(actor, User) and actor.id == target_user.id
    if not is_uberadmin:
        assert isinstance(actor, User)
        if target_user.contest_id != actor.contest_id:
            raise HTTPException(status_code=403)
    return UserPhotoContext(
        actor=actor,
        target_user=target_user,
        is_uberadmin=is_uberadmin,
        is_self=is_self,
    )


async def get_uberadmin(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UberAdmin:
    """Return the authenticated UberAdmin for dashboard routes or redirect to `/contests`."""
    result = get_validated_auth_token(request)
    if result is None or result.aud != RoleEnum.UBERADMIN.value:
        raise await build_session_auth_redirect_exception(request, session, "/contests")

    uberadmin = (await session.execute(select(UberAdmin).where(UberAdmin.username == result.sub))).scalar_one_or_none()
    if not uberadmin:
        raise await build_session_auth_redirect_exception(request, session, "/contests")
    if not uberadmin.is_enabled:
        raise await build_session_auth_redirect_exception(request, session, "/contests")

    mark_auth_refresh_eligible(request)
    return uberadmin


@dataclass
class ContestAdminContext:
    """Resolved context for contest administration routes.

    Args:
        contest: Contest targeted by the current admin route.
        session: Database session associated with the current request.
        actor: Authenticated actor authorized to administer the contest.

    Returns:
        A dataclass instance bundling the contest, session, and actor.

    Raises:
        None.

    Notes:
        This context is returned by `get_contest_admin_context()` after admin
        authorization has already been enforced.
    """

    contest: Contest
    session: AsyncSession
    actor: UberAdmin | User


@dataclass
class ChiefJudgeContext:
    contest: Contest
    session: AsyncSession
    actor: User


async def get_contest_admin_context(
    request: Request,
    contest: Contest = Depends(get_contest_by_slug),
    session: AsyncSession = Depends(get_db),
) -> ContestAdminContext:
    """Resolve the authenticated admin-capable actor plus contest/session for admin routes."""
    actor = await get_actor_from_token(request, contest, session)
    ensure_contest_admin_or_uberadmin(actor)
    return ContestAdminContext(contest=contest, session=session, actor=actor)


@dataclass
class ContestContext:
    """Resolved context for general contest-scoped routes.

    Args:
        contest: Contest targeted by the current route.
        session: Database session associated with the current request.
        actor: Authenticated actor resolved from the request token for the contest.

    Returns:
        A dataclass instance bundling the contest, session, and actor.

    Raises:
        None.

    Notes:
        Unlike `ContestAdminContext`, this context does not imply admin
        privileges; callers may still need additional role checks.
    """

    contest: Contest
    session: AsyncSession
    actor: UberAdmin | User


def ensure_allowed_role(actor: UberAdmin | User, allowed_roles: tuple[RoleEnum, ...]) -> None:
    """Raise `403` when the authenticated actor's role is not in the allowed set."""
    if actor.role not in allowed_roles:
        raise HTTPException(status_code=403)


async def get_contest_context(
    request: Request,
    contest: Contest = Depends(get_contest_by_slug),
    session: AsyncSession = Depends(get_db),
) -> ContestContext:
    """Resolve the authenticated actor plus contest/session for general contest routes."""
    actor = await get_actor_from_token(request, contest, session)
    return ContestContext(contest=contest, session=session, actor=actor)


async def get_chief_judge_context(
    ctx: ContestContext = Depends(get_contest_context),
) -> ChiefJudgeContext:
    if not isinstance(ctx.actor, User):
        raise HTTPException(status_code=403)
    if ctx.contest.chief_judge_id is None or ctx.actor.id != ctx.contest.chief_judge_id:
        raise HTTPException(status_code=403)
    return ChiefJudgeContext(contest=ctx.contest, session=ctx.session, actor=ctx.actor)
