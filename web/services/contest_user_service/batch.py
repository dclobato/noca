"""Batch import flow for contest users."""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from web.models.contest import Contest
from web.models.users import UberAdmin, User

from .crud import create_user, update_user
from .models import (
    _USERNAME_RE,
    BatchImportResult,
    BatchUserRow,
    UserImportResult,
    parse_optional_bool_field,
)
from .permissions import ensure_contest_user_add_or_edit_allowed
from .sites import resolve_or_create_import_site
from .validation import (
    normalize_optional_email,
    normalize_username,
    parse_import_role,
    resolve_password_with_detail,
)


async def _reload_after_rollback(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
) -> None:
    """Reload the objects a failed row's rollback expired, so the next rows can still read them.

    The async driver cannot refresh an expired attribute implicitly on access, and the
    remaining rows keep reading the contest and the actor.
    """
    instances: tuple[Any, ...] = (contest, actor)
    for instance in instances:
        if inspect(instance).persistent:
            await session.refresh(instance)


async def batch_import_users(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
    users_data: list[BatchUserRow],
    *,
    allow_concurrent_login: bool = True,
) -> BatchImportResult:
    """Create or update contest users from a parsed batch payload.

    Args:
        allow_concurrent_login: The default session policy for the users this
            import **creates**, from the upload form's checkbox. A row that
            updates an existing user and says nothing about the flag leaves it
            untouched: an import is a roster, and silently reversing an
            organiser's per-team decision because a CSV was re-uploaded would be
            a change nobody asked for.

    Notes:
        A row may carry its own `allow_concurrent_login` field, and an explicit
        value **wins over the form default and applies on update as well**. The
        distinction is between a statement and a default: the checkbox says what
        to do with rows that are silent, while a value written in the file is
        the author saying what this particular user's policy is. Anything that
        is neither of the accepted spellings fails that row rather than being
        read as one of them, because a typo silently restricting a roster and a
        typo silently leaving it unrestricted are both worse than a refusal.
    """
    ensure_contest_user_add_or_edit_allowed(contest)

    results: list[UserImportResult] = []
    created = updated = failed = skipped = 0
    seen_usernames: set[str] = set()

    for user_dict in users_data:
        username = normalize_username(str(user_dict.get("username") or ""))
        raw_role = str(user_dict.get("role") or "").strip()
        fullname = str(user_dict.get("fullname") or "").strip()
        raw_email = user_dict.get("email")
        email_value: str | None = None if raw_email is None else str(raw_email).strip() or None
        raw_site = user_dict.get("site")
        site_name = None if raw_site is None else str(raw_site).strip() or None
        raw_password = user_dict.get("password")
        password: str | None = None if raw_password is None else str(raw_password).strip() or None
        raw_location = user_dict.get("location")
        location_value: str | None = None if raw_location is None else (str(raw_location).strip()[:16] or None)
        try:
            row_allow_concurrent_login = parse_optional_bool_field(user_dict.get("allow_concurrent_login"))
        except ValueError as exc:
            results.append(
                UserImportResult(
                    username=username,
                    fullname=fullname,
                    role=raw_role,
                    email=email_value,
                    status="failed",
                    password=None,
                    site=site_name,
                    location=location_value,
                    detail=f"allow_concurrent_login: {exc}",
                )
            )
            failed += 1
            continue

        if not username or not _USERNAME_RE.match(username):
            results.append(
                UserImportResult(
                    username=username,
                    fullname=fullname,
                    role=raw_role,
                    email=email_value,
                    status="failed",
                    password=None,
                    site=site_name,
                    location=location_value,
                    detail=(
                        "Username must be non-empty and contain only alphanumeric characters, hyphens, or underscores."
                    ),
                )
            )
            failed += 1
            continue

        if username in seen_usernames:
            results.append(
                UserImportResult(
                    username=username,
                    fullname=fullname,
                    role=raw_role,
                    email=email_value,
                    status="skipped",
                    password=None,
                    site=site_name,
                    location=location_value,
                    detail="Duplicate entry in this batch.",
                )
            )
            skipped += 1
            continue

        if not fullname:
            results.append(
                UserImportResult(
                    username=username,
                    fullname=fullname,
                    role=raw_role,
                    email=email_value,
                    status="failed",
                    password=None,
                    site=site_name,
                    location=location_value,
                    detail="Full name is required.",
                )
            )
            failed += 1
            continue

        try:
            role = parse_import_role(raw_role)
        except ValueError as exc:
            results.append(
                UserImportResult(
                    username=username,
                    fullname=fullname,
                    role=raw_role,
                    email=email_value,
                    status="failed",
                    password=None,
                    site=site_name,
                    location=location_value,
                    detail=str(exc),
                )
            )
            failed += 1
            continue

        seen_usernames.add(username)

        try:
            normalized_email = normalize_optional_email(email_value)
            existing_user = (
                await session.execute(
                    select(User).where(
                        User.username == username,
                        User.contest_id == contest.id,
                    )
                )
            ).scalar_one_or_none()
            site = await resolve_or_create_import_site(session, contest, role=role, raw_site=raw_site)

            if existing_user is None:
                # An explicit row value wins over the form's default for this user.
                effective_allow_concurrent_login = (
                    allow_concurrent_login if row_allow_concurrent_login is None else row_allow_concurrent_login
                )
                actual_password, created_password_detail = resolve_password_with_detail(password)
                _, actual_password = await create_user(
                    session,
                    contest,
                    actor,
                    username=username,
                    fullname=fullname,
                    role=role,
                    password=actual_password,
                    email=normalized_email,
                    site_id=site.id if site is not None else None,
                    location=location_value,
                    allow_concurrent_login=effective_allow_concurrent_login,
                )
                results.append(
                    UserImportResult(
                        username=username,
                        fullname=fullname,
                        role=raw_role,
                        email=normalized_email,
                        status="created",
                        password=actual_password,
                        site=site.sitename if site is not None else None,
                        location=location_value,
                        detail=created_password_detail,
                        allow_concurrent_login=effective_allow_concurrent_login,
                    )
                )
                created += 1
                continue

            resolved_password: str | None = None
            password_detail: str | None = None
            if password is not None:
                resolved_password, password_detail = resolve_password_with_detail(password)

            updated_password = await update_user(
                session,
                contest,
                existing_user,
                fullname=fullname,
                role=role,
                password=resolved_password,
                email=normalized_email,
                site_id=site.id if site is not None else None,
                location=location_value,
                allow_concurrent_login=row_allow_concurrent_login,
            )
            results.append(
                UserImportResult(
                    username=username,
                    fullname=fullname,
                    role=raw_role,
                    email=normalized_email,
                    status="updated",
                    password=updated_password,
                    site=site.sitename if site is not None else None,
                    location=location_value,
                    detail=password_detail,
                    # `None` when the row said nothing, which is exactly "left
                    # as the organiser set it" rather than a value this import
                    # chose.
                    allow_concurrent_login=row_allow_concurrent_login,
                )
            )
            updated += 1
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            await _reload_after_rollback(session, contest, actor)
            results.append(
                UserImportResult(
                    username=username,
                    fullname=fullname,
                    role=raw_role,
                    email=email_value,
                    status="failed",
                    password=None,
                    site=site_name,
                    location=location_value,
                    detail=str(exc),
                )
            )
            failed += 1

    return BatchImportResult(
        created=created,
        updated=updated,
        failed=failed,
        skipped=skipped,
        results=results,
    )
