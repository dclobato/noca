from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.db_schema import contests, security_events
from shared.enumerations import RoleEnum
from shared.services.geolocation import GeolocationDetails, GeolocationIP
from shared.services.valkey_service import ContestValkeyPurgeResult
from web.routes.auth import router as auth_router
from web.routes.generaluser_dashboard import router as contest_dashboard_router
from web.routes.root import router as root_router
from web.routes.uberadmin_contest_backup import router as uberadmin_contest_backup_router
from web.routes.uberadmin_contest_removal import router as uberadmin_contest_removal_router
from web.routes.uberadmin_dashboard import router as uberadmin_dashboard_router
from web.routes.uberadmin_security import router as uberadmin_security_router
from web.services.authentication_service import AuthAction, AuthenticationService

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"


class _AuthThrottleValkeyRuntime:
    """Base double answering the auth-throttle surface with "no data".

    Returning ``None`` keeps `shared.services.auth_rate_limit` on its in-memory
    fallback limiter without pretending Valkey is unavailable.
    """

    async def get(self, key: str) -> str | None:
        return None

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        return None

    async def delete(self, *keys: str) -> None:
        return None


class _UnexpectedValkeyRuntime(_AuthThrottleValkeyRuntime):
    async def purge_contest_runtime_state(self, targets: object) -> None:
        raise AssertionError(f"Valkey cleanup should not run: {targets}")


class _SuccessfulValkeyRuntime(_AuthThrottleValkeyRuntime):
    def __init__(self) -> None:
        self.calls = 0

    async def purge_contest_runtime_state(self, targets: object) -> ContestValkeyPurgeResult:
        self.calls += 1
        return ContestValkeyPurgeResult(queue_entries_removed=0, keys_removed=0)


class _NoopGeo:
    def get_details_by_ip(self, ip_address: str | None) -> GeolocationDetails | None:
        return None


def _build_app(session: AsyncSession) -> tuple[FastAPI, AuthenticationService]:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["RoleEnum"] = RoleEnum
    templates.env.globals["role_labels"] = {role.value: role.value.title() for role in RoleEnum}
    setup_flash(templates)
    app.state.templates = templates
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.valkey_runtime = _UnexpectedValkeyRuntime()

    jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=AuthAction,
    )
    app.state.auth_service = AuthenticationService(
        jwt_service=jwt_service,
        geolocation_service=cast(GeolocationIP, _NoopGeo()),
        logger=logging.getLogger(__name__),
    )

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/profile", name="profile_get")
    async def _profile() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/problems", name="problem_bank")
    async def _problem_bank() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/uberadmin/uberadmins", name="list_uberadmins_route")
    async def _list_uberadmins() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}/admin", name="view")
    async def _contest_admin(slug: str) -> dict[str, str]:
        return {"slug": slug}

    app.include_router(auth_router)
    app.include_router(root_router)
    app.include_router(uberadmin_dashboard_router)
    app.include_router(uberadmin_contest_backup_router)
    app.include_router(uberadmin_contest_removal_router)
    app.include_router(uberadmin_security_router)
    app.include_router(contest_dashboard_router)
    return app, app.state.auth_service


async def _login_uberadmin(
    auth_service: AuthenticationService,
    session: AsyncSession,
    username: str,
) -> str:
    return await auth_service.uberadmin_login(username=username, password="TestPass1!", session=session)


def _contest_token(auth_service: AuthenticationService, *, username: str, contest_id: str) -> str:
    return auth_service.jwt_service.create(
        action=AuthAction.WEB_ACCESS,
        sub=username,
        audience=RoleEnum.TEAM.value,
        extra_data={"contest_id": contest_id, "session_started_at": int(datetime.now(UTC).timestamp())},
    )


@pytest.mark.asyncio
async def test_uberadmin_can_deactivate_past_contest_and_view_inactive_list(
    session: AsyncSession,
    stopped_contest,
    uberadmin,
) -> None:
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        dashboard = await client.get("/uberadmin/")
        response = await client.post(
            f"/uberadmin/contests/{stopped_contest.id}/deactivate",
            follow_redirects=False,
        )
        inactive = await client.get("/uberadmin/contests/inactive")

    assert dashboard.status_code == 200
    assert "Make inactive" in dashboard.text
    assert response.status_code == 303
    assert response.headers["location"] == "http://testserver/uberadmin/"
    assert inactive.status_code == 200
    assert "Stopped Contest" in inactive.text


@pytest.mark.asyncio
async def test_inactive_card_renders_password_confirmation_remove_modal(
    session: AsyncSession,
    stopped_contest,
    uberadmin,
) -> None:
    stopped_contest.active = False
    await session.commit()
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/uberadmin/contests/inactive")

    assert response.status_code == 200
    assert "Remove" in response.text
    assert f'id="remove-contest-{stopped_contest.id}"' in response.text
    assert f"Remove {stopped_contest.contest_name}?" in response.text
    assert "This action cannot be undone." in response.text
    assert 'name="password"' in response.text
    assert 'autocomplete="current-password"' in response.text
    assert f'action="http://testserver/uberadmin/contests/{stopped_contest.id}/remove"' in response.text


@pytest.mark.asyncio
async def test_remove_rejects_unauthenticated_blank_and_wrong_password_without_changes(
    session: AsyncSession,
    stopped_contest,
    uberadmin,
) -> None:
    stopped_contest.active = False
    await session.commit()
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        unauthenticated = await client.post(
            f"/uberadmin/contests/{stopped_contest.id}/remove",
            data={"password": "TestPass1!"},
            follow_redirects=False,
        )
        client.cookies.set("noca_access_token", token)
        blank_password = await client.post(
            f"/uberadmin/contests/{stopped_contest.id}/remove",
            data={"password": ""},
            follow_redirects=False,
        )
        wrong_password = await client.post(
            f"/uberadmin/contests/{stopped_contest.id}/remove",
            data={"password": "wrong"},
            follow_redirects=False,
        )

    assert unauthenticated.status_code == 302
    assert unauthenticated.headers["location"].endswith("/contests")
    assert blank_password.status_code == 303
    assert blank_password.headers["location"].endswith("/uberadmin/contests/inactive")
    assert wrong_password.status_code == 303
    assert wrong_password.headers["location"].endswith("/uberadmin/contests/inactive")
    assert await session.get(type(stopped_contest), stopped_contest.id) is not None
    assert (await session.execute(select(security_events.c.id))).scalars().all() == []


@pytest.mark.asyncio
async def test_direct_remove_rejects_active_and_missing_contests(
    session: AsyncSession,
    running_contest,
    uberadmin,
) -> None:
    await session.commit()
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        active = await client.post(
            f"/uberadmin/contests/{running_contest.id}/remove",
            data={"password": "TestPass1!"},
            follow_redirects=False,
        )
        missing = await client.post(
            "/uberadmin/contests/00000000-0000-0000-0000-000000000000/remove",
            data={"password": "TestPass1!"},
            follow_redirects=False,
        )

    assert active.status_code == 303
    assert missing.status_code == 303
    assert await session.get(type(running_contest), running_contest.id) is not None
    assert (await session.execute(select(security_events.c.id))).scalars().all() == []


@pytest.mark.asyncio
async def test_successful_remove_is_audited_and_duplicate_post_is_harmless(
    session: AsyncSession,
    stopped_contest,
    uberadmin,
) -> None:
    stopped_contest.active = False
    contest_id = stopped_contest.id
    await session.commit()
    app, auth_service = _build_app(session)
    runtime = _SuccessfulValkeyRuntime()
    app.state.valkey_runtime = runtime
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        removed = await client.post(
            f"/uberadmin/contests/{contest_id}/remove",
            data={"password": "TestPass1!"},
            follow_redirects=False,
        )
        duplicate = await client.post(
            f"/uberadmin/contests/{contest_id}/remove",
            data={"password": "TestPass1!"},
            follow_redirects=False,
        )

    assert removed.status_code == 303
    assert duplicate.status_code == 303
    assert runtime.calls == 1
    assert (
        await session.execute(select(contests.c.id).where(contests.c.id == contest_id))
    ).scalar_one_or_none() is None
    events = (
        (await session.execute(select(security_events).where(security_events.c.event_type == "contest_deleted")))
        .mappings()
        .all()
    )
    assert len(events) == 1
    assert events[0]["actor_user_id"] == uberadmin.id
    assert events[0]["metadata"] == {"contest_id": contest_id}


@pytest.mark.asyncio
async def test_public_contests_and_contest_access_hide_inactive_contest(
    session: AsyncSession,
    stopped_contest,
    team_user,
    uberadmin,
) -> None:
    stopped_contest.active = False
    await session.commit()
    app, auth_service = _build_app(session)
    token = _contest_token(auth_service, username=team_user.username, contest_id=stopped_contest.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        contests = await client.get("/contests")
        login_get = await client.get(f"/c/{stopped_contest.login_slug}/login")
        login_post = await client.post(
            f"/c/{stopped_contest.login_slug}/login",
            data={"identifier": team_user.username, "password": "TestPass1!"},
        )
        dashboard = await client.get(f"/c/{stopped_contest.login_slug}/")

    assert contests.status_code == 200
    assert "Stopped Contest" not in contests.text
    assert login_get.status_code == 404
    assert login_post.status_code == 404
    assert dashboard.status_code == 404


@pytest.mark.asyncio
async def test_running_contest_backup_export_is_rejected(
    session: AsyncSession,
    running_contest,
    uberadmin,
) -> None:
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get(f"/uberadmin/contests/{running_contest.id}/export")

    assert response.status_code == 409
    assert "Only finished or inactive contests" in response.text


@pytest.mark.asyncio
async def test_sensitive_backup_audit_is_written_only_after_success(
    session: AsyncSession,
    stopped_contest,
    uberadmin,
) -> None:
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        rejected = await client.post(
            f"/uberadmin/contests/{stopped_contest.id}/export",
            data={"include_password_hashes": "yes", "reconfirm_password": "wrong"},
        )
        exported = await client.post(
            f"/uberadmin/contests/{stopped_contest.id}/export",
            data={"include_password_hashes": "yes", "reconfirm_password": "TestPass1!"},
        )

    assert rejected.status_code == 422
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    events = (
        await session.execute(
            select(security_events.c.metadata, security_events.c.actor_label).where(
                security_events.c.event_type == "admin_action",
                security_events.c.actor_user_id == uberadmin.id,
            )
        )
    ).all()
    assert [(row.metadata["action"], row.actor_label) for row in events] == [
        ("contest_backup_export_password_hashes", uberadmin.username)
    ]


@pytest.mark.asyncio
async def test_media_export_records_its_own_audit_event(
    session: AsyncSession,
    stopped_contest,
    uberadmin,
) -> None:
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        exported = await client.post(
            f"/uberadmin/contests/{stopped_contest.id}/export",
            data={"include_media": "yes"},
        )

    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    events = (
        await session.execute(
            select(security_events.c.metadata, security_events.c.actor_label).where(
                security_events.c.event_type == "admin_action",
                security_events.c.actor_user_id == uberadmin.id,
            )
        )
    ).all()
    assert [(row.metadata["action"], row.actor_label) for row in events] == [
        ("contest_backup_export_user_media", uberadmin.username)
    ]
