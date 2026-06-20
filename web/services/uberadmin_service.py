#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.email_validation import EmailValidationService
from shared.services.password_service import generate_diceware_password
from web.config import settings
from web.models.users import UberAdmin
from web.services.contest_user_service import normalize_username
from web.services.profile_service import validate_new_password


@dataclass
class UberAdminCreationResult:
    """Result object for UberAdmin account creation requests.

    Attributes:
        success: Whether the UberAdmin account was created successfully.
        error: User-facing error message when creation fails, otherwise an empty string.
        form_data: Form values to preserve when re-rendering the creation form.
        created_credential: Generated credentials to show after a successful creation.
    """

    success: bool
    error: str
    form_data: dict[str, Any]
    created_credential: dict[str, str] | None


@dataclass
class UberAdminUpdateResult:
    """Result object for UberAdmin account update requests.

    Attributes:
        success: Whether the UberAdmin account was updated successfully.
        error: User-facing error message when the update fails.
        changed_password: Plaintext password when it was changed, otherwise None.
    """

    success: bool
    error: str
    changed_password: str | None


async def create_uberadmin_account(
    session: AsyncSession,
    *,
    creator_username: str,
    fullname: str,
    email: str,
    username: str,
) -> UberAdminCreationResult:
    """Validate and create a new UberAdmin account.

    Args:
        session: Database session used to validate uniqueness and persist the account.
        creator_username: Username of the UberAdmin creating the new account.
        fullname: Submitted full name for the new UberAdmin.
        email: Submitted email address for the new UberAdmin.
        username: Submitted username for the new UberAdmin.

    Returns:
        An `UberAdminCreationResult` containing either a user-facing error or
        the generated credential for the new account.

    Notes:
        On success, this function generates a diceware password, persists the
        new `UberAdmin`, commits the session, and returns the plaintext password
        for immediate display/download.
    """
    normalized_username = normalize_username(username)
    form_data = {"fullname": fullname, "email": email, "username": normalized_username}

    if not normalized_username:
        return UberAdminCreationResult(False, "Username is required.", form_data, None)
    if not fullname.strip():
        return UberAdminCreationResult(False, "Full name is required.", form_data, None)

    try:
        normalized_email = EmailValidationService.normalize(email)
    except ValueError:
        return UberAdminCreationResult(False, "Invalid email address.", form_data, None)

    existing_username = (
        await session.execute(select(UberAdmin).where(UberAdmin.username == normalized_username))
    ).scalar_one_or_none()
    if existing_username:
        return UberAdminCreationResult(False, "Username already exists.", form_data, None)

    existing_email = (
        await session.execute(select(UberAdmin).where(UberAdmin.email_normalizado == normalized_email))
    ).scalar_one_or_none()
    if existing_email:
        return UberAdminCreationResult(False, "Email is already in use.", form_data, None)

    password = generate_diceware_password(settings)
    new_admin = UberAdmin(
        username=normalized_username,
        fullname=fullname.strip(),
        created_by_uberadmin=creator_username,
    )
    new_admin.email = email
    new_admin.password = password
    session.add(new_admin)
    await session.commit()

    return UberAdminCreationResult(
        True,
        "",
        {"fullname": "", "email": "", "username": ""},
        {"username": normalized_username, "password": password, "fullname": fullname.strip(), "email": email},
    )


async def list_uberadmins(session: AsyncSession, *, query: str | None = None) -> list[UberAdmin]:
    """Return UberAdmins ordered by full name, optionally filtered by text search.

    Args:
        session: Database session used to query UberAdmin accounts.
        query: Optional substring matched against full name or normalized email.

    Returns:
        A list of matching UberAdmin rows ordered by full name.
    """
    stmt = select(UberAdmin)
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(
            or_(
                UberAdmin.fullname.ilike(pattern),
                UberAdmin.email_normalizado.ilike(pattern),
            )
        )
    result = await session.execute(stmt.order_by(UberAdmin.fullname.asc()))
    return list(result.scalars().all())


async def get_uberadmin_by_id(session: AsyncSession, uberadmin_id: str) -> UberAdmin | None:
    """Return an UberAdmin by ID or None when it does not exist.

    Args:
        session: Database session used to load the account.
        uberadmin_id: Primary key of the target UberAdmin.

    Returns:
        The matching UberAdmin, or None.
    """
    return (await session.execute(select(UberAdmin).where(UberAdmin.id == uberadmin_id))).scalar_one_or_none()


async def update_uberadmin(
    session: AsyncSession,
    *,
    uberadmin_id: str,
    fullname: str,
    email: str,
    new_password: str,
) -> UberAdminUpdateResult:
    """Validate and update an existing UberAdmin account.

    Args:
        session: Database session used to load and persist the account.
        uberadmin_id: Primary key of the target UberAdmin.
        fullname: Submitted full name.
        email: Submitted email address.
        new_password: Optional plaintext replacement password.

    Returns:
        An `UberAdminUpdateResult` with either a user-facing error or the
        changed plaintext password for one-time display.
    """
    uberadmin = await get_uberadmin_by_id(session, uberadmin_id)
    if uberadmin is None:
        return UberAdminUpdateResult(False, "UberAdmin not found.", None)

    cleaned_fullname = fullname.strip()
    if not cleaned_fullname:
        return UberAdminUpdateResult(False, "Full name is required.", None)

    try:
        normalized_email = EmailValidationService.normalize(email)
    except ValueError:
        return UberAdminUpdateResult(False, "Invalid email address.", None)

    existing_email = (
        await session.execute(
            select(UberAdmin).where(
                UberAdmin.email_normalizado == normalized_email,
                UberAdmin.id != uberadmin_id,
            )
        )
    ).scalar_one_or_none()
    if existing_email is not None:
        return UberAdminUpdateResult(False, "Email is already in use.", None)

    changed_password = new_password.strip() or None
    if changed_password:
        password_error = validate_new_password(changed_password)
        if password_error:
            return UberAdminUpdateResult(False, password_error, None)

    uberadmin.fullname = cleaned_fullname
    uberadmin.email = normalized_email
    if changed_password:
        uberadmin.password = changed_password
    await session.commit()

    return UberAdminUpdateResult(True, "", changed_password)


async def toggle_uberadmin_status(
    session: AsyncSession,
    *,
    uberadmin_id: str,
    actor_id: str,
) -> UberAdmin | None:
    """Enable or disable an UberAdmin account.

    Args:
        session: Database session used to load and persist the account.
        uberadmin_id: Primary key of the target UberAdmin.
        actor_id: Primary key of the authenticated UberAdmin performing the action.

    Returns:
        The updated UberAdmin, or None when the target does not exist.

    Raises:
        ValueError: If the actor attempts to disable their own account.
    """
    uberadmin = await get_uberadmin_by_id(session, uberadmin_id)
    if uberadmin is None:
        return None
    if uberadmin.id == actor_id:
        raise ValueError("Cannot disable your own account.")

    uberadmin.is_enabled = not uberadmin.is_enabled
    await session.commit()
    return uberadmin
