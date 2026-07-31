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
  dimension limits (so a decompression bomb is refused, not expanded), and the
  route's ``ETag``/``304`` handling keeps a projector from re-fetching — and
  therefore re-validating — the same photo.
"""

from __future__ import annotations

import binascii
import logging
from base64 import b64decode
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.query_records import TeamMediaRecord
from shared.db_schema import users, users_media
from shared.enumerations import RoleEnum
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.imageprocessing_service.validation import image_validation

__all__ = [
    "PLACEHOLDER_MIME",
    "TeamImage",
    "TeamMediaKind",
    "load_team_media",
    "placeholder_image",
    "select_team_image",
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


async def load_team_media(
    session: AsyncSession,
    *,
    contest_id: str,
    team_id: str,
    site_id: str | None,
) -> TeamMediaRecord | None:
    """Load one team's media row, constrained to the ceremony's scope.

    The lookup is narrowed by every axis at once — the contest, ``RoleEnum.TEAM``,
    and (for a site-scoped ceremony) the site — so a site spectator cannot read a
    team of another site or another contest, and a judge or admin account is
    never addressable as a team.

    One query serves both media routes: the photo and the optional audio clip are
    read together, so the scope predicates cannot drift between them.

    Args:
        session: Active database session.
        contest_id: The enabled contest the request resolved to.
        team_id: Requested team identifier.
        site_id: Site the request is scoped to, or ``None`` for global scope.

    Returns:
        The team's record (with ``None`` media fields when it has no
        ``users_media`` row), or ``None`` when no such team exists in scope.
    """
    stmt = (
        select(
            users.c.id,
            users.c.username,
            users.c.fullname,
            users.c.site_id,
            users_media.c.com_foto,
            users_media.c.foto_base64,
            users_media.c.avatar_base64,
            users_media.c.dta_foto,
            users_media.c.audio_base64,
            users_media.c.audio_mime,
            users_media.c.dta_audio,
        )
        .select_from(users.outerjoin(users_media, users_media.c.user_id == users.c.id))
        .where(
            users.c.id == team_id,
            users.c.contest_id == contest_id,
            users.c.role == RoleEnum.TEAM,
        )
    )
    if site_id is not None:
        stmt = stmt.where(users.c.site_id == site_id)

    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return TeamMediaRecord(
        team_id=str(row.id),
        username=str(row.username),
        fullname=str(row.fullname),
        site_id=str(row.site_id) if row.site_id is not None else None,
        com_foto=bool(row.com_foto),
        foto_base64=row.foto_base64,
        avatar_base64=row.avatar_base64,
        dta_foto=row.dta_foto,
        audio_base64=row.audio_base64,
        audio_mime=row.audio_mime,
        dta_audio=row.dta_audio,
    )


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


def select_team_image(media: TeamMediaRecord) -> TeamImage:
    """Choose the image to serve: photo, then avatar, then placeholder.

    Args:
        media: The team's stored media record.

    Returns:
        The first candidate that decodes to a recognizable image; the
        placeholder when none does. Never ``None`` — the modal must always get a
        valid image.
    """
    if media.com_foto:
        candidates: tuple[tuple[str | None, TeamMediaKind], ...] = (
            (media.foto_base64, "photo"),
            (media.avatar_base64, "avatar"),
        )
        for payload, kind in candidates:
            candidate = _decode_candidate(payload, team_id=media.team_id, kind=kind)
            if candidate is not None:
                return candidate
    return placeholder_image()
