#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Changing an Arena handle: the self-service endpoint and the admin override.

``test_username_service.py`` owns the validator -- what a handle may look like
and which ones are refused. This file owns what happens when someone actually
changes one: the cooldown, the conflict, the audit trail, and the fact that an
administrator can override the first of those but not skip the last.

Two properties are asserted here that no unit test can reach:

- **A collision is 409, never 500.** The availability check is a TOCTOU, so the
  ``UNIQUE`` constraint is the real guarantee and its failure has to arrive as a
  conflict the caller can act on.
- **Both security events name both handles.** Linking a user's old handle to
  their new one is exactly what the cooldown denies an outside observer, which
  is why it belongs in the admin-only audit log -- and why an audit row that
  omits it would be useless.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.security import generate_password_hash

import arena.models.arena_users  # noqa: F401
from arena.config import settings
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_users import ArenaUser
from arena.routes.admin_users_username import router as arena_admin_users_username_router
from arena.routes.user_username_api import router as arena_user_username_api_router
from arena.services import username_service
from arena.services.token_service import ArenaTokenAction
from shared.db_schema import security_events
from shared.enumerations import ArenaRole

TEST_JWT_SECRET = "test-secret-key-for-arena-username-change-32b!!"
_TEST_PASSWORD = "TestPass1!"
_ADULT_DOB = date(1990, 6, 15)


def _build_app(session: AsyncSession) -> FastAPI:
    """Build an Arena app serving both username surfaces."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.jwt_service = JWTService(
        config=load_token_config_from_dict(
            {"SECRET_KEY": TEST_JWT_SECRET, "JWTSERVICE_ALGORITHM": "HS256", "JWTSERVICE_ISSUER": "noca-arena-test"}
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )
    app.include_router(arena_user_username_api_router)
    app.include_router(arena_admin_users_username_router)

    @app.get("/admin/users", name="arena_admin_user_list")
    async def _user_list() -> Response:
        return Response("list")

    @app.get("/admin/users/{user_id}", name="arena_admin_user_profile")
    async def _user_profile(user_id: str) -> Response:
        return Response("profile")

    return app


async def _make_user(
    session: AsyncSession,
    *,
    email: str,
    username: str,
    role: ArenaRole = ArenaRole.ARENA_USER,
    dta_nascimento: date | None = _ADULT_DOB,
    dta_troca_username: datetime | None = None,
) -> ArenaUser:
    """Create an Arena user holding a known handle."""
    user = ArenaUser(
        nome="Pessoa de Teste",
        username=username,
        email_normalizado=email,
        password_hash=generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        role=role,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=dta_nascimento,
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
        consent_generation=0,
        ranking_visible=True,
        dta_troca_username=dta_troca_username,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    """Mint a LOGIN token for this user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


async def _post_username(app: FastAPI, user: ArenaUser, username: str) -> Response:
    """POST a self-service username change."""
    cookies = {"arena_access_token": _login_token(app, user)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        return await client.post("/user/profile/username", json={"username": username})


async def _get_availability(app: FastAPI, user: ArenaUser, candidate: str) -> Response:
    """GET the availability probe for a candidate handle."""
    cookies = {"arena_access_token": _login_token(app, user)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        return await client.get("/user/profile/username/available", params={"q": candidate})


async def _admin_rename(
    app: FastAPI,
    admin: ArenaUser,
    target: ArenaUser,
    username: str,
    *,
    password: str = _TEST_PASSWORD,
) -> Response:
    """POST the admin rename action."""
    cookies = {"arena_access_token": _login_token(app, admin)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        return await client.post(
            f"/admin/users/{target.id}/change-username",
            data={"new_username": username, "confirm_password": password},
            follow_redirects=False,
        )


async def _reload(session: AsyncSession, user_id: str) -> ArenaUser:
    """Re-read a user from the database, bypassing the identity map."""
    session.expire_all()
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == user_id))
    return result.scalar_one()


async def _username_events(session: AsyncSession) -> list[dict[str, Any]]:
    """Return every ``username_changed`` security event, oldest first."""
    rows = await session.execute(
        select(security_events).where(security_events.c.event_type == "username_changed").order_by(security_events.c.id)
    )
    return [dict(row) for row in rows.mappings()]


async def _admin_action_rows(session: AsyncSession, action: str) -> list[dict[str, Any]]:
    """Return every ``admin_action`` audit row for one action slug.

    Admin actions are not a table of their own: ``shared.services.admin_audit``
    records them as ``security_events`` rows carrying a structured payload.
    """
    rows = await session.execute(
        select(security_events).where(security_events.c.event_type == "admin_action").order_by(security_events.c.id)
    )
    return [dict(row) for row in rows.mappings() if dict(row)["metadata"].get("action") == action]


# ---------------------------------------------------------------------------
# The self-service change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_successful_change_stores_the_canonical_form_and_stamps_the_cooldown(
    session: AsyncSession,
) -> None:
    """The handle is canonicalized on the way in and the clock starts."""
    app = _build_app(session)
    user = await _make_user(session, email="rename@test.example", username="tucano-alegre-100")

    response = await _post_username(app, user, "  Tucano-Sereno-101  ")

    assert response.status_code == 200
    assert response.json()["username"] == "tucano-sereno-101"
    stored = await _reload(session, user.id)
    assert stored.username == "tucano-sereno-101"
    assert stored.dta_troca_username is not None


@pytest.mark.asyncio
async def test_a_successful_change_records_both_handles_in_the_security_event(
    session: AsyncSession,
) -> None:
    """An audit trail that cannot connect the two names records nothing useful."""
    app = _build_app(session)
    user = await _make_user(session, email="audit-self@test.example", username="tucano-alegre-102")

    await _post_username(app, user, "tucano-sereno-103")

    events = await _username_events(session)
    assert len(events) == 1
    metadata = events[0]["metadata"]
    assert metadata["source"] == "self"
    assert metadata["old"] == "tucano-alegre-102"
    assert metadata["new"] == "tucano-sereno-103"


@pytest.mark.asyncio
async def test_a_second_change_inside_the_window_is_refused_with_the_days_left(
    session: AsyncSession,
) -> None:
    """The cooldown answers 429 and says how long is left."""
    app = _build_app(session)
    user = await _make_user(session, email="cooldown@test.example", username="tucano-alegre-104")

    first = await _post_username(app, user, "tucano-sereno-105")
    assert first.status_code == 200

    second = await _post_username(app, await _reload(session, user.id), "tucano-serio-106")

    assert second.status_code == 429
    body = second.json()
    assert body["error"] == "cooldown"
    assert body["days_remaining"] > 0
    stored = await _reload(session, user.id)
    assert stored.username == "tucano-sereno-105"


@pytest.mark.asyncio
async def test_a_change_after_the_window_is_allowed(session: AsyncSession) -> None:
    """The cooldown expires on its own; nothing has to clear it."""
    app = _build_app(session)
    elapsed = datetime.now(UTC) - timedelta(days=settings.USERNAME_CHANGE_COOLDOWN_DAYS + 1)
    user = await _make_user(
        session,
        email="cooldown-elapsed@test.example",
        username="tucano-alegre-107",
        dta_troca_username=elapsed,
    )

    response = await _post_username(app, user, "tucano-sereno-108")

    assert response.status_code == 200
    stored = await _reload(session, user.id)
    assert stored.username == "tucano-sereno-108"


@pytest.mark.asyncio
async def test_a_zero_day_cooldown_never_refuses(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting the cooldown to 0 disables it outright."""
    monkeypatch.setattr(settings, "USERNAME_CHANGE_COOLDOWN_DAYS", 0)
    app = _build_app(session)
    user = await _make_user(
        session,
        email="cooldown-off@test.example",
        username="tucano-alegre-109",
        dta_troca_username=datetime.now(UTC),
    )

    response = await _post_username(app, user, "tucano-sereno-110")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_resubmitting_the_unchanged_handle_does_not_start_a_cooldown(
    session: AsyncSession,
) -> None:
    """Saving a form you did not edit must not cost you a 30-day wait."""
    app = _build_app(session)
    user = await _make_user(session, email="noop@test.example", username="tucano-alegre-111")

    response = await _post_username(app, user, "Tucano-Alegre-111")

    assert response.status_code == 200
    stored = await _reload(session, user.id)
    assert stored.username == "tucano-alegre-111"
    assert stored.dta_troca_username is None
    assert await _username_events(session) == []


@pytest.mark.asyncio
async def test_a_duplicate_differing_only_in_case_is_refused_with_409(session: AsyncSession) -> None:
    """Canonicalization means casing cannot dodge the uniqueness constraint."""
    app = _build_app(session)
    await _make_user(session, email="holder@test.example", username="onca-pintada-200")
    user = await _make_user(session, email="claimant@test.example", username="tucano-alegre-112")

    response = await _post_username(app, user, "ONCA-Pintada-200")

    assert response.status_code == 409
    assert response.json()["error"] == "username_taken"
    stored = await _reload(session, user.id)
    assert stored.username == "tucano-alegre-112"


@pytest.mark.asyncio
async def test_a_race_lost_at_the_constraint_is_409_and_not_500(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The availability pre-check is advisory; UNIQUE is the real guarantee.

    The check is monkeypatched to always say "free", which is exactly what a
    competing transaction committing between the check and the write looks like
    from inside this request.
    """
    app = _build_app(session)
    await _make_user(session, email="race-holder@test.example", username="onca-pintada-201")
    user = await _make_user(session, email="race-claimant@test.example", username="tucano-alegre-113")

    async def _always_available(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(username_service, "is_username_available", _always_available)

    response = await _post_username(app, user, "onca-pintada-201")

    assert response.status_code == 409
    assert response.json()["error"] == "username_taken"


@pytest.mark.parametrize(
    "candidate",
    ["ad", "admin", "profile", "has space", "-leading", "trailing-", "UPPER!", "0f8b1e2c-1a2b-3c4d-5e6f-708192a3b4c5"],
)
@pytest.mark.asyncio
async def test_an_unacceptable_handle_is_refused_with_400(
    session: AsyncSession,
    candidate: str,
) -> None:
    """Reserved, malformed, UUID-shaped and out-of-bounds handles are 400."""
    app = _build_app(session)
    user = await _make_user(
        session,
        email=f"bad-{abs(hash(candidate)) % 10000}@test.example",
        username=f"tucano-alegre-{abs(hash(candidate)) % 900 + 300:03d}",
    )

    response = await _post_username(app, user, candidate)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_username"


@pytest.mark.asyncio
async def test_the_change_endpoint_refuses_a_guest(session: AsyncSession) -> None:
    """The handle is an identity; an anonymous caller has none to change."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/user/profile/username", json={"username": "tucano-sereno-999"})

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# The availability probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_availability_reports_a_taken_handle(session: AsyncSession) -> None:
    """A handle another account holds is not available."""
    app = _build_app(session)
    await _make_user(session, email="probe-holder@test.example", username="onca-pintada-202")
    user = await _make_user(session, email="probe@test.example", username="tucano-alegre-114")

    response = await _get_availability(app, user, "onca-pintada-202")

    assert response.status_code == 200
    assert response.json()["available"] is False


@pytest.mark.asyncio
async def test_availability_treats_the_callers_own_handle_as_available(session: AsyncSession) -> None:
    """Your own handle is not a conflict with yourself."""
    app = _build_app(session)
    user = await _make_user(session, email="probe-own@test.example", username="tucano-alegre-115")

    response = await _get_availability(app, user, "tucano-alegre-115")

    assert response.status_code == 200
    assert response.json()["available"] is True


@pytest.mark.asyncio
async def test_availability_answers_200_for_a_malformed_handle_not_422(session: AsyncSession) -> None:
    """One error style for the field, whatever is wrong with the input."""
    app = _build_app(session)
    user = await _make_user(session, email="probe-bad@test.example", username="tucano-alegre-116")

    response = await _get_availability(app, user, "not a handle!")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["error"]


@pytest.mark.asyncio
async def test_the_availability_probe_is_rate_limited_per_user(session: AsyncSession) -> None:
    """It is a per-keystroke database query, so it takes a per-user window."""
    from arena.routes import user_username_api

    app = _build_app(session)
    user = await _make_user(session, email="probe-flood@test.example", username="tucano-alegre-117")

    limit = user_username_api.USERNAME_CHECK_MAX_REQUESTS
    for _ in range(limit):
        allowed = await _get_availability(app, user, "onca-pintada-900")
        assert allowed.status_code == 200

    refused = await _get_availability(app, user, "onca-pintada-900")

    assert refused.status_code == 429
    assert refused.headers["Retry-After"]


# ---------------------------------------------------------------------------
# The admin override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_rename_bypasses_the_cooldown(session: AsyncSession) -> None:
    """The cooldown protects a user from their own churn, not from moderation."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="admin@test.example", username="gaviao-chefe-001", role=ArenaRole.ARENA_ADMIN
    )
    target = await _make_user(
        session,
        email="target@test.example",
        username="tucano-alegre-118",
        dta_troca_username=datetime.now(UTC),
    )

    response = await _admin_rename(app, admin, target, "tucano-sereno-119")

    assert response.status_code == 303
    stored = await _reload(session, target.id)
    assert stored.username == "tucano-sereno-119"


@pytest.mark.asyncio
async def test_admin_rename_writes_an_audit_row_and_a_security_event_with_both_handles(
    session: AsyncSession,
) -> None:
    """The admin path is audited twice, and both records name both handles."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="admin-audit@test.example", username="gaviao-chefe-002", role=ArenaRole.ARENA_ADMIN
    )
    target = await _make_user(session, email="target-audit@test.example", username="tucano-alegre-120")

    await _admin_rename(app, admin, target, "tucano-sereno-121")

    audit_rows = await _admin_action_rows(session, "change_username")
    assert len(audit_rows) == 1
    assert audit_rows[0]["metadata"]["target_type"] == "arena_user"
    assert "tucano-alegre-120" in audit_rows[0]["metadata"]["detail"]
    assert "tucano-sereno-121" in audit_rows[0]["metadata"]["detail"]
    assert audit_rows[0]["severity"] == "warning"

    events = await _username_events(session)
    assert len(events) == 1
    metadata = events[0]["metadata"]
    assert metadata["source"] == "admin"
    assert metadata["old"] == "tucano-alegre-120"
    assert metadata["new"] == "tucano-sereno-121"


@pytest.mark.asyncio
async def test_admin_rename_with_a_wrong_password_changes_nothing(session: AsyncSession) -> None:
    """The password re-confirmation is the gate, not a formality."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="admin-wrongpw@test.example", username="gaviao-chefe-003", role=ArenaRole.ARENA_ADMIN
    )
    target = await _make_user(session, email="target-wrongpw@test.example", username="tucano-alegre-122")

    response = await _admin_rename(app, admin, target, "tucano-sereno-123", password="WrongPass1!")

    assert response.status_code == 303
    stored = await _reload(session, target.id)
    assert stored.username == "tucano-alegre-122"
    assert await _username_events(session) == []


@pytest.mark.asyncio
async def test_admin_rename_refuses_a_taken_handle_without_a_500(session: AsyncSession) -> None:
    """A conflict is reported through a flash, not raised as a server error."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="admin-conflict@test.example", username="gaviao-chefe-004", role=ArenaRole.ARENA_ADMIN
    )
    await _make_user(session, email="holder-conflict@test.example", username="onca-pintada-203")
    target = await _make_user(session, email="target-conflict@test.example", username="tucano-alegre-124")

    response = await _admin_rename(app, admin, target, "onca-pintada-203")

    assert response.status_code == 303
    stored = await _reload(session, target.id)
    assert stored.username == "tucano-alegre-124"


@pytest.mark.asyncio
async def test_admin_rename_refuses_a_non_admin_caller(session: AsyncSession) -> None:
    """A regular user cannot rename somebody else."""
    app = _build_app(session)
    caller = await _make_user(session, email="not-admin@test.example", username="tucano-alegre-125")
    target = await _make_user(session, email="target-notadmin@test.example", username="tucano-alegre-126")

    response = await _admin_rename(app, caller, target, "tucano-sereno-127")

    assert response.status_code in {303, 401, 403}
    stored = await _reload(session, target.id)
    assert stored.username == "tucano-alegre-126"


# ---------------------------------------------------------------------------
# The shield does not freeze the pseudonym
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_age_shielded_user_may_change_their_own_handle(session: AsyncSession) -> None:
    """The shield hides a legal name; it does not lock the handle hiding it."""
    app = _build_app(session)
    minor = await _make_user(
        session,
        email="minor-rename@test.example",
        username="tucano-alegre-128",
        dta_nascimento=date(2010, 6, 15),
    )

    response = await _post_username(app, minor, "tucano-sereno-129")

    assert response.status_code == 200
    stored = await _reload(session, minor.id)
    assert stored.username == "tucano-sereno-129"


# ---------------------------------------------------------------------------
# Cooldown arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_ago", "expected_zero"),
    [(None, True), (0, False), (1, False), (settings.USERNAME_CHANGE_COOLDOWN_DAYS + 1, True)],
)
def test_username_cooldown_remaining_boundaries(days_ago: int | None, expected_zero: bool) -> None:
    """The window is measured from the last change, and expires on its own."""
    user = ArenaUser(
        nome="x",
        username="capivara-quieta-001",
        email_normalizado="x@test.example",
        dta_troca_username=None if days_ago is None else datetime.now(UTC) - timedelta(days=days_ago),
    )

    remaining = username_service.username_cooldown_remaining(user)

    assert (remaining == 0) is expected_zero


def test_username_cooldown_remaining_handles_a_naive_timestamp() -> None:
    """SQLite hands back naive datetimes; they must not raise on comparison."""
    user = ArenaUser(
        nome="x",
        username="capivara-quieta-002",
        email_normalizado="x2@test.example",
        dta_troca_username=datetime.now(UTC).replace(tzinfo=None),
    )

    assert username_service.username_cooldown_remaining(user) > 0


# ---------------------------------------------------------------------------
# The admin-scoped availability probe
# ---------------------------------------------------------------------------


async def _admin_probe(app: FastAPI, admin: ArenaUser, target: ArenaUser, candidate: str) -> Response:
    """GET the admin availability probe for a target user."""
    cookies = {"arena_access_token": _login_token(app, admin)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        return await client.get(f"/admin/users/{target.id}/username/available", params={"q": candidate})


@pytest.mark.asyncio
async def test_admin_probe_treats_the_targets_own_handle_as_available(session: AsyncSession) -> None:
    """The probe excludes the *target*, not the acting admin.

    This is the whole reason it is a separate endpoint. Reusing the self-service
    probe would exclude the admin's own id, so an admin opening the modal -- which
    is pre-filled with the handle the user already holds -- would immediately be
    told it is taken.
    """
    app = _build_app(session)
    admin = await _make_user(
        session, email="probe-admin@test.example", username="gaviao-chefe-100", role=ArenaRole.ARENA_ADMIN
    )
    target = await _make_user(session, email="probe-target@test.example", username="tucano-alegre-200")

    response = await _admin_probe(app, admin, target, "tucano-alegre-200")

    assert response.status_code == 200
    assert response.json()["available"] is True


@pytest.mark.asyncio
async def test_admin_probe_reports_a_handle_held_by_someone_else(session: AsyncSession) -> None:
    """A handle another account holds is refused before the form is submitted."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="probe-admin2@test.example", username="gaviao-chefe-101", role=ArenaRole.ARENA_ADMIN
    )
    await _make_user(session, email="probe-holder@test.example", username="onca-pintada-300")
    target = await _make_user(session, email="probe-target2@test.example", username="tucano-alegre-201")

    response = await _admin_probe(app, admin, target, "onca-pintada-300")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["error"]


@pytest.mark.asyncio
async def test_admin_probe_answers_200_for_a_malformed_handle(session: AsyncSession) -> None:
    """One feedback style for the field, whatever is wrong with the input."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="probe-admin3@test.example", username="gaviao-chefe-102", role=ArenaRole.ARENA_ADMIN
    )
    target = await _make_user(session, email="probe-target3@test.example", username="tucano-alegre-202")

    response = await _admin_probe(app, admin, target, "Not A Handle!")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["error"]


@pytest.mark.asyncio
async def test_admin_probe_refuses_a_non_admin_caller(session: AsyncSession) -> None:
    """A regular user cannot probe handles on someone else's behalf."""
    app = _build_app(session)
    caller = await _make_user(session, email="probe-notadmin@test.example", username="tucano-alegre-203")
    target = await _make_user(session, email="probe-target4@test.example", username="tucano-alegre-204")

    response = await _admin_probe(app, caller, target, "onca-pintada-301")

    assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_admin_probe_404s_for_an_unknown_target(session: AsyncSession) -> None:
    """An unknown target is a 404, not a verdict about a handle."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="probe-admin5@test.example", username="gaviao-chefe-103", role=ArenaRole.ARENA_ADMIN
    )
    cookies = {"arena_access_token": _login_token(app, admin)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        response = await client.get("/admin/users/does-not-exist/username/available", params={"q": "onca-pintada-302"})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# What an admin rename does to the target's own cooldown
# ---------------------------------------------------------------------------
#
# Neither behaviour is self-evidently right, so both are pinned. The default
# protects a moderation rename from being undone; the opt-in keeps a benign
# rename from penalising a user for something they did not do. A silent change
# to either would be a policy change, and these assertions are what make it
# announce itself.


async def _admin_rename_with_choice(
    app: FastAPI,
    admin: ArenaUser,
    target: ArenaUser,
    username: str,
    *,
    allow_immediate: bool,
) -> Response:
    """POST the admin rename, with or without the immediate-change opt-in."""
    data = {"new_username": username, "confirm_password": _TEST_PASSWORD}
    if allow_immediate:
        data["allow_immediate_change"] = "true"
    cookies = {"arena_access_token": _login_token(app, admin)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        return await client.post(f"/admin/users/{target.id}/change-username", data=data, follow_redirects=False)


@pytest.mark.asyncio
async def test_admin_rename_restarts_the_cooldown_by_default(session: AsyncSession) -> None:
    """The protective default: the user waits the full window afterwards."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="cool-admin1@test.example", username="gaviao-chefe-200", role=ArenaRole.ARENA_ADMIN
    )
    target = await _make_user(
        session,
        email="cool-target1@test.example",
        username="tucano-alegre-600",
        dta_troca_username=datetime.now(UTC) - timedelta(days=29),
    )
    assert username_service.username_cooldown_remaining(target) == 1

    response = await _admin_rename_with_choice(app, admin, target, "tucano-sereno-601", allow_immediate=False)

    assert response.status_code == 303
    stored = await _reload(session, target.id)
    assert stored.username == "tucano-sereno-601"
    assert stored.dta_troca_username is not None
    assert username_service.username_cooldown_remaining(stored) == settings.USERNAME_CHANGE_COOLDOWN_DAYS


@pytest.mark.asyncio
async def test_admin_rename_can_leave_the_user_free_to_change_again(session: AsyncSession) -> None:
    """The opt-in clears the window instead of restarting it."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="cool-admin2@test.example", username="gaviao-chefe-201", role=ArenaRole.ARENA_ADMIN
    )
    target = await _make_user(
        session,
        email="cool-target2@test.example",
        username="tucano-alegre-602",
        dta_troca_username=datetime.now(UTC) - timedelta(days=29),
    )

    response = await _admin_rename_with_choice(app, admin, target, "tucano-sereno-603", allow_immediate=True)

    assert response.status_code == 303
    stored = await _reload(session, target.id)
    assert stored.username == "tucano-sereno-603"
    assert stored.dta_troca_username is None
    assert username_service.username_cooldown_remaining(stored) == 0


@pytest.mark.asyncio
async def test_after_the_opt_in_the_user_really_can_rename_immediately(session: AsyncSession) -> None:
    """End-to-end: the cleared window is honoured by the self-service path.

    Asserting the column alone would not prove the user is unblocked; only the
    endpoint they would actually use can.
    """
    app = _build_app(session)
    admin = await _make_user(
        session, email="cool-admin3@test.example", username="gaviao-chefe-202", role=ArenaRole.ARENA_ADMIN
    )
    target = await _make_user(
        session,
        email="cool-target3@test.example",
        username="tucano-alegre-604",
        dta_troca_username=datetime.now(UTC) - timedelta(days=1),
    )

    await _admin_rename_with_choice(app, admin, target, "tucano-sereno-605", allow_immediate=True)

    own = await _post_username(app, await _reload(session, target.id), "tucano-serio-606")

    assert own.status_code == 200
    stored = await _reload(session, target.id)
    assert stored.username == "tucano-serio-606"


@pytest.mark.asyncio
async def test_after_the_default_the_user_is_held_off(session: AsyncSession) -> None:
    """The mirror of the above: the default really does block the user."""
    app = _build_app(session)
    admin = await _make_user(
        session, email="cool-admin4@test.example", username="gaviao-chefe-203", role=ArenaRole.ARENA_ADMIN
    )
    target = await _make_user(session, email="cool-target4@test.example", username="tucano-alegre-607")

    await _admin_rename_with_choice(app, admin, target, "tucano-sereno-608", allow_immediate=False)

    own = await _post_username(app, await _reload(session, target.id), "tucano-serio-609")

    assert own.status_code == 429
    assert own.json()["error"] == "cooldown"


@pytest.mark.parametrize(
    ("allow_immediate", "expected"),
    [(False, "restarted"), (True, "cleared")],
    ids=["default", "opt-in"],
)
@pytest.mark.asyncio
async def test_the_cooldown_decision_is_audited(
    session: AsyncSession,
    allow_immediate: bool,
    expected: str,
) -> None:
    """Which choice the admin made is recorded, not just that a rename happened."""
    app = _build_app(session)
    admin = await _make_user(
        session,
        email=f"cool-audit-{expected}@test.example",
        username=f"gaviao-chefe-{'204' if allow_immediate else '205'}",
        role=ArenaRole.ARENA_ADMIN,
    )
    target = await _make_user(
        session,
        email=f"cool-audit-target-{expected}@test.example",
        username=f"tucano-alegre-{'610' if allow_immediate else '611'}",
    )

    await _admin_rename_with_choice(
        app, admin, target, f"tucano-sereno-{'612' if allow_immediate else '613'}", allow_immediate=allow_immediate
    )

    audit_rows = await _admin_action_rows(session, "change_username")
    assert len(audit_rows) == 1
    assert f"cooldown={expected}" in audit_rows[0]["metadata"]["detail"]

    events = await _username_events(session)
    assert events[0]["metadata"]["cooldown"] == expected
