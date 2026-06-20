from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from web.models.site import Site
from web.models.users import UberAdmin, User
from web.services.site_service import normalize_site_name_key
from web.services.users_per_site_report_service import build_users_per_site_report


async def _make_site(session: AsyncSession, contest_id: str, name: str) -> Site:
    site = Site(
        sitename=name,
        sitename_normalized=normalize_site_name_key(name),
        contest_id=contest_id,
    )
    session.add(site)
    await session.flush()
    return site


async def _make_user(
    session: AsyncSession,
    contest_id: str,
    uberadmin: UberAdmin,
    username: str,
    fullname: str,
    role: RoleEnum,
    *,
    site_id: str | None = None,
    location: str | None = None,
) -> User:
    user = User(
        username=username,
        fullname=fullname,
        role=role,
        contest_id=contest_id,
        created_by_uberadmin_id=uberadmin.id,
        site_id=site_id,
        location=location,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_build_users_per_site_report_uses_ascii_table_blocks(
    session: AsyncSession,
    running_contest,
    uberadmin: UberAdmin,
) -> None:
    site = await _make_site(session, running_contest.id, "Campus A")
    chief_judge = await _make_user(
        session,
        running_contest.id,
        uberadmin,
        "judge-chief",
        "Judge Chief",
        RoleEnum.JUDGE,
        site_id=site.id,
        location="Room 101",
    )
    running_contest.chief_judge_id = chief_judge.id
    await _make_user(
        session,
        running_contest.id,
        uberadmin,
        "team-no-site",
        "Team No Site",
        RoleEnum.TEAM,
    )
    await _make_user(
        session,
        running_contest.id,
        uberadmin,
        "staff-campus",
        "Staff Campus",
        RoleEnum.STAFF,
        site_id=site.id,
        location="Lab",
    )
    await session.commit()

    filename, content = await build_users_per_site_report(session, running_contest, "https://example.test/login")

    assert filename == f"noca-users-per-site-{running_contest.login_slug}.md"
    assert "```text" in content
    assert "+========" in content
    assert "| Role" in content
    assert "| Username" in content
    assert "| TEAM" in content
    assert "| team-no-site" in content
    assert "## Site: Campus A (2 users)" in content
    assert "- Chiefjudge: judge-chief" in content
    assert "| judge-chief" in content
    assert "| Judge Chief" in content
    assert "| Room 101" in content
