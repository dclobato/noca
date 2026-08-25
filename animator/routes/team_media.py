#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Scoped team photo and audio delivery for public Animator presentations.

Both routes share one scoped lookup, one conditional-request implementation, and
one caching policy; they differ only in what "no usable media" means. A photo
always resolves (placeholder), while an audio clip is optional and its absence is
an honest ``404``.

The photo route, three guarantees:

- **Scope cannot leak another site's teams.** The team is looked up by contest,
  ``RoleEnum.TEAM``, and — for a site-scoped request — site, all in one query, so
  a site spectator addressing a foreign team gets the same ``404`` a nonexistent
  team gets.
- **A team in scope always yields a valid image.** Stored photo, else stored
  avatar, else the checked-in placeholder; the served ``Content-Type`` is sniffed
  from the bytes actually being sent (see
  :mod:`animator.services.team_media_service`).
- **Repeat requests are cheap.** A projector reopens the same handful of photos
  all ceremony, and these payloads are multi-megabyte base64 blobs in the
  database. The response is therefore conditional: an ``ETag`` built from the
  media kind and ``dta_foto``, answered with ``304`` (and the same validators)
  when the client already holds it.

The audio route adds one guarantee of its own: **a clip is served only if it can
be vouched for.** A missing, undecodable, empty, or unrecognizable payload is a
``404``, so the modal hides its player instead of rendering a broken one, and the
served content type is sniffed from the bytes rather than read from the stored
``audio_mime`` claim (see :mod:`animator.services.team_audio_service`). Neither
route advertises ``Accept-Ranges``: range requests are not implemented, and
claiming support the server does not have would break seeking rather than enable
it.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from animator.dependencies import DbSession, EnabledContest, PublicScopeDep
from animator.models.query_records import TeamMediaRecord, ensure_utc
from animator.services.team_audio_service import select_team_audio
from animator.services.team_media_service import TeamImage, load_team_media, select_team_image

router = APIRouter(prefix="/c/{slug}", tags=["animator-team-media"])

_CACHE_MAX_AGE = 300
"""Seconds a team photo or clip may be reused without revalidation.

Short by design: media can be replaced mid-event, and the conditional ``304``
already makes revalidation nearly free. Long enough that a projector redrawing
the same modal does not refetch megabytes.
"""

_CACHE_CONTROL = f"public, max-age={_CACHE_MAX_AGE}"
"""This media is public presentation data — no per-viewer variation."""


def media_base_url(request: Request, route_name: str, slug: str) -> str:
    """Return the route-derived ``/teams`` base used by presentation clients.

    Args:
        request: Current request, used for reverse-proxy-aware URL generation.
        route_name: Named team-media route used to derive the base.
        slug: Contest login slug.

    Returns:
        Absolute URL ending at the route's ``/teams`` segment.
    """
    return str(request.url_for(route_name, slug=slug, team_id="_")).rsplit("/", 2)[0]


def build_etag(image: TeamImage, dta_foto: datetime | None) -> str:
    """Build the strong entity tag for one resolved image.

    The tag combines the media *kind* with the stored photo's update instant, so
    it changes when the bytes could have changed: a new upload moves ``dta_foto``,
    and a fall-through from photo to avatar (or to the placeholder) moves the
    kind. The placeholder is a static asset and carries no version component.

    Args:
        image: The image about to be served.
        dta_foto: The row's last photo-update instant, if any.

    Returns:
        A quoted, strong ETag.
    """
    if image.kind == "placeholder":
        return '"placeholder"'
    return f'"{image.kind}-{_version_of(dta_foto)}"'


def build_audio_etag(dta_audio: datetime | None) -> str:
    """Build the strong entity tag for one team's audio clip.

    There is no fallback chain for audio — a clip is either served or absent — so
    the tag needs no kind component beyond the constant ``audio``: only a new
    upload, which moves ``dta_audio``, can change the bytes.

    Args:
        dta_audio: The row's last audio-update instant, if any.

    Returns:
        A quoted, strong ETag.
    """
    return f'"audio-{_version_of(dta_audio)}"'


def _version_of(updated_at: datetime | None) -> int:
    """Return the microsecond version component of a media timestamp.

    Matches ``UserMedia.audio_cache_version`` in the Web module, so the animator
    and the Web module derive the same version from the same row.
    """
    return int(ensure_utc(updated_at).timestamp() * 1_000_000) if updated_at is not None else 0


def etag_matches(header: str | None, etag: str) -> bool:
    """Whether an ``If-None-Match`` header matches ``etag``.

    Implements the RFC 9110 comparison the browser cache actually relies on:

    - ``*`` matches any existing representation;
    - the header may be a **comma-separated list** of candidates;
    - comparison is **weak**, so a validator the client (or an intermediary)
      returned as ``W/"x"`` still matches the strong ``"x"`` we issued. Weak
      comparison is the correct rule for ``If-None-Match`` — strong comparison
      belongs to ``If-Range``, and using it here would silently disable caching
      for any proxy that weakens tags.

    Args:
        header: Raw ``If-None-Match`` value, or ``None``.
        etag: The entity tag this response would carry.

    Returns:
        True when the client already holds this representation.
    """
    if not header:
        return False
    candidates = [part.strip() for part in header.split(",")]
    if "*" in candidates:
        return True
    return any(_strip_weak(part) == _strip_weak(etag) for part in candidates if part)


def _strip_weak(value: str) -> str:
    """Drop a ``W/`` prefix so two tags compare weakly."""
    return value[2:] if value.startswith("W/") else value


@router.get("/teams/{team_id}/photo", name="animator_team_photo")
async def team_photo(
    request: Request,
    contest: EnabledContest,
    scope: PublicScopeDep,
    db: DbSession,
    team_id: str,
) -> Response:
    """Serve one team's photo, avatar, or placeholder for the ceremony modal.

    Args:
        request: Current request, read for ``If-None-Match``.
        contest: The resolved enabled contest.
        scope: The resolved ceremony scope constraining the lookup.
        db: Active database session.
        team_id: The team whose photo to serve.

    Returns:
        ``200`` with the image bytes, or ``304`` with no body when the client's
        validator still matches. Both carry the ``ETag`` and ``Cache-Control``,
        plus ``X-NOCA-Team-Image-Kind`` so the modal can distinguish a real
        photo or avatar from the placeholder. A ``304`` refreshes the client's
        freshness lifetime instead of stranding a cached entry that must be
        revalidated on every use.

    Raises:
        HTTPException: ``404`` when no such team exists within this scope.
    """
    media: TeamMediaRecord | None = await load_team_media(
        db,
        contest_id=contest.id,
        team_id=team_id,
        site_id=scope.site_id,
    )
    if media is None:
        raise HTTPException(status_code=404)

    image = select_team_image(media)
    etag = build_etag(image, media.dta_foto)
    headers = {
        "ETag": etag,
        "Cache-Control": _CACHE_CONTROL,
        "X-NOCA-Team-Image-Kind": image.kind,
    }

    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=image.data, media_type=image.mime, headers=headers)


@router.get("/teams/{team_id}/audio", name="animator_team_audio")
async def team_audio(
    request: Request,
    contest: EnabledContest,
    scope: PublicScopeDep,
    db: DbSession,
    team_id: str,
) -> Response:
    """Serve one team's optional audio clip for the ceremony modal.

    Scope isolation is inherited from the same lookup the photo route uses, so a
    site spectator addressing another site's team gets the same ``404`` a
    nonexistent team gets — and, deliberately, the same ``404`` a team with no
    clip gets. Absence of a clip is not an error condition to report; it is the
    normal state of most teams, and the modal simply hides its player.

    Args:
        request: Current request, read for ``If-None-Match``.
        contest: The resolved enabled contest.
        scope: The resolved ceremony scope constraining the lookup.
        db: Active database session.
        team_id: The team whose clip to serve.

    Returns:
        ``200`` with the audio bytes under their sniffed canonical type, or
        ``304`` with no body when the client's validator still matches. Both
        carry the ``ETag`` and ``Cache-Control``. Never ``Accept-Ranges``: range
        requests are not implemented here.

    Raises:
        HTTPException: ``404`` when no such team exists within this scope, or the
            team has no usable clip.
    """
    media: TeamMediaRecord | None = await load_team_media(
        db,
        contest_id=contest.id,
        team_id=team_id,
        site_id=scope.site_id,
    )
    if media is None:
        raise HTTPException(status_code=404)

    audio = select_team_audio(media)
    if audio is None:
        raise HTTPException(status_code=404)

    etag = build_audio_etag(media.dta_audio)
    headers = {"ETag": etag, "Cache-Control": _CACHE_CONTROL}

    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=audio.data, media_type=audio.mime, headers=headers)


__all__ = ["build_audio_etag", "build_etag", "etag_matches", "router"]
