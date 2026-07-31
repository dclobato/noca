#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for scoped team-photo delivery.

Two contracts are pinned here:

- **A team in scope always yields a valid image.** Every degradation — the flag
  off, undecodable base64, empty bytes, bytes that are not an image — falls
  through to the next candidate and finally to the placeholder, so the ceremony
  modal can never render broken-image chrome.
- **Scope cannot reach another site's team.** A site-scoped request for a foreign
  team is the same ``404`` a nonexistent team gets.

Conditional-request behavior is tested through the real header parsing (weak,
list, and wildcard forms), because a browser cache is exactly what makes a
projector reopening the same photos cheap.
"""

from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from animator.routes.team_media import router as team_media_router
from animator.services.team_media_service import PLACEHOLDER_MIME, placeholder_image
from shared.db_schema import users_media
from shared.enumerations import RoleEnum
from tests.animator._feed_seed import make_contest, make_site, make_user
from tests.animator._reveal_seed import Ceremony, seed_ceremony
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

# Smallest valid PNG and GIF payloads: enough for a signature sniff, which is
# what the read path performs (it never decodes the image).
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001" + "0d0a2db4" + "0000000049454e44ae426082"
)
_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

# A payload that passes a *signature* check and nothing more: the real PNG magic
# bytes followed by a truncated header. Sniffing alone would happily serve this as
# `image/png`, and the browser would render broken-image chrome — which is why the
# read path decodes rather than sniffs.
_TRUNCATED_PNG = _PNG[:20]

_DTA = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _build_app(engine: AsyncEngine) -> FastAPI:
    """Wire a minimal app around the team-media router."""
    app = FastAPI()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.include_router(team_media_router)
    return app


def _client(app: FastAPI) -> AsyncClient:
    """Build an ASGI client for the app."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _set_media(
    session: AsyncSession,
    team_id: str,
    *,
    com_foto: bool = True,
    foto: str | None = None,
    avatar: str | None = None,
    dta_foto: datetime | None = _DTA,
) -> None:
    """Insert or replace one team's stored media row."""
    await session.execute(
        insert(users_media).values(
            user_id=team_id,
            com_foto=com_foto,
            foto_base64=foto,
            avatar_base64=avatar,
            foto_mime="image/png",
            dta_foto=dta_foto,
        )
    )
    await session.commit()


def _url(ceremony: Ceremony, team_id: str, *, scope: str = "global") -> str:
    """Build the scoped photo URL for one team."""
    return f"/c/{ceremony.slug}/teams/{team_id}/photo?scope={scope}"


# ---------------------------------------------------------------------------
# Selection: photo → avatar → placeholder
# ---------------------------------------------------------------------------


async def test_stored_photo_is_served_with_its_sniffed_mime(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(session, ceremony.a1, foto=b64encode(_PNG).decode(), avatar=b64encode(_GIF).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 200
    assert response.content == _PNG
    # The content type comes from the bytes, not from the stored `foto_mime`
    # claim, so a mislabeled row cannot be served under the wrong type.
    assert response.headers["content-type"] == "image/png"
    assert response.headers["Cache-Control"] == "public, max-age=300"
    assert response.headers["X-NOCA-Team-Image-Kind"] == "photo"


async def test_invalid_photo_falls_through_to_a_valid_avatar(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(session, ceremony.a1, foto="!!!not base64!!!", avatar=b64encode(_GIF).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 200
    assert response.content == _GIF
    assert response.headers["content-type"] == "image/gif"
    assert response.headers["ETag"].startswith('"avatar-')
    assert response.headers["X-NOCA-Team-Image-Kind"] == "avatar"


async def test_both_blobs_invalid_falls_through_to_the_placeholder(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    # Valid base64 that is not an image at all, plus an empty-decoding blob.
    await _set_media(session, ceremony.a1, foto=b64encode(b"plain text, not an image").decode(), avatar="")
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 200
    assert response.content == placeholder_image().data
    assert response.headers["content-type"] == PLACEHOLDER_MIME
    assert response.headers["ETag"] == '"placeholder"'
    assert response.headers["X-NOCA-Team-Image-Kind"] == "placeholder"


async def test_empty_decoded_bytes_count_as_invalid(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    # "" decodes to zero bytes: valid base64, unusable image.
    await _set_media(session, ceremony.a1, foto=b64encode(b"").decode(), avatar=b64encode(_PNG).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.content == _PNG
    assert response.headers["ETag"].startswith('"avatar-')


async def test_truncated_photo_with_a_valid_signature_falls_through(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(
        session,
        ceremony.a1,
        foto=b64encode(_TRUNCATED_PNG).decode(),
        avatar=b64encode(_GIF).decode(),
    )
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    # A file-signature check would have accepted the truncated photo and served
    # an unrenderable `image/png`. Structural validation rejects it, so the modal
    # gets the avatar instead of broken-image chrome.
    assert response.status_code == 200
    assert response.content == _GIF
    assert response.headers["content-type"] == "image/gif"


async def test_truncated_photo_and_avatar_fall_through_to_the_placeholder(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(
        session,
        ceremony.a1,
        foto=b64encode(_TRUNCATED_PNG).decode(),
        avatar=b64encode(_GIF[:8]).decode(),
    )
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.content == placeholder_image().data
    assert response.headers["content-type"] == PLACEHOLDER_MIME


async def test_com_foto_false_ignores_both_stored_blobs(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(
        session,
        ceremony.a1,
        com_foto=False,
        foto=b64encode(_PNG).decode(),
        avatar=b64encode(_GIF).decode(),
    )
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    # The Web semantics: a removed photo stays removed even if the bytes linger.
    assert response.content == placeholder_image().data
    assert response.headers["ETag"] == '"placeholder"'


async def test_team_without_a_media_row_gets_the_placeholder(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 200
    assert response.content == placeholder_image().data


# ---------------------------------------------------------------------------
# Conditional requests
# ---------------------------------------------------------------------------


async def test_conditional_request_returns_304_with_no_body(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(session, ceremony.a1, foto=b64encode(_PNG).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        first = await client.get(_url(ceremony, ceremony.a1))
        second = await client.get(_url(ceremony, ceremony.a1), headers={"If-None-Match": first.headers["ETag"]})

    assert first.status_code == 200
    assert second.status_code == 304
    assert second.content == b""
    # The 304 carries the validators too, so the client refreshes its freshness
    # lifetime instead of revalidating on every single use.
    assert second.headers["ETag"] == first.headers["ETag"]
    assert second.headers["Cache-Control"] == first.headers["Cache-Control"]
    assert second.headers["X-NOCA-Team-Image-Kind"] == first.headers["X-NOCA-Team-Image-Kind"]


@pytest.mark.parametrize("template", ["W/{etag}", '"other", {etag}', "*", 'W/"other", W/{etag}'])
async def test_if_none_match_weak_list_and_wildcard_forms_match(
    session: AsyncSession, uberadmin: UberAdmin, template: str
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(session, ceremony.a1, foto=b64encode(_PNG).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        first = await client.get(_url(ceremony, ceremony.a1))
        header = template.format(etag=first.headers["ETag"])
        conditional = await client.get(_url(ceremony, ceremony.a1), headers={"If-None-Match": header})

    assert conditional.status_code == 304, header


async def test_non_matching_validator_returns_the_full_body(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(session, ceremony.a1, foto=b64encode(_PNG).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1), headers={"If-None-Match": '"stale"'})

    assert response.status_code == 200
    assert response.content == _PNG


async def test_a_replaced_photo_changes_the_etag(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(session, ceremony.a1, foto=b64encode(_PNG).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        first = await client.get(_url(ceremony, ceremony.a1))

        # A new upload moves both the bytes and `dta_foto`.
        await session.execute(
            update(users_media)
            .where(users_media.c.user_id == ceremony.a1)
            .values(foto_base64=b64encode(_GIF).decode(), dta_foto=_DTA + timedelta(minutes=5))
        )
        await session.commit()

        stale_validator = await client.get(
            _url(ceremony, ceremony.a1), headers={"If-None-Match": first.headers["ETag"]}
        )

    assert stale_validator.status_code == 200
    assert stale_validator.content == _GIF
    assert stale_validator.headers["ETag"] != first.headers["ETag"]


async def test_the_kind_is_part_of_the_etag(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(session, ceremony.a1, foto=b64encode(_PNG).decode(), avatar=b64encode(_GIF).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        photo = await client.get(_url(ceremony, ceremony.a1))
        # Corrupt only the photo: the same `dta_foto` now answers with the avatar,
        # so the tag must move even though the version component did not.
        await session.execute(update(users_media).where(users_media.c.user_id == ceremony.a1).values(foto_base64="!!!"))
        await session.commit()
        avatar = await client.get(_url(ceremony, ceremony.a1), headers={"If-None-Match": photo.headers["ETag"]})

    assert avatar.status_code == 200
    assert avatar.content == _GIF


# ---------------------------------------------------------------------------
# Scope isolation and the uniform 404
# ---------------------------------------------------------------------------


async def test_site_scope_serves_its_own_team(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(session, ceremony.a1, foto=b64encode(_PNG).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1, scope=ceremony.site_a))

    assert response.status_code == 200
    assert response.content == _PNG


async def test_site_scope_cannot_reach_another_sites_team(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_media(session, ceremony.b1, foto=b64encode(_PNG).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        foreign = await client.get(_url(ceremony, ceremony.b1, scope=ceremony.site_a))
        unknown = await client.get(_url(ceremony, "no-such-team", scope=ceremony.site_a))
        # The global scope legitimately sees every team.
        globally = await client.get(_url(ceremony, ceremony.b1))

    assert foreign.status_code == 404
    assert foreign.json() == unknown.json()
    assert globally.status_code == 200


async def test_a_team_of_another_contest_is_not_addressable(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    other = await make_contest(session, uberadmin, slug="photo-other")
    other_site = await make_site(session, other, sitename="Other Campus")
    outsider = make_user(other, uberadmin, "outsider", site_id=other_site.id)
    session.add(outsider)
    await session.commit()
    await _set_media(session, outsider.id, foto=b64encode(_PNG).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, outsider.id))

    assert response.status_code == 404


async def test_a_non_team_user_is_not_addressable(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    from web.models.contest import Contest

    contest = await session.get(Contest, ceremony.contest_id)
    assert contest is not None
    judge = make_user(contest, uberadmin, "judge-1", role=RoleEnum.JUDGE)
    session.add(judge)
    await session.commit()
    await _set_media(session, judge.id, foto=b64encode(_PNG).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, judge.id))

    assert response.status_code == 404


async def test_disabled_contest_and_bad_scope_are_the_same_bare_404(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    disabled = await make_contest(session, uberadmin, slug="photo-off", animator_enabled=False)
    await session.commit()
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        unknown_slug = await client.get(f"/c/photo-nope/teams/{ceremony.a1}/photo?scope=global")
        disabled_contest = await client.get(f"/c/{disabled.login_slug}/teams/{ceremony.a1}/photo?scope=global")
        bad_scope = await client.get(_url(ceremony, ceremony.a1, scope="not-a-site"))

    for response in (unknown_slug, disabled_contest, bad_scope):
        assert response.status_code == 404
        assert response.json() == unknown_slug.json()
