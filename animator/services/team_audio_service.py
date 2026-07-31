#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Defensive selection of a team's optional ceremony audio clip.

The audio counterpart of :mod:`animator.services.team_media_service`, and it
differs from it in one decisive way: a photo request must **always** succeed
(there is a placeholder), while audio is genuinely optional. Every failure here
therefore resolves to ``None``, which the route turns into a ``404`` — never a
``500``, and never a half-usable response that would leave the modal showing a
broken player.

Three decisions are deliberate:

- **The stored ``audio_mime`` claim is never served.** It describes bytes this
  module did not produce, so the served type comes from
  :func:`shared.services.audio_signature.detect_audio_mime`, the same
  signature-based policy the Web upload path enforces. One owner means a clip
  that could be uploaded can always be played back, and nothing else can be
  served at all. A row whose claim disagrees with its content is served
  correctly, or not at all.
- **Every failure mode is caught.** Base64 decoding raises ``binascii.Error`` or
  ``ValueError``; the shared detector raises ``AudioSignatureError`` for content
  it cannot identify or cannot accept. Missing any of them would convert an
  unusable stored blob into a ``500`` where the contract calls for ``404``.
- **Log level separates "absent" from "broken".** A team with no clip is the
  ordinary case and logs at ``debug``; a clip that is present but unusable is a
  data problem an operator should see, and logs at ``warning``.

Unlike the photo path this module does **not** decode the media. Decoding audio
would require a native library for no benefit: a browser that cannot play a
signature-valid clip surfaces that through the ``<audio>`` element's ``error``
event, which the ceremony modal already handles as "no usable clip".
"""

from __future__ import annotations

import binascii
import logging
from base64 import b64decode
from dataclasses import dataclass

from animator.models.query_records import TeamMediaRecord
from shared.services.audio_signature import AudioSignatureError, detect_audio_mime

__all__ = ["TeamAudio", "select_team_audio"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamAudio:
    """One resolved audio response body.

    Attributes:
        data: The audio bytes to serve.
        mime: Canonical content type, sniffed from ``data`` rather than trusted
            from the stored claim.
    """

    data: bytes
    mime: str


def select_team_audio(media: TeamMediaRecord) -> TeamAudio | None:
    """Resolve the team's playable audio clip, or ``None`` when there is none.

    Args:
        media: The team's stored media record.

    Returns:
        The clip and its canonical MIME type, or ``None`` when the team has no
        clip or the stored payload cannot be vouched for. Callers translate
        ``None`` into ``404``; this function never raises for bad stored data.
    """
    payload = media.audio_base64
    if not payload:
        # The ordinary case for most teams: no clip was ever uploaded.
        logger.debug("team %s has no stored audio clip", media.team_id)
        return None

    try:
        data = b64decode(payload, validate=True)
    except binascii.Error, ValueError:
        logger.warning("team %s has an undecodable stored audio clip; serving none", media.team_id)
        return None
    if not data:
        logger.warning("team %s has an empty stored audio clip; serving none", media.team_id)
        return None

    try:
        canonical = detect_audio_mime(data)
    except AudioSignatureError as exc:
        # Unidentifiable or unsupported content. Catching the shared base class
        # is what keeps a bad stored blob from surfacing as a 500 instead of the
        # contract's 404.
        logger.warning("team %s has an unusable stored audio clip (%s); serving none", media.team_id, exc)
        return None
    return TeamAudio(data=data, mime=canonical)
