#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read-side helpers for contest user management."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import User
from web.services.site_service import list_contest_sites

from .models import ContestUserGroups, RoleUserGroups, SiteUserGroup
from .validation import normalize_username


def _user_sort_key(user: User) -> tuple[str, str, str]:
    """Return a stable presentation sort key for contest users."""
    return ((user.fullname or "").casefold(), user.username.casefold(), user.id)


def group_users_by_site(users: list[User]) -> RoleUserGroups:
    """Split users into flat no-site rows and ordered site groups."""
    ungrouped_users: list[User] = []
    site_groups_by_id: dict[str, SiteUserGroup] = {}

    for user in sorted(users, key=_user_sort_key):
        if user.site is None:
            ungrouped_users.append(user)
            continue

        group = site_groups_by_id.get(user.site.id)
        if group is None:
            group = SiteUserGroup(
                key=user.site.id,
                label=user.site.sitename,
                normalized_name=user.site.sitename_normalized,
                users=[],
            )
            site_groups_by_id[user.site.id] = group
        group.users.append(user)

    site_groups = sorted(
        site_groups_by_id.values(),
        key=lambda group: (group.normalized_name, group.label.casefold(), group.key),
    )
    return RoleUserGroups(ungrouped_users=ungrouped_users, site_groups=site_groups)


async def get_contest_user_groups(session: AsyncSession, contest: Contest) -> ContestUserGroups:
    """Load and group contest users by role."""
    members = (
        (await session.execute(select(User).where(User.contest_id == contest.id).options(selectinload(User.site))))
        .scalars()
        .all()
    )

    return ContestUserGroups(
        admin_users=group_users_by_site([user for user in members if user.role == RoleEnum.ADMIN]),
        judge_users=group_users_by_site([user for user in members if user.role == RoleEnum.JUDGE]),
        staff_users=group_users_by_site([user for user in members if user.role == RoleEnum.STAFF]),
        team_users=group_users_by_site([user for user in members if user.role == RoleEnum.TEAM]),
        user_users=group_users_by_site([user for user in members if user.role == RoleEnum.USER]),
    )


async def count_contest_teams(session: AsyncSession, contest: Contest) -> int:
    """Return the number of TEAM-role users enrolled in the contest.

    A cheap `COUNT(*)`, distinct from `active` teams (those with at least one
    submission): this is the reports page's Highlights "enrolled" figure, not
    a percentage denominator -- `Highlights.most_solved`/`least_solved` still
    key off active teams alone.
    """
    count = await session.scalar(
        select(func.count()).select_from(User).where(User.contest_id == contest.id, User.role == RoleEnum.TEAM)
    )
    return int(count or 0)


async def count_teams_by_site(session: AsyncSession, contest: Contest) -> dict[str, int]:
    """Return TEAM-role user counts per site, keyed by site id.

    A team with no site is excluded (there is no site to attribute it to);
    `count_contest_teams` remains the source of the unscoped total. One
    grouped query, used by the reports page to label each site tile without
    a query per site.
    """
    result = await session.execute(
        select(User.site_id, func.count())
        .where(User.contest_id == contest.id, User.role == RoleEnum.TEAM, User.site_id.is_not(None))
        .group_by(User.site_id)
    )
    return {str(site_id): int(count) for site_id, count in result.all()}


async def get_user_in_contest(
    session: AsyncSession,
    contest: Contest,
    user_id: str,
) -> User | None:
    """Load a contest user by ID within a specific contest."""
    result = await session.execute(select(User).where(User.id == user_id, User.contest_id == contest.id))
    return result.scalar_one_or_none()


async def get_user_by_username_in_contest(
    session: AsyncSession,
    contest: Contest,
    username: str,
) -> User | None:
    """Load a contest user by normalized username within a specific contest."""
    username = normalize_username(username)
    result = await session.execute(select(User).where(User.username == username, User.contest_id == contest.id))
    return result.scalar_one_or_none()


async def list_contest_sites_for_form(session: AsyncSession, contest: Contest) -> list[tuple[str, str]]:
    """Return contest sites formatted for select options."""
    sites = await list_contest_sites(session, contest.id)
    return [(site.id, site.sitename) for site in sites]


async def list_users_for_export(session: AsyncSession, contest: Contest) -> list[User]:
    """Return contest users ordered for export."""
    result = await session.execute(
        select(User).options(selectinload(User.site)).where(User.contest_id == contest.id).order_by(User.username)
    )
    return list(result.scalars().all())
