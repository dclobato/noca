import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from shared.enumerations import RoleEnum
from web.dependencies import ContestAdminContext
from web.models.site import Site
from web.models.users import User
from web.routes.contest_admin_user import edit_user_submit, export_users
from web.services.contest_user_service import (
    batch_import_users,
    build_user_export_row,
    create_user,
    get_contest_user_groups,
    parse_batch_upload,
    update_user,
)
from web.services.site_service import normalize_site_name_key


def _route_request() -> Request:
    app = SimpleNamespace(state=SimpleNamespace(templates=None))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "app": app})


async def _create_site(session: AsyncSession, contest_id: str, name: str) -> Site:
    site = Site(
        sitename=name,
        sitename_normalized=normalize_site_name_key(name),
        contest_id=contest_id,
    )
    session.add(site)
    await session.flush()
    return site


@pytest.mark.asyncio
async def test_create_user_requires_site_for_team_and_staff(session: AsyncSession, running_contest, uberadmin) -> None:
    with pytest.raises(ValueError, match="Team users must have a site assigned."):
        await create_user(
            session,
            running_contest,
            uberadmin,
            username="team-new",
            fullname="Team New",
            role=RoleEnum.TEAM,
            password="TestPass1!",
            site_id=None,
        )

    with pytest.raises(ValueError, match="Staff users must have a site assigned."):
        await create_user(
            session,
            running_contest,
            uberadmin,
            username="staff-new",
            fullname="Staff New",
            role=RoleEnum.STAFF,
            password="TestPass1!",
            site_id=None,
        )


@pytest.mark.asyncio
async def test_create_and_update_allow_missing_site_for_non_required_roles(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="judge-new",
        fullname="Judge New",
        role=RoleEnum.JUDGE,
        password="TestPass1!",
        site_id=None,
    )

    assert user.site_id is None

    applied_password = await update_user(
        session,
        running_contest,
        user,
        fullname="Judge Updated",
        role=RoleEnum.JUDGE,
        password=None,
        site_id=None,
    )

    assert applied_password is None
    await session.refresh(user)
    assert user.fullname == "Judge Updated"
    assert user.site_id is None


@pytest.mark.asyncio
async def test_create_and_update_persist_optional_email(session: AsyncSession, running_contest, uberadmin) -> None:
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="judge-email",
        fullname="Judge Email",
        role=RoleEnum.JUDGE,
        password="TestPass1!",
        email="Judge.Email+One@Example.COM",
        site_id=None,
    )
    assert user.email_normalizado == "judge.email+one@example.com"

    await update_user(
        session,
        running_contest,
        user,
        fullname="Judge Email",
        role=RoleEnum.JUDGE,
        password=None,
        email="",
        site_id=None,
    )
    await session.refresh(user)
    assert user.email_normalizado is None


@pytest.mark.asyncio
async def test_update_user_requires_site_for_existing_team(session: AsyncSession, running_contest, uberadmin) -> None:
    site = await _create_site(session, running_contest.id, "Campus One")
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="team-edit",
        fullname="Team Edit",
        role=RoleEnum.TEAM,
        password="TestPass1!",
        site_id=site.id,
    )

    with pytest.raises(ValueError, match="Team users must have a site assigned."):
        await update_user(
            session,
            running_contest,
            user,
            fullname="Team Edit",
            role=RoleEnum.TEAM,
            password=None,
            site_id=None,
        )


@pytest.mark.asyncio
async def test_batch_import_creates_sites_and_matches_case_insensitively(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    existing_site = await _create_site(session, running_contest.id, "Campus A")

    result = await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [
            {"username": "team01", "fullname": "Team 01", "role": "team", "site": "campus a"},
            {"username": "staff01", "fullname": "Staff 01", "role": "staff", "site": "Campus B"},
            {"username": "judge01", "fullname": "Judge 01", "role": "judge"},
        ],
    )

    assert result.created == 3
    assert result.failed == 0
    assert [entry.site for entry in result.results] == ["Campus A", "Campus B", None]

    team = (
        await session.execute(select(User).where(User.contest_id == running_contest.id, User.username == "team01"))
    ).scalar_one()
    staff = (
        await session.execute(select(User).where(User.contest_id == running_contest.id, User.username == "staff01"))
    ).scalar_one()
    judge = (
        await session.execute(select(User).where(User.contest_id == running_contest.id, User.username == "judge01"))
    ).scalar_one()

    assert team.site_id == existing_site.id
    assert staff.site_id is not None
    assert judge.site_id is None

    sites = (
        (await session.execute(select(Site).where(Site.contest_id == running_contest.id).order_by(Site.sitename)))
        .scalars()
        .all()
    )
    assert [site.sitename for site in sites] == ["Campus A", "Campus B"]


@pytest.mark.asyncio
async def test_batch_import_requires_site_only_for_team_and_staff(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    result = await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [
            {"username": "team01", "fullname": "Team 01", "role": "team"},
            {"username": "user01", "fullname": "User 01", "role": "user"},
        ],
    )

    assert result.created == 1
    assert result.failed == 1
    assert result.results[0].status == "failed"
    assert result.results[0].detail == "Team users must have a site assigned."
    assert result.results[1].status == "created"
    assert result.results[1].site is None


def test_parse_batch_upload_accepts_legacy_and_site_csv_headers() -> None:
    legacy = parse_batch_upload(
        "contest",
        "users.csv",
        b"username,fullname,role,password\njudge01,Judge 01,judge,\n",
    )
    with_site = parse_batch_upload(
        "contest",
        "users.csv",
        b"username,fullname,role,password,site\nteam01,Team 01,team,,Campus A\n",
    )
    with_email = parse_batch_upload(
        "contest",
        "users.csv",
        b"username,fullname,role,password,email\njudge01,Judge 01,judge,,judge@example.com\n",
    )

    assert legacy == [{"username": "judge01", "fullname": "Judge 01", "role": "judge", "password": ""}]
    assert with_site == [
        {
            "username": "team01",
            "fullname": "Team 01",
            "role": "team",
            "password": "",
            "site": "Campus A",
        }
    ]
    assert with_email == [
        {
            "username": "judge01",
            "fullname": "Judge 01",
            "role": "judge",
            "password": "",
            "email": "judge@example.com",
        }
    ]


@pytest.mark.asyncio
async def test_build_user_export_row_omits_password_and_uses_site_name(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    site = await _create_site(session, running_contest.id, "Campus A")
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="team-export",
        fullname="Team Export",
        role=RoleEnum.TEAM,
        password="TestPass1!",
        site_id=site.id,
    )
    await session.refresh(user, ["site"])

    assert build_user_export_row(user) == {
        "username": "team-export",
        "fullname": "Team Export",
        "role": "team",
        "site": "Campus A",
    }


@pytest.mark.asyncio
async def test_build_user_export_row_includes_email_when_present(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="user-email-export",
        fullname="User Export",
        role=RoleEnum.USER,
        password="TestPass1!",
        email="user.export@example.com",
    )

    assert build_user_export_row(user) == {
        "username": "user-email-export",
        "fullname": "User Export",
        "role": "user",
        "email": "user.export@example.com",
    }


@pytest.mark.asyncio
async def test_get_contest_user_groups_keeps_no_site_users_flat_and_orders_site_groups(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    site_z = await _create_site(session, running_contest.id, "Zulu")
    site_a = await _create_site(session, running_contest.id, "Alpha")

    admin_no_site, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="admin-no-site",
        fullname="Admin No Site",
        role=RoleEnum.ADMIN,
        password="TestPass1!",
        site_id=None,
    )
    admin_alpha, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="admin-alpha",
        fullname="Admin Alpha",
        role=RoleEnum.ADMIN,
        password="TestPass1!",
        site_id=site_a.id,
    )
    admin_zulu, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="admin-zulu",
        fullname="Admin Zulu",
        role=RoleEnum.ADMIN,
        password="TestPass1!",
        site_id=site_z.id,
    )

    groups = await get_contest_user_groups(session, running_contest)

    assert [user.id for user in groups.admin_users.ungrouped_users] == [admin_no_site.id]
    assert [group.label for group in groups.admin_users.site_groups] == ["Alpha", "Zulu"]
    assert [group.users[0].id for group in groups.admin_users.site_groups] == [admin_alpha.id, admin_zulu.id]
    assert groups.admin_users.total_users == 3


@pytest.mark.asyncio
async def test_edit_user_submit_keeps_role_immutable_even_if_forged_role_is_posted(
    session: AsyncSession, running_contest, admin_user, judge_user
) -> None:
    ctx = ContestAdminContext(contest=running_contest, session=session, actor=admin_user)
    request = _route_request()
    flash_messages: list[tuple[str, object]] = []

    response = await edit_user_submit(
        request=request,
        user_id=judge_user.id,
        flash=lambda message, category: flash_messages.append((message, category)),
        ctx=ctx,
        fullname="Judge X Updated",
        email="",
        password="",
        site_id="",
        location="",
    )

    await session.refresh(judge_user)
    assert response.status_code == 303
    assert judge_user.role == RoleEnum.JUDGE
    assert judge_user.fullname == "Judge X Updated"
    assert flash_messages

    edit_template = (
        Path(__file__).resolve().parents[2] / "web" / "template" / "admin" / "users" / "edit.html"
    ).read_text()
    assert 'name="role"' not in edit_template
    assert "Role cannot be changed after the user is created." in edit_template


@pytest.mark.asyncio
async def test_edit_user_submit_flashes_validation_error_for_team_without_site(
    session: AsyncSession, running_contest, uberadmin, admin_user
) -> None:
    site = await _create_site(session, running_contest.id, "Campus One")
    team_user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="team-edit-route",
        fullname="Team Route",
        role=RoleEnum.TEAM,
        password="TestPass1!",
        site_id=site.id,
    )
    ctx = ContestAdminContext(contest=running_contest, session=session, actor=admin_user)
    request = _route_request()
    flash_messages: list[tuple[str, object]] = []
    team_user_id = team_user.id
    original_site_id = team_user.site_id

    response = await edit_user_submit(
        request=request,
        user_id=team_user.id,
        flash=lambda message, category: flash_messages.append((message, category)),
        ctx=ctx,
        fullname="Team User Updated",
        email="",
        password="",
        site_id="",
        location="",
    )

    refreshed_team_user = (await session.execute(select(User).where(User.id == team_user_id))).scalar_one()
    assert response.status_code == 303
    assert refreshed_team_user.site_id == original_site_id
    assert refreshed_team_user.fullname != "Team User Updated"
    assert any(message == "Team users must have a site assigned." for message, _ in flash_messages)


@pytest.mark.asyncio
async def test_edit_user_submit_flashes_validation_error_for_staff_without_site(
    session: AsyncSession, running_contest, uberadmin, admin_user
) -> None:
    site = await _create_site(session, running_contest.id, "Campus Two")
    staff_user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="staff-edit-route",
        fullname="Staff Route",
        role=RoleEnum.STAFF,
        password="TestPass1!",
        site_id=site.id,
    )
    ctx = ContestAdminContext(contest=running_contest, session=session, actor=admin_user)
    request = _route_request()
    flash_messages: list[tuple[str, object]] = []
    staff_user_id = staff_user.id
    original_site_id = staff_user.site_id

    response = await edit_user_submit(
        request=request,
        user_id=staff_user.id,
        flash=lambda message, category: flash_messages.append((message, category)),
        ctx=ctx,
        fullname="Staff User Updated",
        email="",
        password="",
        site_id="",
        location="",
    )

    refreshed_staff_user = (await session.execute(select(User).where(User.id == staff_user_id))).scalar_one()
    assert response.status_code == 303
    assert refreshed_staff_user.site_id == original_site_id
    assert refreshed_staff_user.fullname != "Staff User Updated"
    assert any(message == "Staff users must have a site assigned." for message, _ in flash_messages)


@pytest.mark.asyncio
async def test_export_users_route_returns_import_compatible_json_without_passwords(
    session: AsyncSession, running_contest, uberadmin, admin_user
) -> None:
    site = await _create_site(session, running_contest.id, "Campus A")
    await create_user(
        session,
        running_contest,
        uberadmin,
        username="team-export",
        fullname="Team Export",
        role=RoleEnum.TEAM,
        password="TestPass1!",
        email="team.export@example.com",
        site_id=site.id,
    )
    ctx = ContestAdminContext(contest=running_contest, session=session, actor=admin_user)
    response = await export_users(ctx=ctx)

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["contest-slug"] == running_contest.login_slug
    assert all("password" not in row for row in payload["users"])
    assert any(
        row
        == {
            "username": "team-export",
            "fullname": "Team Export",
            "role": "team",
            "email": "team.export@example.com",
            "site": "Campus A",
        }
        for row in payload["users"]
    )
