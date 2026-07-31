#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Validation and persistence for contest-user photo and audio media."""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.audio_signature import (
    UnrecognizedAudioError,
    UnsupportedAudioError,
    detect_audio_mime,
)
from shared.services.imageprocessing_service import ImageProcessingResult
from web.audio_upload_limits import DEFAULT_AUDIO_MAX_FILE_SIZE, MAX_AUDIO_FILE_SIZE
from web.models._base import _utcnow
from web.models.users import User, UserMedia


@dataclass(frozen=True)
class AudioProcessingResult:
    """Validated audio payload ready for database storage."""

    content: bytes
    mime_type: str


async def process_audio_upload(
    upload: UploadFile,
    max_file_size: int = DEFAULT_AUDIO_MAX_FILE_SIZE,
) -> AudioProcessingResult:
    """Validate an MP3, OGG, or WAV upload by size and file signature.

    Args:
        upload: Audio file supplied by the client.
        max_file_size: Configured byte ceiling, up to the 5 MiB hard cap.

    Returns:
        AudioProcessingResult: Validated audio bytes and their canonical MIME type.

    Raises:
        ValueError: The configured limit or uploaded audio is invalid.
    """
    if not 0 < max_file_size <= MAX_AUDIO_FILE_SIZE:
        raise ValueError("Audio maximum file size must be between 1 byte and 5 MiB.")

    await upload.seek(0)
    content = await upload.read(max_file_size + 1)
    if not content:
        raise ValueError("Please select a non-empty audio file.")
    if len(content) > max_file_size:
        maximum_megabytes = max_file_size / (1024 * 1024)
        raise ValueError(f"Audio file too large. Maximum allowed: {maximum_megabytes:.1f} MB.")

    # The accepted-format policy is shared with the animator, which re-validates
    # the same bytes before serving them; only the wording of a refusal is
    # Web's own, because it is rendered on the upload form.
    try:
        canonical_mime = detect_audio_mime(content)
    except UnrecognizedAudioError as exc:
        raise ValueError("The selected file is not a recognized MP3, OGG, or WAV audio clip.") from exc
    except UnsupportedAudioError as exc:
        raise ValueError("Unsupported audio format. Use MP3, OGG, or WAV.") from exc
    return AudioProcessingResult(content=content, mime_type=canonical_mime)


async def get_user_media(session: AsyncSession, user_id: str) -> UserMedia | None:
    """Return the stored media row for a contest user, if one exists."""
    return await session.get(UserMedia, user_id)


async def _get_or_create_user_media(session: AsyncSession, user: User) -> UserMedia:
    """Return the user's media row, creating it when needed."""
    media = await get_user_media(session, user.id)
    if media is None:
        media = UserMedia(user_id=user.id, com_foto=False)
        session.add(media)
    return media


async def update_photo(session: AsyncSession, user: User, result: ImageProcessingResult) -> UserMedia:
    """Store a processed photo and avatar for a contest user."""
    media = await _get_or_create_user_media(session, user)
    media.apply_processed_photo(
        foto_base64=result.imagem_base64,
        avatar_base64=result.avatar_base64,
        mime_type=result.mime_type,
    )
    user.updated_at = _utcnow()
    await session.commit()
    return media


async def remove_photo(session: AsyncSession, user: User) -> UserMedia | None:
    """Clear a contest user's stored photo and avatar."""
    media = await get_user_media(session, user.id)
    if media is not None:
        media.clear_foto_fields()
        user.updated_at = _utcnow()
        await session.commit()
    return media


async def update_audio(
    session: AsyncSession,
    user: User,
    result: AudioProcessingResult,
) -> UserMedia:
    """Store a validated audio clip for a contest user."""
    media = await _get_or_create_user_media(session, user)
    media.apply_audio(
        audio_base64=b64encode(result.content).decode("ascii"),
        mime_type=result.mime_type,
    )
    user.updated_at = _utcnow()
    await session.commit()
    return media


async def remove_audio(session: AsyncSession, user: User) -> UserMedia | None:
    """Clear a contest user's stored audio clip."""
    media = await get_user_media(session, user.id)
    if media is not None:
        media.clear_audio_fields()
        user.updated_at = _utcnow()
        await session.commit()
    return media
