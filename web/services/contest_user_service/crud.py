#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Create, update, and remove contest users."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User

from .models import EMAIL_UNSET
from .permissions import ensure_contest_user_add_or_edit_allowed, ensure_contest_user_remove_allowed
from .sites import resolve_site_for_user
from .validation import ensure_role_allowed, normalize_optional_email, normalize_username, resolve_password


async def create_user(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
    *,
    username: str,
    fullname: str,
    role: RoleEnum,
    password: str | None,
    email: str | None = None,
    site_id: str | None = None,
    location: str | None = None,
) -> tuple[User, str]:
    """Create a contest user and persist it immediately."""
    ensure_contest_user_add_or_edit_allowed(contest)
    ensure_role_allowed(role)
    username = normalize_username(username)

    actual_password = resolve_password(password)
    site = await resolve_site_for_user(session, contest, role=role, site_id=site_id)
    new_user = User(
        username=username,
        fullname=fullname,
        role=role,
        contest_id=contest.id,
        email_normalizado=None,
        site_id=site.id if site is not None else None,
        location=location or None,
    )
    new_user.site = site
    new_user.email_normalizado = normalize_optional_email(email)

    if isinstance(actor, UberAdmin):
        new_user.created_by_uberadmin_id = actor.id
    else:
        new_user.created_by_admin_id = actor.id

    new_user.password = actual_password

    session.add(new_user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "uq_users_contest_username" in str(exc):
            raise ValueError("A user with that username already exists in this contest.") from exc
        raise
    await session.refresh(new_user)

    return new_user, actual_password


async def update_user(
    session: AsyncSession,
    contest: Contest,
    user: User,
    *,
    fullname: str,
    role: RoleEnum,
    password: str | None = None,
    email: str | None | object = EMAIL_UNSET,
    site_id: str | None = None,
    location: str | None = None,
) -> str | None:
    """Update a contest user's profile fields and optional password."""
    ensure_contest_user_add_or_edit_allowed(contest)
    ensure_role_allowed(role)
    site = await resolve_site_for_user(session, contest, role=role, site_id=site_id)

    user.fullname = fullname
    user.role = role
    if email is not EMAIL_UNSET:
        user.email_normalizado = normalize_optional_email(None if email is None else str(email))
    user.site_id = site.id if site is not None else None
    user.site = site
    user.location = location or None

    actual_password: str | None = None
    if password:
        actual_password = resolve_password(password)
        user.password = actual_password

    await session.commit()
    return actual_password


async def remove_user(
    session: AsyncSession,
    contest: Contest,
    user: User,
) -> None:
    """Remove a contest user and persist the deletion."""
    ensure_contest_user_remove_allowed(contest)
    await session.delete(user)
    await session.commit()
