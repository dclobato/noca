#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Generates a markdown-formatted report of contest users grouped by site.
The report is intended for human review and later PDF conversion.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import User
from web.services.assorted_utils import render_prettytable
from web.services.site_service import list_contest_sites

# Role display order for the "no site" section
_ROLE_ORDER: list[RoleEnum] = [
    RoleEnum.ADMIN,
    RoleEnum.JUDGE,
    RoleEnum.STAFF,
    RoleEnum.TEAM,
    RoleEnum.USER,
]

_ROLE_SECTION_TITLES: dict[RoleEnum, str] = {
    RoleEnum.ADMIN: "Admins",
    RoleEnum.JUDGE: "Judges",
    RoleEnum.STAFF: "Staff",
    RoleEnum.TEAM: "Teams",
    RoleEnum.USER: "Users",
}


def _bool_label(value: bool) -> str:
    """Return YES or NO string for a boolean value.

    Args:
        value: Boolean flag to convert.

    Returns:
        "YES" if value is True, "NO" otherwise.
    """
    return "YES" if value else "NO"


def _location_display(user: User) -> str:
    """Return the user's location or a placeholder when unset.

    Args:
        user: Contest user whose location field is read.

    Returns:
        The location string if set, otherwise "Not assigned".
    """
    if user.location and user.location.strip():
        return user.location.strip()
    return "Not assigned"


def _pipe_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render an ASCII table with PrettyTable.

    Args:
        headers: Column header names.
        rows: Data rows, each a list of cell strings (same length as headers).

    Returns:
        Formatted ASCII table string (no trailing newline).
    """
    return render_prettytable(headers, rows)


async def build_users_per_site_report(
    session: AsyncSession,
    contest: Contest,
    login_url: str,
) -> tuple[str, str]:
    """Build a markdown report of contest users grouped by site.

    Args:
        session: Database session for loading users and sites.
        contest: Contest whose users are being reported.
        login_url: Absolute URL to the contest login page.

    Returns:
        A tuple of (filename, markdown_text).
    """
    # Load all contest users with their site relationship, ordered by username
    result = await session.execute(
        select(User).options(selectinload(User.site)).where(User.contest_id == contest.id).order_by(User.username)
    )
    all_users: list[User] = list(result.scalars().all())

    # Load sites ordered A-Z (by sitename_normalized)
    sites = await list_contest_sites(session, contest.id)

    # Determine chief judge username
    chief_judge_username: str | None = None
    if contest.chief_judge_id:
        for u in all_users:
            if u.id == contest.chief_judge_id:
                chief_judge_username = u.username
                break

    # Partition users by site
    users_no_site = [u for u in all_users if u.site_id is None]
    users_by_site_id: dict[str, list[User]] = {}
    for site in sites:
        users_by_site_id[site.id] = [u for u in all_users if u.site_id == site.id]

    lines: list[str] = []

    # ------------------------------------------------------------------ Header
    start_str = contest.local_start_time.strftime("%Y-%m-%d %H:%M")
    end_str = contest.local_end_time.strftime("%Y-%m-%d %H:%M")
    tz = contest.contest_timezone

    lines.append(f"# Contest {contest.contest_name} ({contest.login_slug})")
    lines.append("")
    lines.append(f"Login URL: {login_url}")
    lines.append(f"Starts at: {start_str} {tz}")
    lines.append(f"Duration: {contest.duration_minutes} minutes (expected to end at {end_str} {tz})")
    lines.append("Rules:")
    lines.append(f"- Are limits visible? **{_bool_label(contest.show_limits)}**")
    lines.append(f"- Time penalty for WA, TLE, MLE, OLE: **{contest.wa_penalty} minutes**")
    lines.append(f"- Compilation error adds penalty? **{_bool_label(contest.ce_adds_penalty)}**")
    lines.append(f"- Presentation error counts as Accept? **{_bool_label(contest.accept_pe)}**")
    lines.append("")

    # ------------------------------------------------- Users with no site
    lines.append("## Users with no site assigned")
    lines.append("")

    if not users_no_site:
        lines.append("No users without a site assignment.")
    else:
        # Sort by role order then username
        role_index = {role: i for i, role in enumerate(_ROLE_ORDER)}
        sorted_no_site = sorted(users_no_site, key=lambda u: (role_index.get(u.role, 99), u.username))
        rows = [[u.role.value, u.username, u.fullname] for u in sorted_no_site]
        lines.append("```text")
        lines.append(_pipe_table(["Role", "Username", "Fullname"], rows))
        lines.append("```")

    lines.append("")

    # ----------------------------------------------- Per-site sections
    for site in sites:
        site_users = users_by_site_id.get(site.id, [])
        total = len(site_users)

        lines.append(f"## Site: {site.sitename} ({total} users)")
        lines.append("")

        for role in _ROLE_ORDER:
            role_users = sorted(
                [u for u in site_users if u.role == role],
                key=lambda u: u.username,
            )
            count = len(role_users)
            section_title = _ROLE_SECTION_TITLES[role]

            lines.append(f"### {section_title} ({count})")
            lines.append("")

            if role == RoleEnum.JUDGE:
                cj_name = chief_judge_username if chief_judge_username else "none"
                lines.append(f"- Chiefjudge: {cj_name}")
                lines.append("")

            if not role_users:
                lines.append("No users with this role in this site")
            else:
                rows = [[u.username, u.fullname, _location_display(u)] for u in role_users]
                lines.append("```text")
                lines.append(_pipe_table(["Username", "Fullname", "Location"], rows))
                lines.append("```")

            lines.append("")

    filename = f"noca-users-per-site-{contest.login_slug}.md"
    return filename, "\n".join(lines)
