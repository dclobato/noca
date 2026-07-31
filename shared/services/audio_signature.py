#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The single owner of which audio formats NOCA accepts, and how they are named.

Two runtimes care about this policy and they must not disagree. The Web module
validates an upload before storing it; the animator validates the stored bytes
again before serving them to a ceremony projector. If those two ever accepted
different sets, a clip could be stored and then be unplayable — or, worse, be
served under a type the uploader never validated.

The policy therefore lives here, framework-neutral (no FastAPI, no ORM, no HTTP),
and each caller translates the outcome into its own vocabulary:

- Web turns a rejection into the ``ValueError`` its upload form already renders.
- The animator turns a rejection into ``None``, which its route answers as
  ``404`` — a clip it cannot vouch for is treated as no clip at all.

Detection is by **file signature**, never by a caller-supplied MIME claim: the
bytes are the only trustworthy description of content the validating code did not
produce itself.
"""

from __future__ import annotations

import puremagic

__all__ = [
    "SUPPORTED_AUDIO_MIME_TYPES",
    "AudioSignatureError",
    "UnrecognizedAudioError",
    "UnsupportedAudioError",
    "detect_audio_mime",
]

SUPPORTED_AUDIO_MIME_TYPES = {
    "audio/mpeg": "audio/mpeg",
    "audio/mp3": "audio/mpeg",
    "audio/ogg": "audio/ogg",
    "application/ogg": "audio/ogg",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
}
"""Detected MIME type -> canonical MIME type served to clients.

Deliberately narrow: MP3, OGG, and WAV are what browsers play natively without a
codec dependency, and every stored clip must be one of them.
"""


class AudioSignatureError(ValueError):
    """Base class for a payload that is not an acceptable audio clip.

    Subclasses ``ValueError`` so a caller that only cares about "bad audio" can
    catch one type, while the two subclasses below let a caller distinguish
    *unidentifiable* content from *identified but unwanted* content.
    """


class UnrecognizedAudioError(AudioSignatureError):
    """The payload's type could not be identified at all."""


class UnsupportedAudioError(AudioSignatureError):
    """The payload was identified, but its format is not accepted.

    Attributes:
        detected_mime: The type that was detected and refused.
    """

    def __init__(self, detected_mime: str) -> None:
        """Record the refused type and build a readable message.

        Args:
            detected_mime: The detected, unsupported MIME type.
        """
        self.detected_mime = detected_mime
        super().__init__(f"unsupported audio format: {detected_mime}")


def detect_audio_mime(content: bytes) -> str:
    """Return the canonical MIME type of an audio payload, by signature.

    Args:
        content: Raw candidate audio bytes.

    Returns:
        The canonical MIME type: ``audio/mpeg``, ``audio/ogg``, or ``audio/wav``.

    Raises:
        UnrecognizedAudioError: The content is empty or its type cannot be
            identified.
        UnsupportedAudioError: The content was identified as a type NOCA does not
            accept.
    """
    if not content:
        raise UnrecognizedAudioError("empty audio content")
    try:
        detected = puremagic.from_string(content, mime=True)
    except (puremagic.PureError, ValueError) as exc:
        # puremagic raises PureError for content it cannot match and ValueError
        # for input it cannot process; both mean "not identifiable".
        raise UnrecognizedAudioError("unidentifiable audio content") from exc

    canonical = SUPPORTED_AUDIO_MIME_TYPES.get(detected)
    if canonical is None:
        raise UnsupportedAudioError(detected)
    return canonical
