#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import User


def normalize_site_name(raw_name: str) -> str:
    return raw_name.strip()


def normalize_site_name_key(raw_name: str) -> str:
    return normalize_site_name(raw_name).casefold()


async def list_contest_sites(session: AsyncSession, contest_id: str) -> list[Site]:
    result = await session.execute(
        select(Site).where(Site.contest_id == contest_id).order_by(Site.sitename_normalized, Site.id)
    )
    return list(result.scalars().all())


def get_site_names_from_sites(sites: list[Site]) -> list[str]:
    return [site.sitename for site in sites]


async def list_contest_site_entries(session: AsyncSession, contest_id: str) -> list[dict[str, int | str]]:
    sites = await list_contest_sites(session, contest_id)
    counts_result = await session.execute(
        select(User.site_id, func.count(User.id))
        .where(User.contest_id == contest_id, User.site_id.is_not(None))
        .group_by(User.site_id)
    )
    counts_by_site_id = {str(site_id): int(count) for site_id, count in counts_result.all() if site_id is not None}
    return [
        {
            "name": site.sitename,
            "user_count": counts_by_site_id.get(site.id, 0),
        }
        for site in sites
    ]


def parse_site_names_payload(raw_payload: str) -> list[str]:
    if not raw_payload.strip():
        return []

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid site list payload.") from exc

    if not isinstance(payload, list):
        raise ValueError("Invalid site list payload.")

    names: list[str] = []
    for item in payload:
        if not isinstance(item, str):
            raise ValueError("Invalid site list payload.")
        names.append(item)
    return names


async def contest_has_sites(session: AsyncSession, contest_id: str) -> bool:
    count = await session.scalar(select(func.count()).select_from(Site).where(Site.contest_id == contest_id))
    return bool(count)


async def get_site_in_contest(session: AsyncSession, contest: Contest, site_id: str | None) -> Site | None:
    if site_id is None:
        return None
    result = await session.execute(select(Site).where(Site.id == site_id, Site.contest_id == contest.id))
    return result.scalar_one_or_none()


async def get_site_by_name_in_contest(session: AsyncSession, contest: Contest, raw_name: str | None) -> Site | None:
    if raw_name is None:
        return None
    cleaned_name = normalize_site_name(raw_name)
    if not cleaned_name:
        return None
    normalized_name = normalize_site_name_key(cleaned_name)
    result = await session.execute(
        select(Site).where(
            Site.contest_id == contest.id,
            Site.sitename_normalized == normalized_name,
        )
    )
    return result.scalar_one_or_none()


async def create_site(session: AsyncSession, contest: Contest, raw_name: str) -> Site:
    cleaned_name = normalize_site_name(raw_name)
    if not cleaned_name:
        raise ValueError("Site name is required.")

    normalized_name = normalize_site_name_key(cleaned_name)
    existing = await session.execute(
        select(Site).where(
            Site.contest_id == contest.id,
            Site.sitename_normalized == normalized_name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError("A site with that name already exists in this contest.")

    site = Site(
        sitename=cleaned_name,
        sitename_normalized=normalized_name,
        contest_id=contest.id,
    )
    session.add(site)
    return site


async def remove_site(session: AsyncSession, contest: Contest, site: Site) -> None:
    existing_sites = await list_contest_sites(session, contest.id)
    if len(existing_sites) <= 1:
        raise ValueError("Cannot remove the only remaining site.")

    blocked_users = (
        (
            await session.execute(
                select(User)
                .where(
                    User.site_id == site.id,
                    User.role.in_([RoleEnum.TEAM, RoleEnum.STAFF]),
                )
                .order_by(User.fullname, User.username)
            )
        )
        .scalars()
        .all()
    )
    if blocked_users:
        raise ValueError(f"Cannot remove site {site.sitename!r} while it still has TEAM or STAFF users assigned.")

    users_to_unassign = (
        (await session.execute(select(User).where(User.site_id == site.id).order_by(User.fullname, User.username)))
        .scalars()
        .all()
    )
    for user in users_to_unassign:
        user.site_id = None

    await session.delete(site)


async def sync_contest_sites(session: AsyncSession, contest: Contest, raw_site_names: list[str]) -> list[str]:
    existing_sites = await list_contest_sites(session, contest.id)

    submitted_names: list[str] = []
    submitted_by_key: dict[str, str] = {}
    for raw_name in raw_site_names:
        cleaned_name = normalize_site_name(raw_name)
        if not cleaned_name:
            raise ValueError("Site names cannot be blank.")
        key = normalize_site_name_key(cleaned_name)
        if key in submitted_by_key:
            raise ValueError(f"Site name {cleaned_name!r} is duplicated in this contest.")
        submitted_by_key[key] = cleaned_name
        submitted_names.append(cleaned_name)

    if not submitted_names:
        raise ValueError("At least one site is required.")

    existing_by_key = {site.sitename_normalized: site for site in existing_sites}

    for key, display_name in submitted_by_key.items():
        existing_site = existing_by_key.get(key)
        if existing_site is not None and existing_site.sitename != display_name:
            existing_site.sitename = display_name

    removed_sites = [site for site in existing_sites if site.sitename_normalized not in submitted_by_key]
    for site in removed_sites:
        blocked_users = (
            (
                await session.execute(
                    select(User)
                    .where(
                        User.site_id == site.id,
                        User.role.in_([RoleEnum.TEAM, RoleEnum.STAFF]),
                    )
                    .order_by(User.fullname, User.username)
                )
            )
            .scalars()
            .all()
        )
        if blocked_users:
            raise ValueError(f"Cannot remove site {site.sitename!r} while it still has TEAM or STAFF users assigned.")

        users_to_unassign = (
            (await session.execute(select(User).where(User.site_id == site.id).order_by(User.fullname, User.username)))
            .scalars()
            .all()
        )
        for user in users_to_unassign:
            user.site_id = None

        await session.delete(site)

    for key, display_name in submitted_by_key.items():
        if key in existing_by_key:
            continue
        session.add(
            Site(
                sitename=display_name,
                sitename_normalized=key,
                contest_id=contest.id,
            )
        )

    return submitted_names
