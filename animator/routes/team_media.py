#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Scoped team photo and audio delivery for public Animator presentations.

Both routes share one scoped lookup, one conditional-request implementation, one
caching policy, and one per-IP rate-limit bucket; they differ only in what "no
usable media" means. A photo always resolves (placeholder), while an audio clip
is optional and its absence is an honest ``404``.

The photo route, four guarantees:

- **Scope cannot leak another site's teams.** The team is looked up by contest,
  ``RoleEnum.TEAM``, and — for a site-scoped request — site, all in one query, so
  a site spectator addressing a foreign team gets the same ``404`` a nonexistent
  team gets.
- **A team in scope always yields a valid image.** Stored photo, else stored
  avatar, else the checked-in placeholder; the served ``Content-Type`` is sniffed
  from the bytes actually being sent (see
  :mod:`animator.services.team_media_service`).
- **A ``304`` is nearly free.** The stored blobs are multi-megabyte base64 in
  the database and a photo is *decoded* to be vouched for, so the conditional
  check runs **before** either. One narrow query yields the media kind, its
  revision, and which blobs exist; if ``If-None-Match`` names a tier possible at
  that revision the answer is ``304`` with no blob ever read. Only a miss loads
  the one column it needs.
- **The tag names the tier actually served.** ``"photo-<v>"``, ``"avatar-<v>"``,
  and — when stored candidates fell through to the placeholder — a versioned
  ``"placeholder-<v>"``, so a re-upload (which moves the revision) can never be
  answered from a stale ``304``. The static ``"placeholder"`` is issued only when
  no stored media is enabled at all.

The audio route adds one guarantee of its own: **a clip is served only if it can
be vouched for.** A missing, undecodable, empty, or unrecognizable payload is a
``404``, so the modal hides its player instead of rendering a broken one, and the
served content type is sniffed from the bytes rather than read from the stored
``audio_mime`` claim (see :mod:`animator.services.team_audio_service`). Neither
route advertises ``Accept-Ranges``: range requests are not implemented, and
claiming support the server does not have would break seeking rather than enable
it.

The cache contract: ``dta_foto`` / ``dta_audio`` are the media revisions. Every
supported write path moves or clears them, so a blob that changes changes its
tag. An out-of-band rewrite of a blob at the *same* instant is outside the
contract.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from animator.dependencies import DbSession, EnabledContest, PublicScopeDep, enforce_public_rate_limit
from animator.models.query_records import TeamMediaMetadata, ensure_utc
from animator.services.team_audio_service import resolve_team_audio
from animator.services.team_media_service import TeamImage, load_team_media_metadata, resolve_team_image

router = APIRouter(prefix="/c/{slug}", tags=["animator-team-media"])

_CACHE_MAX_AGE = 300
"""Seconds a team photo or clip may be reused without revalidation.

Short by design: media can be replaced mid-event, and the conditional ``304``
is nearly free. Long enough that a projector redrawing the same modal does not
refetch megabytes.
"""

_CACHE_CONTROL = f"public, max-age={_CACHE_MAX_AGE}"
"""This media is public presentation data — no per-viewer variation."""

_STATIC_PLACEHOLDER_ETAG = '"placeholder"'
"""Tag for the placeholder when no stored media is enabled: a static asset."""


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


def _has_enabled_media(media: TeamMediaMetadata) -> bool:
    """Whether any stored image blob is a candidate at all."""
    return media.com_foto and (media.has_photo or media.has_avatar)


def build_etag(image: TeamImage, media: TeamMediaMetadata) -> str:
    """Build the strong entity tag for one resolved image.

    The tag combines the media *kind* with the stored photo's revision, so it
    changes when the bytes could have changed: a new upload moves ``dta_foto``,
    and a fall-through from photo to avatar (or to the placeholder) moves the
    kind. The placeholder is versioned only when stored candidates fell through
    to it — a re-upload must then miss — and static when nothing is enabled.

    Args:
        image: The image about to be served.
        media: The team's media metadata.

    Returns:
        A quoted, strong ETag.
    """
    if image.kind == "placeholder" and not _has_enabled_media(media):
        return _STATIC_PLACEHOLDER_ETAG
    return f'"{image.kind}-{_version_of(media.dta_foto)}"'


def possible_photo_etags(media: TeamMediaMetadata) -> tuple[str, ...]:
    """Every tag the photo route could issue for this metadata, without decoding.

    A client validator outside this set is stale by construction; one inside it
    names a tier that is possible at the current revision, and is honored as a
    ``304``.
    """
    if not _has_enabled_media(media):
        return (_STATIC_PLACEHOLDER_ETAG,)
    version = _version_of(media.dta_foto)
    tags: list[str] = []
    if media.has_photo:
        tags.append(f'"photo-{version}"')
    if media.has_avatar:
        tags.append(f'"avatar-{version}"')
    tags.append(f'"placeholder-{version}"')
    return tuple(tags)


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


def matching_etag(header: str | None, candidates: tuple[str, ...]) -> str | None:
    """Return the candidate an ``If-None-Match`` header matches, if any.

    Implements the RFC 9110 comparison the browser cache actually relies on:

    - ``*`` matches any existing representation (the first candidate);
    - the header may be a **comma-separated list** of validators;
    - comparison is **weak**, so a validator the client (or an intermediary)
      returned as ``W/"x"`` still matches the strong ``"x"`` we issued. Weak
      comparison is the correct rule for ``If-None-Match`` — strong comparison
      belongs to ``If-Range``, and using it here would silently disable caching
      for any proxy that weakens tags.

    Args:
        header: Raw ``If-None-Match`` value, or ``None``.
        candidates: The tags this response could carry, in preference order.

    Returns:
        The matched candidate, or ``None`` when the client holds none of them.
    """
    if not header or not candidates:
        return None
    validators = [part.strip() for part in header.split(",")]
    if "*" in validators:
        return candidates[0]
    held = {_strip_weak(part) for part in validators if part}
    return next((tag for tag in candidates if _strip_weak(tag) in held), None)


def etag_matches(header: str | None, etag: str) -> bool:
    """Whether an ``If-None-Match`` header matches one ``etag`` (see :func:`matching_etag`)."""
    return matching_etag(header, (etag,)) is not None


def _strip_weak(value: str) -> str:
    """Drop a ``W/`` prefix so two tags compare weakly."""
    return value[2:] if value.startswith("W/") else value


def _kind_of(etag: str) -> str:
    """Recover the image kind a photo tag names (``"photo-1"`` → ``photo``)."""
    return etag.strip('"').split("-", 1)[0]


async def _load_metadata(
    db: DbSession, contest: EnabledContest, scope: PublicScopeDep, team_id: str
) -> TeamMediaMetadata:
    """Run the one narrow scoped query both routes start with, or raise ``404``."""
    media = await load_team_media_metadata(db, contest_id=contest.id, team_id=team_id, site_id=scope.site_id)
    if media is None:
        raise HTTPException(status_code=404)
    return media


@router.get("/teams/{team_id}/photo", name="animator_team_photo", dependencies=[Depends(enforce_public_rate_limit)])
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
        validator still names a tier possible at the current revision — decided
        from metadata alone, before any blob is read. Both carry the ``ETag``
        and ``Cache-Control``, plus ``X-NOCA-Team-Image-Kind`` so the modal can
        distinguish a real photo or avatar from the placeholder. A ``304``
        refreshes the client's freshness lifetime instead of stranding a cached
        entry that must be revalidated on every use.

    Raises:
        HTTPException: ``404`` when no such team exists within this scope.
    """
    media = await _load_metadata(db, contest, scope, team_id)

    held = matching_etag(request.headers.get("if-none-match"), possible_photo_etags(media))
    if held is not None:
        return Response(status_code=304, headers=_photo_headers(held, _kind_of(held)))

    image = await resolve_team_image(db, media)
    etag = build_etag(image, media)
    return Response(content=image.data, media_type=image.mime, headers=_photo_headers(etag, image.kind))


def _photo_headers(etag: str, kind: str) -> dict[str, str]:
    """Headers shared by the ``200`` and ``304`` photo answers."""
    return {"ETag": etag, "Cache-Control": _CACHE_CONTROL, "X-NOCA-Team-Image-Kind": kind}


@router.get("/teams/{team_id}/audio", name="animator_team_audio", dependencies=[Depends(enforce_public_rate_limit)])
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
        ``304`` with no body when the client's validator still matches — decided
        from metadata alone, before the clip is read. Both carry the ``ETag``
        and ``Cache-Control``. Never ``Accept-Ranges``: range requests are not
        implemented here.

    Raises:
        HTTPException: ``404`` when no such team exists within this scope, or the
            team has no usable clip.
    """
    media = await _load_metadata(db, contest, scope, team_id)
    if not media.has_audio:
        raise HTTPException(status_code=404)

    etag = build_audio_etag(media.dta_audio)
    headers = {"ETag": etag, "Cache-Control": _CACHE_CONTROL}
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    audio = await resolve_team_audio(db, media)
    if audio is None:
        raise HTTPException(status_code=404)
    return Response(content=audio.data, media_type=audio.mime, headers=headers)


__all__ = ["build_audio_etag", "build_etag", "etag_matches", "matching_etag", "possible_photo_etags", "router"]
