#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for scoped team-audio delivery.

Three contracts are pinned here:

- **A clip is served only if it can be vouched for.** Missing, undecodable,
  empty, and unrecognizable payloads are all the same ``404``, so the ceremony
  modal hides its player instead of rendering a broken one. No stored-data defect
  may surface as a ``500``.
- **The served type comes from the bytes, never from ``audio_mime``.** A row whose
  claim disagrees with its content is served under the sniffed type.
- **Scope cannot reach another site's team**, exactly as for photos.

Conditional-request behavior reuses the photo route's RFC 9110 comparison, so the
weak/list/wildcard forms are exercised here too.
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
from animator.services import team_audio_service
from shared.db_schema import users_media
from shared.enumerations import RoleEnum
from tests.animator._feed_seed import make_contest, make_site, make_user
from tests.animator._media_probe import StatementProbe
from tests.animator._reveal_seed import Ceremony, seed_ceremony
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

# Minimal payloads carrying the real file signatures puremagic detects.
#
# The MP3 fixture is a bare MPEG frame sync rather than an ID3-tagged file:
# puremagic reports an ID3v2 header as `audio/vnd.audiokoz`, which neither this
# module nor the Web upload path accepts. That parity is deliberate — a clip can
# only reach the database through the Web upload validator, so the animator
# accepts exactly the set Web can store and nothing more.
_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 64
_OGG = b"OggS\x00\x02" + b"\x00" * 26 + b"\x01vorbis" + b"\x00" * 32  # detected as application/ogg


def _wav(payload_len: int = 64) -> bytes:
    """Build a structurally valid little-endian PCM WAV."""
    data = b"\x00" * payload_len
    header = (
        b"RIFF"
        + (36 + len(data)).to_bytes(4, "little")
        + b"WAVEfmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (8000).to_bytes(4, "little")
        + (16000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data"
        + len(data).to_bytes(4, "little")
    )
    return header + data


_WAV = _wav()
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


async def _set_audio(
    session: AsyncSession,
    team_id: str,
    *,
    audio: str | None,
    mime: str | None = "audio/mpeg",
    dta_audio: datetime | None = _DTA,
) -> None:
    """Insert one team's stored media row carrying an audio payload."""
    await session.execute(
        insert(users_media).values(
            user_id=team_id,
            com_foto=False,
            audio_base64=audio,
            audio_mime=mime,
            dta_audio=dta_audio,
        )
    )
    await session.commit()


def _url(ceremony: Ceremony, team_id: str, *, scope: str = "global") -> str:
    """Build the scoped audio URL for one team."""
    return f"/c/{ceremony.slug}/teams/{team_id}/audio?scope={scope}"


# ---------------------------------------------------------------------------
# Serving a usable clip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(_MP3, "audio/mpeg"), (_OGG, "audio/ogg"), (_WAV, "audio/wav")],
)
async def test_supported_formats_are_served_with_canonical_types(
    session: AsyncSession, uberadmin: UberAdmin, payload: bytes, expected: str
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(payload).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == expected
    assert response.headers["Cache-Control"] == "public, max-age=300"


async def test_stored_mime_claim_is_ignored_in_favor_of_the_bytes(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    # The row claims WAV; the bytes are an MP3. The bytes win.
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode(), mime="audio/wav")
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"


async def test_range_support_is_not_advertised(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    # Claiming range support the route does not implement would break seeking
    # rather than enable it.
    assert "accept-ranges" not in {key.lower() for key in response.headers}


# ---------------------------------------------------------------------------
# Every unusable payload is a 404, never a 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,  # no clip stored at all
        "",  # empty string
        "!!!not base64!!!",  # undecodable
        b64encode(b"").decode(),  # decodes to zero bytes
        b64encode(b"plain text, not audio at all").decode(),  # unrecognizable
        b64encode(bytes.fromhex("89504e470d0a1a0a")).decode(),  # a PNG: recognized, unsupported
    ],
)
async def test_unusable_payloads_are_404(session: AsyncSession, uberadmin: UberAdmin, payload: str | None) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=payload)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 404


async def test_team_without_a_media_row_is_404(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Conditional requests
# ---------------------------------------------------------------------------


async def test_conditional_request_returns_304_with_validators(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        first = await client.get(_url(ceremony, ceremony.a1))
        second = await client.get(_url(ceremony, ceremony.a1), headers={"If-None-Match": first.headers["ETag"]})

    assert first.headers["ETag"].startswith('"audio-')
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["ETag"] == first.headers["ETag"]
    assert second.headers["Cache-Control"] == first.headers["Cache-Control"]


@pytest.mark.parametrize("template", ["W/{etag}", '"other", {etag}', "*", 'W/"other", W/{etag}'])
async def test_if_none_match_weak_list_and_wildcard_forms_match(
    session: AsyncSession, uberadmin: UberAdmin, template: str
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        first = await client.get(_url(ceremony, ceremony.a1))
        header = template.format(etag=first.headers["ETag"])
        conditional = await client.get(_url(ceremony, ceremony.a1), headers={"If-None-Match": header})

    assert conditional.status_code == 304, header


async def test_a_replaced_clip_changes_the_etag(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        first = await client.get(_url(ceremony, ceremony.a1))
        await session.execute(
            update(users_media)
            .where(users_media.c.user_id == ceremony.a1)
            .values(audio_base64=b64encode(_WAV).decode(), dta_audio=_DTA + timedelta(minutes=5))
        )
        await session.commit()
        stale = await client.get(_url(ceremony, ceremony.a1), headers={"If-None-Match": first.headers["ETag"]})

    assert stale.status_code == 200
    assert stale.content == _WAV
    assert stale.headers["ETag"] != first.headers["ETag"]


async def test_304_runs_one_narrow_query_and_no_signature_check(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("detect_audio_mime must not run on a 304")

    async with _client(app) as client:
        first = await client.get(_url(ceremony, ceremony.a1))
        monkeypatch.setattr(team_audio_service, "detect_audio_mime", _boom)
        with StatementProbe(session.bind) as probe:  # type: ignore[arg-type]
            second = await client.get(_url(ceremony, ceremony.a1), headers={"If-None-Match": first.headers["ETag"]})

    assert second.status_code == 304
    assert not probe.selected_any("audio_base64", "audio_mime", "foto_base64", "avatar_base64")
    assert probe.count_selecting("users_media") == 1


async def test_a_miss_loads_only_the_audio_column(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client, StatementProbe(session.bind) as probe:  # type: ignore[arg-type]
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 200
    assert probe.selected_any("audio_base64")
    assert not probe.selected_any("audio_mime", "foto_base64", "avatar_base64")


async def test_a_team_with_no_clip_is_404_without_a_blob_query(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=None)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client, StatementProbe(session.bind) as probe:  # type: ignore[arg-type]
        response = await client.get(_url(ceremony, ceremony.a1))

    assert response.status_code == 404
    assert not probe.selected_any("audio_base64")


async def test_a_clip_without_a_timestamp_still_gets_a_stable_etag(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode(), dta_audio=None)
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        first = await client.get(_url(ceremony, ceremony.a1))
        second = await client.get(_url(ceremony, ceremony.a1), headers={"If-None-Match": first.headers["ETag"]})

    assert first.headers["ETag"] == '"audio-0"'
    assert second.status_code == 304


# ---------------------------------------------------------------------------
# Scope isolation and the uniform 404
# ---------------------------------------------------------------------------


async def test_site_scope_serves_its_own_team(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, ceremony.a1, scope=ceremony.site_a))

    assert response.status_code == 200


async def test_site_scope_cannot_reach_another_sites_team(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.b1, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        foreign = await client.get(_url(ceremony, ceremony.b1, scope=ceremony.site_a))
        unknown = await client.get(_url(ceremony, "no-such-team", scope=ceremony.site_a))
        globally = await client.get(_url(ceremony, ceremony.b1))

    assert foreign.status_code == 404
    assert foreign.json() == unknown.json()
    assert globally.status_code == 200


async def test_a_non_team_user_is_not_addressable(session: AsyncSession, uberadmin: UberAdmin) -> None:
    from web.models.contest import Contest

    ceremony = await seed_ceremony(session, uberadmin)
    contest = await session.get(Contest, ceremony.contest_id)
    assert contest is not None
    judge = make_user(contest, uberadmin, "audio-judge", role=RoleEnum.JUDGE)
    session.add(judge)
    await session.commit()
    await _set_audio(session, judge.id, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, judge.id))

    assert response.status_code == 404


async def test_a_team_of_another_contest_is_not_addressable(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    other = await make_contest(session, uberadmin, slug="audio-other")
    other_site = await make_site(session, other, sitename="Other Campus")
    outsider = make_user(other, uberadmin, "audio-outsider", site_id=other_site.id)
    session.add(outsider)
    await session.commit()
    await _set_audio(session, outsider.id, audio=b64encode(_MP3).decode())
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, outsider.id))

    assert response.status_code == 404


async def test_disabled_contest_and_bad_scope_are_the_same_bare_404(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    await _set_audio(session, ceremony.a1, audio=b64encode(_MP3).decode())
    disabled = await make_contest(session, uberadmin, slug="audio-off", animator_enabled=False)
    await session.commit()
    app = _build_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        unknown_slug = await client.get(f"/c/audio-nope/teams/{ceremony.a1}/audio?scope=global")
        disabled_contest = await client.get(f"/c/{disabled.login_slug}/teams/{ceremony.a1}/audio?scope=global")
        bad_scope = await client.get(_url(ceremony, ceremony.a1, scope="not-a-site"))
        no_clip = await client.get(_url(ceremony, ceremony.a2))

    # A team with no clip is indistinguishable from a team that does not exist:
    # absence of optional media is not a fact worth disclosing separately.
    for response in (unknown_slug, disabled_contest, bad_scope, no_clip):
        assert response.status_code == 404
        assert response.json() == unknown_slug.json()
