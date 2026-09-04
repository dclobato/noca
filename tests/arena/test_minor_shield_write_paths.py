#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The write half of the Arena minor shield.

``test_minor_shield_read_paths.py`` owns what a shielded row *looks like* on a
public page. This file owns the invariant underneath it: **no shielded row ever
persists ``public_profile=true`` or ``full_name_public=true``.**

That invariant is what makes the read-side masking safe rather than merely
cosmetic. A stored ``True`` that is only hidden would surface on its owner's
eighteenth birthday, when the mask lifts and the profile publishes itself with
nobody having chosen anything -- so the flags are refused on the way in and
cleared on every transition into the shielded band.

``test_turning_18_does_not_auto_publish`` is the test that justifies the whole
file. It deliberately reuses the same account whose opt-in was refused as a
minor, because "a rejected opt-in cannot come back later" is the actual claim;
asserting it against a freshly built adult would prove something weaker.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

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
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_users import ArenaUser
from arena.routes.user_profile_api import router as arena_user_profile_api_router
from arena.services import admin_user_service, user_service
from arena.services.token_service import ArenaTokenAction
from arena.services.user_visibility_service import is_shielded
from shared.enumerations import ArenaRole

TEST_JWT_SECRET = "test-secret-key-for-arena-write-shield-32bytes!!"
_TEST_PASSWORD = "TestPass1!"

# Fixed dates, so a birthday cannot flip a case on the day the suite happens to run.
_MINOR_DOB = date(2010, 6, 15)
_ADULT_DOB = date(1990, 6, 15)


def _build_app(session: AsyncSession) -> FastAPI:
    """Build an Arena app serving the real personal-data JSON route."""
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
    app.include_router(arena_user_profile_api_router)

    @app.get("/dashboard", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    return app


async def _make_user(
    session: AsyncSession,
    *,
    email: str,
    dta_nascimento: date | None,
    username: str,
    name: str = "Pessoa de Teste",
    public_profile: bool = False,
    full_name_public: bool = False,
    ranking_visible: bool = True,
) -> ArenaUser:
    """Create an Arena user with an explicit age and visibility posture."""
    user = ArenaUser(
        nome=name,
        username=username,
        email_normalizado=email,
        password_hash=generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=dta_nascimento,
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
        consent_generation=0,
        ranking_visible=ranking_visible,
        public_profile=public_profile,
        full_name_public=full_name_public,
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


def _payload(user: ArenaUser, **overrides: object) -> dict[str, object]:
    """Build a complete personal-data payload for this user, with overrides."""
    body: dict[str, object] = {
        "name": user.nome,
        "date_of_birth": user.dta_nascimento.isoformat() if user.dta_nascimento else "",
        "country_code": None,
        "subdivision_code": None,
        "affiliation_id": None,
        "language_id": None,
        "prefered_language": "en-US",
    }
    body.update(overrides)
    return body


async def _post_personal_data(app: FastAPI, user: ArenaUser, body: dict[str, object]) -> Response:
    """POST the personal-data form as this user."""
    cookies = {"arena_access_token": _login_token(app, user)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        return await client.post("/user/profile/personal-data", json=body)


async def _reload(session: AsyncSession, user_id: str) -> ArenaUser:
    """Re-read a user from the database, bypassing the identity map."""
    session.expire_all()
    result = await session.execute(select(ArenaUser).where(ArenaUser.id == user_id))
    return result.scalar_one()


# ---------------------------------------------------------------------------
# The route refuses a shielded opt-in, and writes nothing at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["public_profile", "full_name_public"])
@pytest.mark.asyncio
async def test_shielded_user_cannot_enable_a_publication_flag(
    session: AsyncSession,
    flag: str,
) -> None:
    """A 17-year-old is refused both opt-ins with 400 age_shielded."""
    app = _build_app(session)
    minor = await _make_user(
        session,
        email=f"minor-{flag}@example.com",
        dta_nascimento=_MINOR_DOB,
        username=f"tatu-esperto-{'001' if flag == 'public_profile' else '002'}",
    )

    response = await _post_personal_data(app, minor, _payload(minor, **{flag: True}))

    assert response.status_code == 400
    assert response.json()["error"] == "age_shielded"


@pytest.mark.asyncio
async def test_a_refused_opt_in_writes_nothing_at_all(session: AsyncSession) -> None:
    """The refusal is hoisted above every mutation, so no field is persisted.

    The handler assigns name, language and location onto the ORM object before it
    reaches the visibility flags, so a guard placed where the old coupling block
    sat would leave those changes staged.
    """
    app = _build_app(session)
    minor = await _make_user(
        session,
        email="minor-nowrite@example.com",
        dta_nascimento=_MINOR_DOB,
        username="tatu-esperto-003",
        name="Nome Original",
    )

    response = await _post_personal_data(
        app,
        minor,
        _payload(minor, name="Nome Alterado", prefered_language="pt-BR", public_profile=True),
    )

    assert response.status_code == 400
    stored = await _reload(session, minor.id)
    assert stored.nome == "Nome Original"
    assert stored.prefered_language == "en-US"
    assert stored.public_profile is False
    assert stored.full_name_public is False


@pytest.mark.asyncio
async def test_shielded_user_may_still_save_everything_else(session: AsyncSession) -> None:
    """The shield refuses two flags, not the whole form."""
    app = _build_app(session)
    minor = await _make_user(
        session,
        email="minor-otherfields@example.com",
        dta_nascimento=_MINOR_DOB,
        username="tatu-esperto-004",
    )

    response = await _post_personal_data(app, minor, _payload(minor, name="Nome Novo", public_profile=False))

    assert response.status_code == 200
    stored = await _reload(session, minor.id)
    assert stored.nome == "Nome Novo"


@pytest.mark.asyncio
async def test_lowering_the_date_of_birth_while_opting_in_is_refused(session: AsyncSession) -> None:
    """The shield is evaluated against the submitted date, not the stored one.

    An adult who corrects their date of birth into the 13-17 band in the same
    request that asks to publish must be refused. Evaluating the stored value
    would accept the request and then silently clear the flag, which reports
    success for something that did not happen.
    """
    app = _build_app(session)
    adult = await _make_user(
        session,
        email="adult-lowering@example.com",
        dta_nascimento=_ADULT_DOB,
        username="jacare-sereno-001",
    )

    response = await _post_personal_data(
        app,
        adult,
        _payload(adult, date_of_birth=_MINOR_DOB.isoformat(), public_profile=True),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "age_shielded"
    stored = await _reload(session, adult.id)
    assert stored.dta_nascimento == _ADULT_DOB
    assert stored.public_profile is False


# ---------------------------------------------------------------------------
# The adult path still works, and echoes both flags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adult_may_enable_both_flags_and_the_response_echoes_them(session: AsyncSession) -> None:
    """An adult opts in to both, and the response reports the persisted values."""
    app = _build_app(session)
    adult = await _make_user(
        session,
        email="adult-optin@example.com",
        dta_nascimento=_ADULT_DOB,
        username="jacare-sereno-002",
    )

    response = await _post_personal_data(
        app,
        adult,
        _payload(adult, public_profile=True, full_name_public=True),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["public_profile"] is True
    assert body["full_name_public"] is True
    stored = await _reload(session, adult.id)
    assert stored.public_profile is True
    assert stored.full_name_public is True


@pytest.mark.asyncio
async def test_the_response_reports_the_shield_so_the_page_can_track_it(session: AsyncSession) -> None:
    """``age_shielded`` rides the response, re-derived after the date is applied.

    Without it the page would keep offering two opt-ins the server has just
    started refusing -- or keep them greyed out for someone who just proved they
    are an adult -- until a reload.
    """
    app = _build_app(session)
    adult = await _make_user(
        session,
        email="adult-shieldflag@example.com",
        dta_nascimento=_ADULT_DOB,
        username="jacare-sereno-004",
    )

    response = await _post_personal_data(app, adult, _payload(adult))

    assert response.status_code == 200
    assert response.json()["age_shielded"] is False


@pytest.mark.asyncio
async def test_a_shielded_account_reports_the_shield_in_the_response(session: AsyncSession) -> None:
    """A shielded user saving an unrelated field still learns they are shielded."""
    app = _build_app(session)
    minor = await _make_user(
        session,
        email="minor-shieldflag@example.com",
        dta_nascimento=_MINOR_DOB,
        username="jacare-sereno-005",
    )

    response = await _post_personal_data(app, minor, _payload(minor))

    assert response.status_code == 200
    assert response.json()["age_shielded"] is True


@pytest.mark.asyncio
async def test_omitting_full_name_public_preserves_the_stored_value(session: AsyncSession) -> None:
    """``None`` means "leave it alone", matching the two flags beside it."""
    app = _build_app(session)
    adult = await _make_user(
        session,
        email="adult-omit@example.com",
        dta_nascimento=_ADULT_DOB,
        username="jacare-sereno-003",
        full_name_public=True,
    )

    response = await _post_personal_data(app, adult, _payload(adult))

    assert response.status_code == 200
    assert response.json()["full_name_public"] is True
    stored = await _reload(session, adult.id)
    assert stored.full_name_public is True


# ---------------------------------------------------------------------------
# The test that justifies the write-side half
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turning_18_does_not_auto_publish(session: AsyncSession) -> None:
    """A refused minor opt-in does not resurface on the eighteenth birthday.

    Same account throughout: it is refused while shielded, and the point is that
    nothing was stored to be un-masked later. Once it ages out, publication still
    requires an explicit request.
    """
    app = _build_app(session)
    user = await _make_user(
        session,
        email="birthday@example.com",
        dta_nascimento=_MINOR_DOB,
        username="preguica-atenta-001",
    )

    refused = await _post_personal_data(
        app,
        user,
        _payload(user, public_profile=True, full_name_public=True),
    )
    assert refused.status_code == 400

    stored = await _reload(session, user.id)
    assert stored.public_profile is False
    assert stored.full_name_public is False

    # Advance past the eighteenth birthday. The shield is evaluated per request
    # against the current date, so ageing out needs no scheduler and no write.
    eighteenth = date(_MINOR_DOB.year + 18, _MINOR_DOB.month, _MINOR_DOB.day)
    assert is_shielded(_MINOR_DOB, reference_date=eighteenth - timedelta(days=1))
    assert not is_shielded(_MINOR_DOB, reference_date=eighteenth)

    # Nothing changed in the database when the birthday passed.
    still_stored = await _reload(session, user.id)
    assert still_stored.public_profile is False
    assert still_stored.full_name_public is False

    # Only an explicit opt-in publishes, and it is the user who makes it.
    still_stored.dta_nascimento = _ADULT_DOB
    await session.commit()
    granted = await _post_personal_data(
        app,
        await _reload(session, user.id),
        _payload(user, date_of_birth=_ADULT_DOB.isoformat(), public_profile=True, full_name_public=True),
    )
    assert granted.status_code == 200
    final = await _reload(session, user.id)
    assert final.public_profile is True
    assert final.full_name_public is True


# ---------------------------------------------------------------------------
# Every transition into the shielded band clears both flags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dob_change_adult_to_minor_clears_both_flags_and_invalidates_sessions(
    session: AsyncSession,
) -> None:
    """An adult who becomes a minor loses both opt-ins and every live session."""
    app = _build_app(session)
    adult = await _make_user(
        session,
        email="adult-to-minor@example.com",
        dta_nascimento=_ADULT_DOB,
        username="lontra-agil-001",
        public_profile=True,
        full_name_public=True,
    )
    session_version_before = adult.session_version
    consent_generation_before = adult.consent_generation

    response = await _post_personal_data(app, adult, _payload(adult, date_of_birth=_MINOR_DOB.isoformat()))

    assert response.status_code == 200
    body = response.json()
    assert body["public_profile"] is False
    assert body["full_name_public"] is False
    stored = await _reload(session, adult.id)
    assert stored.public_profile is False
    assert stored.full_name_public is False
    assert stored.session_version > session_version_before
    assert stored.consent_generation > consent_generation_before


@pytest.mark.asyncio
async def test_update_date_of_birth_service_clears_both_flags(session: AsyncSession) -> None:
    """The service does the clearing, so every caller inherits it."""
    adult = await _make_user(
        session,
        email="service-adult@example.com",
        dta_nascimento=_ADULT_DOB,
        username="lontra-agil-002",
        public_profile=True,
        full_name_public=True,
    )

    await user_service.update_date_of_birth(adult, _MINOR_DOB, session)
    await session.commit()

    stored = await _reload(session, adult.id)
    assert stored.public_profile is False
    assert stored.full_name_public is False


@pytest.mark.asyncio
async def test_regularizar_data_nascimento_clears_both_flags(session: AsyncSession) -> None:
    """The legacy date-of-birth path shields too, and bumps the consent epoch."""
    user = await _make_user(
        session,
        email="regularize@example.com",
        dta_nascimento=None,
        username="lontra-agil-003",
        public_profile=True,
        full_name_public=True,
    )
    consent_generation_before = user.consent_generation

    await user_service.regularizar_data_nascimento(user.id, _MINOR_DOB, session)
    await session.commit()

    stored = await _reload(session, user.id)
    assert stored.public_profile is False
    assert stored.full_name_public is False
    assert stored.consent_generation > consent_generation_before


@pytest.mark.asyncio
async def test_regularizar_data_nascimento_bumps_the_epoch_for_an_adult_too(session: AsyncSession) -> None:
    """Granting consent is a consent-state transition as much as revoking it."""
    user = await _make_user(
        session,
        email="regularize-adult@example.com",
        dta_nascimento=None,
        username="lontra-agil-004",
    )
    consent_generation_before = user.consent_generation

    await user_service.regularizar_data_nascimento(user.id, _ADULT_DOB, session)
    await session.commit()

    stored = await _reload(session, user.id)
    assert stored.consentimento_responsavel is True
    assert stored.consent_generation > consent_generation_before


# ---------------------------------------------------------------------------
# The admin path is not weaker than the user path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_cannot_enable_a_public_profile_for_a_shielded_user(session: AsyncSession) -> None:
    """The age shield is a legal control, so an admin may not override it."""
    minor = await _make_user(
        session,
        email="admin-target-minor@example.com",
        dta_nascimento=_MINOR_DOB,
        username="quati-curioso-001",
    )

    error = await admin_user_service.toggle_public_profile(minor, session)

    assert error is not None
    assert "age-shielded" in error
    stored = await _reload(session, minor.id)
    assert stored.public_profile is False


@pytest.mark.asyncio
async def test_the_admin_refusal_is_the_age_rule_not_the_ranking_rule(session: AsyncSession) -> None:
    """``ranking_visible`` is true here, so only the age shield can be refusing."""
    minor = await _make_user(
        session,
        email="admin-target-ranked@example.com",
        dta_nascimento=_MINOR_DOB,
        username="quati-curioso-002",
        ranking_visible=True,
    )

    error = await admin_user_service.toggle_public_profile(minor, session)

    assert error is not None
    assert "ranking visibility" not in error


@pytest.mark.asyncio
async def test_admin_may_still_disable_a_public_profile_on_a_shielded_user(session: AsyncSession) -> None:
    """Refusing to *enable* must not trap a stale flag that is already set."""
    minor = await _make_user(
        session,
        email="admin-target-stale@example.com",
        dta_nascimento=_MINOR_DOB,
        username="quati-curioso-003",
        public_profile=True,
    )

    error = await admin_user_service.toggle_public_profile(minor, session)
    await session.commit()

    assert error is None
    stored = await _reload(session, minor.id)
    assert stored.public_profile is False


@pytest.mark.asyncio
async def test_admin_may_enable_a_public_profile_for_an_adult(session: AsyncSession) -> None:
    """The refusal is the shield, not a blanket block on the admin action."""
    adult = await _make_user(
        session,
        email="admin-target-adult@example.com",
        dta_nascimento=_ADULT_DOB,
        username="quati-curioso-004",
    )

    error = await admin_user_service.toggle_public_profile(adult, session)
    await session.commit()

    assert error is None
    stored = await _reload(session, adult.id)
    assert stored.public_profile is True


# ---------------------------------------------------------------------------
# The model property must not become a third expression of the rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dob",
    [None, _MINOR_DOB, _ADULT_DOB, date(2008, 2, 29), date(1900, 1, 1)],
)
@pytest.mark.asyncio
async def test_is_age_shielded_property_agrees_with_the_service(
    session: AsyncSession,
    dob: date | None,
) -> None:
    """``ArenaUser.is_age_shielded`` delegates; it does not re-derive.

    The property exists so a template cannot reach past the shield. If it ever
    grew its own copy of the rule there would be three expressions of it -- the
    Python predicate, its SQL mirror, and this -- which is exactly the drift the
    one-shared-predicate idiom exists to prevent.
    """
    suffix = "none" if dob is None else dob.isoformat()
    user = await _make_user(
        session,
        email=f"prop-{suffix}@example.com",
        dta_nascimento=dob,
        username=f"bugio-calmo-{abs(hash(suffix)) % 1000:03d}",
    )

    assert user.is_age_shielded == is_shielded(dob)


@pytest.mark.asyncio
async def test_username_cooldown_days_property_reports_zero_when_never_changed(
    session: AsyncSession,
) -> None:
    """A user who has never renamed themselves may rename now."""
    user = await _make_user(
        session,
        email="cooldown-never@example.com",
        dta_nascimento=_ADULT_DOB,
        username="bugio-calmo-900",
    )

    assert user.dta_troca_username is None
    assert user.username_cooldown_days == 0


@pytest.mark.asyncio
async def test_username_cooldown_days_property_reports_a_recent_change(
    session: AsyncSession,
) -> None:
    """A rename a moment ago leaves the full window to wait."""
    user = await _make_user(
        session,
        email="cooldown-recent@example.com",
        dta_nascimento=_ADULT_DOB,
        username="bugio-calmo-901",
    )
    user.dta_troca_username = datetime.now(UTC)
    await session.commit()

    assert user.username_cooldown_days > 0
