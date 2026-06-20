#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest creation helpers."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import contest_languages as contest_languages_table
from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import UberAdmin, User
from web.services.contest_user_service import normalize_username
from web.services.contest_user_service.validation import normalize_optional_email, resolve_password
from web.services.site_service import normalize_site_name_key

from .forms import ContestMetadataInput
from .models import ContestCreationResult
from .validation import validate_contest_metadata_fields

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def build_blank_contest_form(default_start_time: str) -> dict[str, Any]:
    """Build default form values for the create-contest screen."""
    return {
        "contest_name": "",
        "login_slug": "",
        "contest_url": "",
        "start_time": default_start_time,
        "contest_timezone": "America/Sao_Paulo",
        "duration_minutes": "300",
        "stop_updating_scoreboard": "290",
        "stop_answers_after": "290",
        "clarifications_timeout_minutes": "20",
        "tasks_timeout_minutes": "10",
        "review_timeout_minutes": "10",
        "max_problem_file_size_bytes": "32768",
        "wa_penalty": "20",
        "show_limits": True,
        "autojudge_only": True,
        "allow_print_requests": True,
        "accept_pe": False,
        "ce_adds_penalty": False,
        "owner_username": "",
        "owner_fullname": "",
        "owner_email": "",
        "owner_password": "",
        "language_ids": [],
    }


async def create_contest_with_owner(
    session: AsyncSession,
    *,
    creator_username: str,
    contest_name: str,
    login_slug: str,
    metadata: ContestMetadataInput,
    owner_username: str,
    owner_fullname: str,
    owner_email: str,
    owner_password: str,
    language_ids: list[str],
) -> ContestCreationResult:
    """Validate and create a contest together with its initial admin owner."""
    normalized_owner_username = normalize_username(owner_username)
    cleaned_owner_fullname = owner_fullname.strip()
    cleaned_owner_password = owner_password.strip()
    form_data = {
        "contest_name": contest_name,
        "login_slug": login_slug,
        **metadata.to_form_data(contest_name=contest_name, login_slug=login_slug),
        "owner_username": normalized_owner_username,
        "owner_fullname": cleaned_owner_fullname,
        "owner_email": owner_email,
        "owner_password": cleaned_owner_password,
        "language_ids": language_ids,
    }

    errors: list[str] = []
    normalized_owner_email: str | None = None
    if not contest_name.strip():
        errors.append("Contest name is required.")
    if not login_slug.strip():
        errors.append("Slug is required.")
    elif not _SLUG_RE.match(login_slug.strip()):
        errors.append("Slug must contain only lowercase letters, numbers, and hyphens (e.g. my-contest-2026).")
    if not normalized_owner_username:
        errors.append("Owner's contest username is required.")
    if not cleaned_owner_fullname:
        errors.append("Owner's full name is required.")
    try:
        normalized_owner_email = normalize_optional_email(owner_email)
    except ValueError as exc:
        errors.append(str(exc))
    if not language_ids:
        errors.append("At least one language must be selected.")

    validated, metadata_errors = validate_contest_metadata_fields(metadata)
    errors.extend(metadata_errors)

    if errors:
        return ContestCreationResult(False, errors, form_data, None)

    existing_slug = (
        await session.execute(select(Contest).where(Contest.login_slug == login_slug.strip()))
    ).scalar_one_or_none()
    if existing_slug:
        return ContestCreationResult(False, ["A contest with this slug already exists."], form_data, None)

    uberadmin = (
        await session.execute(select(UberAdmin).where(UberAdmin.username == creator_username))
    ).scalar_one_or_none()
    if uberadmin is None:
        return ContestCreationResult(False, ["Authentication error: UberAdmin not found."], form_data, None)

    password = resolve_password(cleaned_owner_password or None)
    assert validated is not None
    contest = Contest(
        contest_name=contest_name.strip(),
        login_slug=login_slug.strip(),
        contest_url=validated.contest_url,
        start_time=validated.start_time,
        contest_timezone=validated.contest_timezone,
        duration_minutes=validated.duration_minutes,
        stop_answers_after=validated.stop_answers_after,
        stop_updating_scoreboard=validated.stop_updating_scoreboard,
        clarifications_timeout_minutes=validated.clarifications_timeout_minutes,
        tasks_timeout_minutes=validated.tasks_timeout_minutes,
        review_timeout_minutes=validated.review_timeout_minutes,
        max_problem_file_size_bytes=validated.max_problem_file_size_bytes,
        wa_penalty=validated.wa_penalty,
        show_limits=validated.show_limits,
        autojudge_only=validated.autojudge_only,
        allow_print_requests=validated.allow_print_requests,
        accept_pe=validated.accept_pe,
        ce_adds_penalty=validated.ce_adds_penalty,
        created_by_uberadmin_id=uberadmin.id,
        active=True,
    )

    owner = User(
        username=normalized_owner_username,
        fullname=cleaned_owner_fullname,
        role=RoleEnum.ADMIN,
        created_by_uberadmin_id=uberadmin.id,
        email_normalizado=normalized_owner_email,
    )
    owner.password = password
    contest.members.append(owner)
    contest.sites.append(
        Site(
            sitename="Main",
            sitename_normalized=normalize_site_name_key("Main"),
        )
    )

    session.add(contest)
    await session.flush()
    contest.owner_user_id = owner.id
    await session.execute(
        insert(contest_languages_table),
        [{"contest_id": contest.id, "language_id": language_id} for language_id in language_ids],
    )
    await session.commit()

    return ContestCreationResult(
        True,
        [],
        form_data,
        {
            "contest_name": contest_name.strip(),
            "contest_slug": login_slug.strip(),
            "username": normalized_owner_username,
            "fullname": cleaned_owner_fullname,
            "password": password,
            "email": normalized_owner_email or "",
        },
    )
