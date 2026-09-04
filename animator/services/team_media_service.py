#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Scoped team-photo loading and defensive image selection.

The ceremony's team modal must **never** show broken-image chrome, so this
module answers with a usable image for every team that exists in scope: the
stored full photo, else the stored avatar, else a checked-in placeholder.

Two decisions are deliberate and documented rather than implicit:

- **``com_foto`` is authoritative.** When it is false, both stored blobs are
  ignored even if present — the same semantics the Web media properties apply
  (``web/models/users.py``), so the animator cannot resurrect a photo a user
  removed.
- **Stored bytes are re-verified structurally, and the served MIME comes from
  the bytes.** Uploads were validated when written, but a stored blob can still
  be truncated or rewritten, and ``foto_mime`` is only a *claim* about content
  the animator did not produce. Each candidate is base64-decoded defensively and
  then run through the shared ``image_validation``, which actually **decodes**
  the image and reports its real format and dimensions.

  A signature sniff alone would not be enough: an eight-byte PNG header passes
  ``detect_image_type`` and still renders as broken-image chrome, which is
  exactly the outcome this module exists to prevent. Validation is what lets the
  fallback chain be trusted — a truncated photo *falls through to the avatar*
  rather than being served as a corrupt ``image/png``. The cost is bounded: the
  payloads are upload-limited, the decode is capped by explicit pixel and
  dimension limits (so a decompression bomb is refused, not expanded), and a
  conditional request never reaches it at all.
- **Metadata first, one blob at a time.** :func:`load_team_media_metadata`
  selects no payload column: the route answers a matching ``If-None-Match``
  from the media kind and revision alone. Only a cache miss calls
  :func:`resolve_team_image`, which loads the stored photo and — only if that
  photo fails validation — the avatar, each through its own single-column
  query. A ``304`` therefore costs one narrow row, never a decode.
"""

from __future__ import annotations

import binascii
import logging
from base64 import b64decode
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.query_records import TeamMediaMetadata
from shared.db_schema import users, users_media
from shared.enumerations import RoleEnum
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.imageprocessing_service.validation import image_validation

__all__ = [
    "PLACEHOLDER_MIME",
    "TeamImage",
    "TeamMediaKind",
    "load_avatar_payload",
    "load_photo_payload",
    "load_team_media_metadata",
    "placeholder_image",
    "resolve_team_image",
    "scoped_team_query",
]

logger = logging.getLogger(__name__)

_PLACEHOLDER_PATH = Path(__file__).resolve().parent.parent / "assets" / "team_placeholder.svg"

PLACEHOLDER_MIME = "image/svg+xml"
"""MIME type of the checked-in fallback artwork."""

_SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
"""Formats accepted for a *stored* blob.

Wider than the upload path's ``{JPEG, PNG, WEBP}`` on purpose: this is a read
path serving rows some other code wrote, possibly before that set narrowed. It
still excludes SVG, which is never a stored user photo and would be an
active-content vector if it were served from a stored blob.
"""

_MAX_IMAGE_PIXELS = 8000 * 8000
_MAX_DIMENSION = 8000
"""Decode limits for a stored blob.

Generous for any real team photo and far below Pillow's default bomb threshold,
so a hostile or corrupt row is refused during validation instead of expanding
into animator memory while a ceremony is on screen.
"""

TeamMediaKind = Literal["photo", "avatar", "placeholder"]
"""Which of the three sources answered a photo request."""


@dataclass(frozen=True)
class TeamImage:
    """One resolved image response body.

    Attributes:
        kind: Which source answered — ``photo``, ``avatar``, or ``placeholder``.
        data: The image bytes to serve.
        mime: The content type, sniffed from ``data`` for stored blobs.
    """

    kind: TeamMediaKind
    data: bytes
    mime: str


@lru_cache(maxsize=1)
def placeholder_image() -> TeamImage:
    """Return the checked-in placeholder, read from disk once per process."""
    return TeamImage(kind="placeholder", data=_PLACEHOLDER_PATH.read_bytes(), mime=PLACEHOLDER_MIME)


def scoped_team_query(
    *columns: ColumnElement[Any],
    contest_id: str,
    team_id: str,
    site_id: str | None,
) -> Select[tuple[Any, ...]]:
    """Build the one scoped ``users`` ⟕ ``users_media`` lookup every media read uses.

    The lookup is narrowed by every axis at once — the contest, ``RoleEnum.TEAM``,
    and (for a site-scoped ceremony) the site — so a site spectator cannot read a
    team of another site or another contest, and a judge or admin account is
    never addressable as a team. Both media routes, and both the metadata and
    payload reads, go through this builder so the scope predicates cannot drift.

    Args:
        *columns: Columns to select.
        contest_id: The enabled contest the request resolved to.
        team_id: Requested team identifier.
        site_id: Site the request is scoped to, or ``None`` for global scope.

    Returns:
        The scoped select statement.
    """
    stmt = (
        select(*columns)
        .select_from(users.outerjoin(users_media, users_media.c.user_id == users.c.id))
        .where(
            users.c.id == team_id,
            users.c.contest_id == contest_id,
            users.c.role == RoleEnum.TEAM,
        )
    )
    if site_id is not None:
        stmt = stmt.where(users.c.site_id == site_id)
    return stmt


def _present(column: ColumnElement[str | None]) -> ColumnElement[bool]:
    """SQL predicate: the blob column is stored and non-empty."""
    return func.coalesce(func.length(column) > 0, false())


async def load_team_media_metadata(
    session: AsyncSession,
    *,
    contest_id: str,
    team_id: str,
    site_id: str | None,
) -> TeamMediaMetadata | None:
    """Load one team's media *metadata*, constrained to the ceremony's scope.

    No payload column is selected: the row carries the flag, the two revisions,
    and one presence boolean per blob, computed in SQL. This is the whole cost
    of a conditional request that still matches.

    Args:
        session: Active database session.
        contest_id: The enabled contest the request resolved to.
        team_id: Requested team identifier.
        site_id: Site the request is scoped to, or ``None`` for global scope.

    Returns:
        The team's metadata (all-false/``None`` media fields when it has no
        ``users_media`` row), or ``None`` when no such team exists in scope.
    """
    stmt = scoped_team_query(
        users.c.id,
        users_media.c.com_foto,
        _present(users_media.c.foto_base64),
        _present(users_media.c.avatar_base64),
        _present(users_media.c.audio_base64),
        users_media.c.dta_foto,
        users_media.c.dta_audio,
        contest_id=contest_id,
        team_id=team_id,
        site_id=site_id,
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    team, com_foto, has_photo, has_avatar, has_audio, dta_foto, dta_audio = row
    return TeamMediaMetadata(
        team_id=str(team),
        com_foto=bool(com_foto),
        has_photo=bool(has_photo),
        has_avatar=bool(has_avatar),
        has_audio=bool(has_audio),
        dta_foto=dta_foto,
        dta_audio=dta_audio,
    )


async def _load_payload(session: AsyncSession, column: ColumnElement[str | None], team_id: str) -> str | None:
    """Load exactly one stored blob column for a team already proven in scope."""
    stmt = select(column).where(users_media.c.user_id == team_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def load_photo_payload(session: AsyncSession, team_id: str) -> str | None:
    """Load only ``foto_base64`` for a team the metadata query already scoped."""
    return await _load_payload(session, users_media.c.foto_base64, team_id)


async def load_avatar_payload(session: AsyncSession, team_id: str) -> str | None:
    """Load only ``avatar_base64`` for a team the metadata query already scoped."""
    return await _load_payload(session, users_media.c.avatar_base64, team_id)


def _decode_candidate(payload: str | None, *, team_id: str, kind: TeamMediaKind) -> TeamImage | None:
    """Decode and structurally validate one stored blob, or return ``None``.

    Three ways a candidate is rejected, all of them falling through to the next
    source: the payload is missing, it is not decodable base64, or the decoded
    bytes are not an image this module can vouch for. "Vouch for" means the image
    actually **decoded** — a valid signature over truncated data is rejected here,
    because serving it would produce exactly the broken image the fallback chain
    exists to avoid. Empty decoded bytes count as invalid.

    Every rejection is logged at ``warning``: the request still succeeds through
    the fallback, but a corrupt stored blob is a data problem an operator should
    see.
    """
    if not payload:
        return None
    try:
        data = b64decode(payload, validate=True)
    except binascii.Error, ValueError:
        logger.warning("team %s has an undecodable stored %s; falling through", team_id, kind)
        return None
    if not data:
        logger.warning("team %s has an empty stored %s; falling through", team_id, kind)
        return None
    try:
        metadata = image_validation(
            data,
            max_image_pixels=_MAX_IMAGE_PIXELS,
            max_dimension=_MAX_DIMENSION,
            enforce_format=True,
            supported_formats=_SUPPORTED_FORMATS,
        )
    except ImageProcessingError as exc:
        logger.warning("team %s has an unusable stored %s (%s); falling through", team_id, kind, exc)
        return None
    return TeamImage(kind=kind, data=data, mime=metadata.mime_type)


async def resolve_team_image(session: AsyncSession, media: TeamMediaMetadata) -> TeamImage:
    """Choose the image to serve: photo, then avatar, then placeholder.

    Each candidate is loaded through its own single-column query, and only when
    the metadata says it is stored: a valid photo never pulls the avatar blob, and
    a team without stored media never queries a blob at all.

    Args:
        session: Active database session.
        media: The team's media metadata.

    Returns:
        The first candidate that decodes to a recognizable image; the
        placeholder when none does. Never ``None`` — the modal must always get a
        valid image.
    """
    if media.com_foto:
        if media.has_photo:
            payload = await load_photo_payload(session, media.team_id)
            candidate = _decode_candidate(payload, team_id=media.team_id, kind="photo")
            if candidate is not None:
                return candidate
        if media.has_avatar:
            payload = await load_avatar_payload(session, media.team_id)
            candidate = _decode_candidate(payload, team_id=media.team_id, kind="avatar")
            if candidate is not None:
                return candidate
    return placeholder_image()
