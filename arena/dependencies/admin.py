#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""FastAPI dependencies for restricting routes to Arena administrators and judges.

``require_arena_admin`` restricts to ``ARENA_ADMIN`` only.
``require_arena_judge_or_admin`` allows both ``ARENA_JUDGE`` and ``ARENA_ADMIN``.
``require_arena_problem_editor`` allows ``ARENA_ADMIN`` or any user whose ``can_edit``
flag is set (the admin-granted privilege to manage the Arena problem base).

These dependencies verify authentication and role authorization in a single step:

- Return the authenticated ``ArenaUser`` when the session is valid and the user
  holds the required role, narrowing the type to non-optional.
- Raise ``HTTPException(401)`` when no authenticated user is present.
- Raise ``HTTPException(403)`` when the authenticated user lacks the required role.
"""

from fastapi import Depends, HTTPException

from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from shared.enumerations import ArenaRole


async def require_arena_admin(
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> ArenaUser:
    """Enforce that the current request is made by an Arena administrator.

    Wraps ``get_current_arena_user`` and adds role-level authorization on top
    of the authentication check already performed by that dependency.

    Args:
        current_user: The resolved Arena user, or ``None`` if unauthenticated.

    Returns:
        The authenticated ``ArenaUser`` with ``ARENA_ADMIN`` role confirmed.

    Raises:
        HTTPException: 401 if no authenticated user is present.
        HTTPException: 403 if the authenticated user does not hold the
            ``ARENA_ADMIN`` role.
    """
    if current_user is None:
        raise HTTPException(status_code=401)
    if current_user.role != ArenaRole.ARENA_ADMIN:
        raise HTTPException(status_code=403)
    return current_user


async def require_arena_judge_or_admin(
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> ArenaUser:
    """Enforce that the current request is made by an Arena judge or administrator.

    Allows both ``ARENA_JUDGE`` and ``ARENA_ADMIN`` roles through.  Route
    handlers that need to differentiate between the two roles (e.g. to restrict
    judges to their own resources) should inspect ``current_user.role`` after
    this dependency resolves.

    Args:
        current_user: The resolved Arena user, or ``None`` if unauthenticated.

    Returns:
        The authenticated ``ArenaUser`` with ``ARENA_JUDGE`` or ``ARENA_ADMIN``
        role confirmed.

    Raises:
        HTTPException: 401 if no authenticated user is present.
        HTTPException: 403 if the authenticated user holds neither judge nor
            admin role.
    """
    if current_user is None:
        raise HTTPException(status_code=401)
    if current_user.role not in (ArenaRole.ARENA_ADMIN, ArenaRole.ARENA_JUDGE):
        raise HTTPException(status_code=403)
    return current_user


async def require_arena_problem_editor(
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> ArenaUser:
    """Enforce that the current request may add/edit Arena problems.

    Allows ``ARENA_ADMIN`` users (who may always manage the problem base) and any
    user whose ``can_edit`` flag has been granted by an administrator, regardless
    of role. Judges without ``can_edit`` are rejected: managing classes and
    problem sets is a separate privilege from managing the problem base.

    Args:
        current_user: The resolved Arena user, or ``None`` if unauthenticated.

    Returns:
        The authenticated ``ArenaUser`` allowed to manage Arena problems.

    Raises:
        HTTPException: 401 if no authenticated user is present.
        HTTPException: 403 if the user is neither an admin nor ``can_edit``.
    """
    if current_user is None:
        raise HTTPException(status_code=401)
    if current_user.role != ArenaRole.ARENA_ADMIN and not current_user.can_edit:
        raise HTTPException(status_code=403)
    return current_user
