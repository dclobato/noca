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
    update_user_credentials,
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


async def _make_contest_user(
    session: AsyncSession,
    contest_id: str,
    uberadmin_id: str,
    *,
    username: str,
    role: RoleEnum = RoleEnum.USER,
    email: str | None = None,
    site_id: str | None = None,
) -> User:
    """Insert a contest user directly, bypassing the contest-state guards.

    Used to seed users on an already-finished contest, where ``create_user``
    is intentionally refused.
    """
    user = User(
        username=username,
        fullname=username.replace("-", " ").title(),
        role=role,
        contest_id=contest_id,
        created_by_uberadmin_id=uberadmin_id,
        site_id=site_id,
    )
    user.email_normalizado = email
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


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
        # Emitted always, not only when restricted: a missing field means "use
        # the import's default", so omitting the common value would let an
        # exported roster arrive carrying the destination's checkbox instead.
        "allow_concurrent_login": "true",
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
        "allow_concurrent_login": "true",
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
    assert "user_photo_submit" in edit_template
    assert "user_audio_submit" in edit_template
    assert "user_audio_remove" in edit_template
    assert '{% include "_partials/crop_modal.html" %}' in edit_template
    crop_modal_template = (
        Path(__file__).resolve().parents[2] / "web" / "template" / "_partials" / "crop_modal.html"
    ).read_text()
    assert 'id="cropModal"' in crop_modal_template
    # The cropper preview must ship a real placeholder src so it never renders as a
    # broken image before Cropper.js swaps in the selected file (issue #36).
    assert "crop-placeholder.svg" in crop_modal_template
    assert 'data-photo-preview-id="adminPhotoPreview"' in edit_template
    assert 'data-audio-preview-id="adminAudioPreview"' in edit_template
    assert 'id="adminPhotoUnsaved"' in edit_template
    assert 'id="adminAudioUnsaved"' in edit_template
    assert "image_max_file_size_mib" in edit_template
    assert "image_max_width" in edit_template
    assert "image_max_height" in edit_template
    assert "audio_max_file_size_mib" in edit_template


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
            "allow_concurrent_login": "true",
            "email": "team.export@example.com",
            "site": "Campus A",
        }
        for row in payload["users"]
    )


@pytest.mark.asyncio
async def test_update_user_credentials_allows_email_and_password_after_contest_end(
    session: AsyncSession, stopped_contest, uberadmin
) -> None:
    user = await _make_contest_user(
        session,
        stopped_contest.id,
        uberadmin.id,
        username="team-after-end",
        role=RoleEnum.USER,
        email="old@example.com",
    )
    original_fullname = user.fullname

    applied_password = await update_user_credentials(
        session,
        stopped_contest,
        user,
        email="New.Email+Reset@Example.COM",
        password="NewPassword1!",
    )

    await session.refresh(user)
    assert applied_password == "NewPassword1!"
    assert user.email_normalizado == "new.email+reset@example.com"
    # Profile fields stay frozen after the contest ends.
    assert user.fullname == original_fullname


@pytest.mark.asyncio
async def test_edit_user_submit_after_contest_end_updates_only_credentials(
    session: AsyncSession, stopped_contest, uberadmin
) -> None:
    site = await _create_site(session, stopped_contest.id, "Frozen Site")
    user = await _make_contest_user(
        session,
        stopped_contest.id,
        uberadmin.id,
        username="team-locked",
        role=RoleEnum.TEAM,
        email="old@example.com",
        site_id=site.id,
    )
    original_fullname = user.fullname
    original_site_id = user.site_id
    ctx = ContestAdminContext(contest=stopped_contest, session=session, actor=uberadmin)
    request = _route_request()
    flash_messages: list[tuple[str, object]] = []
    user_id = user.id

    response = await edit_user_submit(
        request=request,
        user_id=user_id,
        flash=lambda message, category: flash_messages.append((message, category)),
        ctx=ctx,
        fullname="Forged Name",
        email="new@example.com",
        password="NewPassword1!",
        site_id="",
        location="FORGED",
    )

    await session.refresh(user)
    assert response.status_code == 303
    # Credentials are applied even though the contest has ended.
    assert user.email_normalizado == "new@example.com"
    # Profile fields posted alongside are ignored, not cleared.
    assert user.fullname == original_fullname
    assert user.site_id == original_site_id
    assert user.location is None
    assert any(message == "Credentials updated successfully." for message, _ in flash_messages)


@pytest.mark.asyncio
async def test_edit_user_submit_after_contest_end_rejects_invalid_email(
    session: AsyncSession, stopped_contest, uberadmin
) -> None:
    user = await _make_contest_user(
        session,
        stopped_contest.id,
        uberadmin.id,
        username="team-bad-email",
        role=RoleEnum.USER,
        email="keep@example.com",
    )
    ctx = ContestAdminContext(contest=stopped_contest, session=session, actor=uberadmin)
    request = _route_request()
    flash_messages: list[tuple[str, object]] = []
    user_id = user.id

    response = await edit_user_submit(
        request=request,
        user_id=user_id,
        flash=lambda message, category: flash_messages.append((message, category)),
        ctx=ctx,
        fullname="",
        email="not-an-email",
        password="",
        site_id="",
        location="",
    )

    await session.refresh(user)
    assert response.status_code == 303
    assert user.email_normalizado == "keep@example.com"
    assert any("Invalid email address." in message for message, _ in flash_messages)


def test_edit_user_template_keeps_credentials_editable_when_locked() -> None:
    edit_template = (
        Path(__file__).resolve().parents[2] / "web" / "template" / "admin" / "users" / "edit.html"
    ).read_text()
    # The email input closes right after its value (no is_locked disabled attr).
    assert "edit_user.email_normalizado or '' }}\">" in edit_template
    # The save button is always rendered, relabelled when the contest is locked.
    assert "Save credentials" in edit_template
    assert "Only the email and password can still be updated." in edit_template


# ---------------------------------------------------------------------------
# The single-session flag on the three creation and edit paths (#216)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_created_user_is_permissive_unless_the_caller_says_otherwise(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    """The default matches the column's, so a caller that says nothing changes nothing."""
    site = await _create_site(session, running_contest.id, "Main")

    default_user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_default",
        fullname="Default",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
    )
    restricted_user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_restricted",
        fullname="Restricted",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
        allow_concurrent_login=False,
    )

    assert default_user.allow_concurrent_login is True
    assert restricted_user.allow_concurrent_login is False


@pytest.mark.asyncio
async def test_updating_a_user_without_naming_the_flag_leaves_it_alone(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    """Tri-state rather than a bool: a caller that renders no control must not reset it.

    The credentials-only path after a contest ends is one such caller, and so is
    every batch row that updates an existing user.
    """
    site = await _create_site(session, running_contest.id, "Main")
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_keep",
        fullname="Keep",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
        allow_concurrent_login=False,
    )

    await update_user(
        session,
        running_contest,
        user,
        fullname="Renamed",
        role=RoleEnum.TEAM,
        site_id=site.id,
    )

    assert user.allow_concurrent_login is False, "the flag survived an unrelated edit"


@pytest.mark.asyncio
async def test_updating_a_user_can_set_the_flag_both_ways(session: AsyncSession, running_contest, uberadmin) -> None:
    site = await _create_site(session, running_contest.id, "Main")
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_toggle",
        fullname="Toggle",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
    )

    await update_user(
        session,
        running_contest,
        user,
        fullname="Toggle",
        role=RoleEnum.TEAM,
        site_id=site.id,
        allow_concurrent_login=False,
    )
    assert user.allow_concurrent_login is False

    await update_user(
        session,
        running_contest,
        user,
        fullname="Toggle",
        role=RoleEnum.TEAM,
        site_id=site.id,
        allow_concurrent_login=True,
    )
    assert user.allow_concurrent_login is True


@pytest.mark.asyncio
async def test_a_batch_import_applies_the_flag_to_creations_only(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    """Re-uploading a roster must not reverse a per-team decision made since.

    An import is a roster, not a policy: the rows it creates take the checkbox,
    and the rows that update an existing user leave that user's flag where the
    organiser put it.
    """
    site = await _create_site(session, running_contest.id, "Main")
    existing, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_existing",
        fullname="Existing",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
        allow_concurrent_login=True,
    )

    result = await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [
            {"username": "t_existing", "fullname": "Existing", "role": "team", "password": "", "site": "Main"},
            {"username": "t_new", "fullname": "New", "role": "team", "password": "", "site": "Main"},
        ],
        allow_concurrent_login=False,
    )

    assert (result.created, result.updated) == (1, 1)
    created = (
        await session.execute(select(User).where(User.username == "t_new", User.contest_id == running_contest.id))
    ).scalar_one()
    await session.refresh(existing)
    assert created.allow_concurrent_login is False, "the row the import created takes the setting"
    assert existing.allow_concurrent_login is True, "the row it updated keeps its own"


@pytest.mark.asyncio
async def test_a_batch_row_may_state_the_flag_and_overrides_the_import_default(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    """A value in the file is a statement about that user; the checkbox is only a default."""
    await _create_site(session, running_contest.id, "Main")

    result = await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [
            {
                "username": "t_row_false",
                "fullname": "A",
                "role": "team",
                "password": "",
                "site": "Main",
                "allow_concurrent_login": "false",
            },
            {
                "username": "t_row_true",
                "fullname": "B",
                "role": "team",
                "password": "",
                "site": "Main",
                "allow_concurrent_login": "true",
            },
            {"username": "t_silent", "fullname": "C", "role": "team", "password": "", "site": "Main"},
        ],
        allow_concurrent_login=True,
    )

    assert result.created == 3
    rows = {
        user.username: user
        for user in (await session.execute(select(User).where(User.contest_id == running_contest.id))).scalars()
    }
    assert rows["t_row_false"].allow_concurrent_login is False, "the row wins over the import default"
    assert rows["t_row_true"].allow_concurrent_login is True
    assert rows["t_silent"].allow_concurrent_login is True, "a silent row takes the default"


@pytest.mark.asyncio
async def test_a_stated_flag_applies_to_a_row_that_updates_an_existing_user(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    """Silence leaves the flag alone; a stated value changes it, on update too."""
    site = await _create_site(session, running_contest.id, "Main")
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_update",
        fullname="Update",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
        allow_concurrent_login=True,
    )

    await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [
            {
                "username": "t_update",
                "fullname": "Update",
                "role": "team",
                "password": "",
                "site": "Main",
                "allow_concurrent_login": "false",
            }
        ],
        allow_concurrent_login=True,
    )
    await session.refresh(user)
    assert user.allow_concurrent_login is False

    await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [{"username": "t_update", "fullname": "Update", "role": "team", "password": "", "site": "Main"}],
        allow_concurrent_login=True,
    )
    await session.refresh(user)
    assert user.allow_concurrent_login is False, "a silent row does not reset what the file did not mention"


@pytest.mark.asyncio
async def test_an_unreadable_flag_fails_its_row_rather_than_being_guessed(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    """The two possible misreadings are both bad, so a typo is refused, not resolved.

    Reading it as `false` would silently restrict a roster; reading it as `true`
    would silently leave it unrestricted. Neither is a guess worth making.
    """
    await _create_site(session, running_contest.id, "Main")

    result = await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [
            {
                "username": "t_typo",
                "fullname": "Typo",
                "role": "team",
                "password": "",
                "site": "Main",
                "allow_concurrent_login": "flase",
            }
        ],
        allow_concurrent_login=True,
    )

    assert (result.created, result.failed) == (0, 1)
    assert "allow_concurrent_login" in (result.results[0].detail or "")
    assert (
        not (
            await session.execute(select(User).where(User.username == "t_typo", User.contest_id == running_contest.id))
        )
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_the_results_say_what_each_row_did_to_the_flag(session: AsyncSession, running_contest, uberadmin) -> None:
    """`None` on an updated row means "left as the organiser set it", not a value chosen here."""
    site = await _create_site(session, running_contest.id, "Main")
    await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_known",
        fullname="Known",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
    )

    result = await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [
            {"username": "t_known", "fullname": "Known", "role": "team", "password": "", "site": "Main"},
            {"username": "t_fresh", "fullname": "Fresh", "role": "team", "password": "", "site": "Main"},
        ],
        allow_concurrent_login=False,
    )

    by_username = {row.username: row for row in result.results}
    assert by_username["t_known"].allow_concurrent_login is None
    assert by_username["t_fresh"].allow_concurrent_login is False


@pytest.mark.asyncio
async def test_a_csv_may_carry_the_flag_column(session: AsyncSession) -> None:
    """The CSV header allowlist is strict, so the column has to be admitted explicitly."""
    csv_bytes = (
        b"username,fullname,role,password,email,site,location,allow_concurrent_login\n"
        b"alice,Alice,team,,alice@example.com,Campus A,Room 3,false\n"
    )

    rows = parse_batch_upload("demo", "roster.csv", csv_bytes)

    assert rows[0]["allow_concurrent_login"] == "false"


@pytest.mark.asyncio
async def test_an_exported_roster_carries_the_policy_back_in(session: AsyncSession, running_contest, uberadmin) -> None:
    """Export then import must not quietly change what it moved."""
    site = await _create_site(session, running_contest.id, "Main")
    restricted, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_round",
        fullname="Round Trip",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
        allow_concurrent_login=False,
    )
    await session.refresh(restricted, ["site"])
    exported = build_user_export_row(restricted)
    assert exported["allow_concurrent_login"] == "false"

    # Re-imported into a contest whose upload checkbox says the opposite.
    await batch_import_users(
        session,
        running_contest,
        uberadmin,
        [dict(exported)],
        allow_concurrent_login=True,
    )

    await session.refresh(restricted)
    assert restricted.allow_concurrent_login is False


@pytest.mark.asyncio
async def test_lifting_the_flag_for_one_user_releases_that_user_s_binding(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    """The per-user edit follows the contest-wide lift, or the rule breaks in miniature.

    An address kept past the rule that recorded it is a trap the next time the
    rule is applied, whether it was one team or the whole contest that was let
    go. No epoch bump either way: the policy has stopped applying to them, so
    there is no session to supersede.
    """
    site = await _create_site(session, running_contest.id, "Main")
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_release",
        fullname="Release",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
        allow_concurrent_login=False,
    )
    user.locked_ip = "203.0.113.10"
    await session.flush()
    epoch_before = user.session_epoch

    await update_user(
        session,
        running_contest,
        user,
        fullname="Release",
        role=RoleEnum.TEAM,
        site_id=site.id,
        allow_concurrent_login=True,
    )

    assert user.allow_concurrent_login is True
    assert user.locked_ip is None
    assert user.locked_at is None
    assert user.session_epoch == epoch_before


@pytest.mark.asyncio
async def test_applying_the_flag_to_one_user_leaves_the_binding_to_the_next_request(
    session: AsyncSession, running_contest, uberadmin
) -> None:
    """The other direction binds nothing itself; the user's next request does."""
    site = await _create_site(session, running_contest.id, "Main")
    user, _ = await create_user(
        session,
        running_contest,
        uberadmin,
        username="t_apply",
        fullname="Apply",
        role=RoleEnum.TEAM,
        password=None,
        site_id=site.id,
    )

    await update_user(
        session,
        running_contest,
        user,
        fullname="Apply",
        role=RoleEnum.TEAM,
        site_id=site.id,
        allow_concurrent_login=False,
    )

    assert user.allow_concurrent_login is False
    assert user.locked_ip is None
