#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Profile service: validators and async update functions for self-service profile management."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from werkzeug.security import check_password_hash

from shared.services.email_validation import EmailValidationService
from shared.services.imageprocessing_service import ImageProcessingResult
from shared.services.password_service import PasswordPolicy, PasswordPolicyError
from web.config import settings
from web.models.users import UberAdmin, User

_policy = PasswordPolicy(settings)
ProfileActor = UberAdmin | User


def validate_fullname(fullname: str) -> str:
    """Return stripped fullname or raise ValueError if empty."""
    stripped = fullname.strip()
    if not stripped:
        raise ValueError("Display name cannot be empty.")
    return stripped


def validate_new_password(new_password: str) -> str | None:
    """Validate password strength. Return None if valid, or error message string."""
    try:
        _policy.validate_new_password(new_password)
    except PasswordPolicyError as exc:
        return str(exc)
    return None


def validate_email(email: str) -> str | None:
    """Validate and normalize optional email.

    Args:
        email: Raw submitted email value.

    Returns:
        Normalized email or ``None`` when blank.

    Raises:
        ValueError: If email is invalid.
    """
    value = email.strip()
    if not value:
        return None
    try:
        return EmailValidationService.normalize(value)
    except ValueError as exc:
        raise ValueError("Invalid email address.") from exc


async def update_fullname(session: AsyncSession, user: ProfileActor, fullname: str) -> None:
    """Update user's display name and commit."""
    user.fullname = validate_fullname(fullname)
    await session.commit()


async def update_email(session: AsyncSession, user: ProfileActor, email: str) -> None:
    """Update current-user email and commit.

    Args:
        session: Active DB session.
        user: Profile actor.
        email: Raw submitted email value.
    """
    normalized_email = validate_email(email)
    if isinstance(user, UberAdmin) and normalized_email is None:
        raise ValueError("Email cannot be empty for UberAdmin accounts.")
    user.email_normalizado = normalized_email
    await session.commit()


async def update_password(
    session: AsyncSession,
    user: ProfileActor,
    new_password: str,
    *,
    current_password: str | None = None,
) -> str | None:
    """
    Update user password with optional current password verification.

    If current_password is provided, verifies it against user.password_hash.
    Returns None on success, or an error message string on failure.
    """
    if current_password is not None and not check_password_hash(user.password_hash, current_password):
        return "Current password is incorrect."

    error = validate_new_password(new_password)
    if error:
        return error

    user.password = new_password
    await session.commit()
    return None


async def update_photo(session: AsyncSession, user: User, result: ImageProcessingResult) -> None:
    """Apply processed photo to user and commit."""
    user.apply_processed_photo(
        foto_base64=result.imagem_base64,
        avatar_base64=result.avatar_base64,
        mime_type=result.mime_type,
    )
    await session.commit()


async def remove_photo(session: AsyncSession, user: User) -> None:
    """Clear user photo fields and commit."""
    user.clear_foto_fields()
    await session.commit()
