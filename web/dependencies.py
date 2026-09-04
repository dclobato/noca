#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ALL_CONTEST_ROLES, RoleEnum
from web.database import get_db
from web.models import Contest, UberAdmin, User
from web.services.actor_service import get_actor_from_token
from web.services.contest_service import ensure_contest_admin_or_uberadmin, get_contest_by_slug
from web.services.htmx_redirect_service import build_auth_redirect_exception
from web.services.session_service import (
    SESSION_EXPIRED_MESSAGE,
    _get_cached_auth_validation,
    build_session_auth_redirect_exception,
    get_validated_auth_token,
    mark_auth_refresh_eligible,
    safe_contest_next_url,
)

_WEB_PUBLIC_EXACT: frozenset[str] = frozenset({"/", "/contests", "/contests/past", "/login", "/health", "/favicon.ico"})
# ``/problem-set`` is public: the route itself gates on the contest being over
# with its problem set released, so unauthenticated visitors may download the
# materials of a contest whose author chose to publish them. ``/announcements``
# is the public announcement board: the list and every detail page are readable
# anonymously by decision (#138); management lives under ``/uberadmin``.
_WEB_PUBLIC_PREFIXES: tuple[str, ...] = ("/assets", "/static", "/problem-set", "/announcements")


def _is_public_web_path(path: str) -> bool:
    """Return whether the Web path is reachable without authentication."""
    if path in _WEB_PUBLIC_EXACT:
        return True
    if path.startswith("/c/"):
        parts = path.strip("/").split("/")
        return len(parts) == 3 and parts[2] == "login"
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _WEB_PUBLIC_PREFIXES)


async def enforce_web_default_auth(request: Request) -> None:
    """Require authentication for every non-public Web route by default."""
    path = request.url.path
    if _is_public_web_path(path):
        return
    result = get_validated_auth_token(request)
    if result is not None:
        mark_auth_refresh_eligible(request)
        return

    validation = _get_cached_auth_validation(request)
    if validation is not None and validation.status == "expired":
        from fastapi_flash import FlashCategory, FlashService

        FlashService(request).flash(SESSION_EXPIRED_MESSAGE, FlashCategory.WARNING)

    if path.startswith("/c/"):
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[1]:
            login_url = f"/c/{parts[1]}/login"
            destination = _contest_return_destination(request, parts[1])
            if destination:
                login_url = f"{login_url}?next={quote(destination, safe='/?=&%')}"
            raise build_auth_redirect_exception(request, login_url)
    raise build_auth_redirect_exception(request, "/login")


def _contest_return_destination(request: Request, slug: str) -> str | None:
    """Return the page a contest login should come back to, or ``None``.

    A ``GET`` names its own page. A ``POST`` names a save target that cannot be
    revisited with a ``GET``, so the same-origin ``Referer`` -- the form's page,
    where a browser draft (``noca-form-draft.js``) waits to be restored -- is
    used instead. Only paths inside this contest qualify; the login route
    re-validates whatever it is handed.
    """
    if request.method == "GET":
        candidate = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    else:
        referer = request.headers.get("referer", "")
        parsed = urlsplit(referer)
        if not parsed.path or (parsed.netloc and parsed.netloc != request.url.netloc):
            return None
        candidate = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return safe_contest_next_url(slug, candidate, default=None)


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


@dataclass
class UserMediaContext:
    """Resolved actor/target context for user media routes.

    Args:
        actor: Authenticated actor viewing or mutating the target user's image.
        target_user: User whose image is being accessed.
        session: Request-scoped database session that loaded both users.
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
    session: AsyncSession
    is_uberadmin: bool
    is_self: bool


async def get_user_media_context(
    request: Request,
    user_id: str,
    session: AsyncSession = Depends(get_db),
) -> UserMediaContext:
    """Resolve the actor and target user for media access.

    Args:
        request: Incoming FastAPI request carrying the auth cookie.
        user_id: Target user primary key from the route path.
        session: Database session used to load the actor and target user.

    Returns:
        A `UserMediaContext` describing the authenticated actor and target user.

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
    return UserMediaContext(
        actor=actor,
        target_user=target_user,
        session=session,
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
