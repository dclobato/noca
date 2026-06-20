#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Site-related validation helpers for contest users."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import User
from web.services.site_service import create_site, get_site_by_name_in_contest, get_site_in_contest

from .models import _ROLE_EXPORT_MAP


def role_requires_site(role: RoleEnum) -> bool:
    """Return whether a contest user role must be assigned to a site."""
    return role in {RoleEnum.TEAM, RoleEnum.STAFF}


def validate_role_site_requirement(role: RoleEnum, site_id: str | None) -> None:
    """Enforce required site assignment for TEAM and STAFF roles."""
    if role_requires_site(role) and not site_id:
        raise ValueError(f"{role.value.title()} users must have a site assigned.")


async def resolve_site_for_user(
    session: AsyncSession,
    contest: Contest,
    *,
    role: RoleEnum,
    site_id: str | None,
) -> Site | None:
    """Resolve a contest site from a submitted site id."""
    validate_role_site_requirement(role, site_id)
    site = await get_site_in_contest(session, contest, site_id)
    if site_id is not None and site is None:
        raise ValueError("Selected site does not belong to this contest.")
    return site


async def resolve_or_create_import_site(
    session: AsyncSession,
    contest: Contest,
    *,
    role: RoleEnum,
    raw_site: object,
) -> Site | None:
    """Resolve or create a site referenced by a batch import row."""
    if raw_site is None:
        validate_role_site_requirement(role, None)
        return None

    site_name = str(raw_site).strip()
    validate_role_site_requirement(role, site_name or None)
    if not site_name:
        return None

    existing_site = await get_site_by_name_in_contest(session, contest, site_name)
    if existing_site is not None:
        return existing_site

    site = await create_site(session, contest, site_name)
    await session.flush()
    return site


def build_user_export_row(user: User) -> dict[str, str]:
    """Build one import-compatible export row for a contest user."""
    row: dict[str, str] = {
        "username": user.username,
        "fullname": user.fullname,
        "role": _ROLE_EXPORT_MAP[user.role],
    }
    if user.email_normalizado:
        row["email"] = user.email_normalizado
    if user.site is not None:
        row["site"] = user.site.sitename
    if user.location:
        row["location"] = user.location
    return row
