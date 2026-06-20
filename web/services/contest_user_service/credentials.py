#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Credential, role, and form validation helpers for contest users."""

from __future__ import annotations

from shared.enumerations import RoleEnum
from shared.services.email_validation import EmailValidationService
from shared.services.password_service import (
    PasswordPolicy,
    PasswordPolicyError,
    generate_diceware_password,
)
from web.config import settings

from .models import _FORBIDDEN_ROLES, _ROLE_NAME_MAP


def resolve_password(raw: str | None) -> str:
    """Resolve a usable password for contest-user creation or update."""
    resolved, _ = resolve_password_core(raw)
    return resolved


def resolve_password_core(raw: str | None) -> tuple[str, str | None]:
    """Resolve a password and detail message in a single policy check path."""
    if raw:
        policy = PasswordPolicy(settings)
        try:
            policy.validate_new_password(raw)
            return raw, None
        except PasswordPolicyError as exc:
            return (
                generate_diceware_password(settings),
                f"Submitted password did not satisfy policy; generated a new password instead. {exc}",
            )
    return generate_diceware_password(settings), None


def resolve_password_with_detail(raw: str | None) -> tuple[str, str | None]:
    """Resolve a password and explain when a generated fallback was used."""
    return resolve_password_core(raw)


def ensure_role_allowed(role: RoleEnum) -> None:
    """Ensure a role is allowed for contest-managed users."""
    if role in _FORBIDDEN_ROLES:
        raise ValueError(f"Role {role.value!r} is not allowed for contest users.")


def normalize_optional_email(raw_email: str | None) -> str | None:
    """Normalize an optional email value."""
    value = (raw_email or "").strip()
    if not value:
        return None
    try:
        return EmailValidationService.normalize(value)
    except ValueError as exc:
        raise ValueError("Invalid email address.") from exc


def normalize_username(username: str) -> str:
    """Normalize a submitted username for contest-user operations."""
    return username.strip().lower()


def parse_import_role(raw_role: str) -> RoleEnum:
    """Parse a batch-import role string into a contest user role."""
    normalized_role = raw_role.strip().lower()
    role = _ROLE_NAME_MAP.get(normalized_role)
    if role is None:
        raise ValueError("Invalid role. Use one of: admin, judge, staff, team, user.")
    ensure_role_allowed(role)
    return role


def parse_single_user_role(raw_role: str) -> RoleEnum:
    """Parse a single-user form role value into a contest user role."""
    role = RoleEnum(raw_role)
    ensure_role_allowed(role)
    return role


def validate_create_user_form(
    username: str,
    fullname: str,
    raw_role: str,
    email: str,
) -> tuple[str, str, str | None, RoleEnum | None, list[str]]:
    """Validate basic fields for the create-user form."""
    normalized_username = normalize_username(username)
    cleaned_fullname = fullname.strip()
    normalized_email: str | None = None
    errors: list[str] = []

    if not normalized_username:
        errors.append("Username is required.")
    if not cleaned_fullname:
        errors.append("Full name is required.")

    try:
        role = parse_single_user_role(raw_role)
    except ValueError:
        errors.append(f"Invalid role: {raw_role!r}.")
        role = None

    try:
        normalized_email = normalize_optional_email(email)
    except ValueError as exc:
        errors.append(str(exc))

    return normalized_username, cleaned_fullname, normalized_email, role, errors


def validate_edit_user_form(fullname: str, email: str) -> tuple[str, str | None, list[str]]:
    """Validate basic fields for the edit-user form."""
    cleaned_fullname = fullname.strip()
    normalized_email: str | None = None
    errors: list[str] = []

    if not cleaned_fullname:
        errors.append("Full name is required.")

    try:
        normalized_email = normalize_optional_email(email)
    except ValueError as exc:
        errors.append(str(exc))

    return cleaned_fullname, normalized_email, errors
