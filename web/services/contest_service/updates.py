#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest metadata write flows."""

from __future__ import annotations

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import contest_languages as contest_languages_table
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemLanguageLimit
from web.models.users import UberAdmin, User
from web.services.site_service import sync_contest_sites

from .authorization import ensure_contest_admin_or_uberadmin
from .forms import ContestMetadataInput
from .models import ContestMetadataResult
from .presentation import build_contest_metadata_view
from .queries import get_active_languages, get_contest_language_ids
from .validation import validate_contest_metadata_update


async def validate_contest_language_selection(
    session: AsyncSession,
    *,
    contest: Contest,
    language_ids: list[str],
) -> tuple[list[Language], list[str], list[str]]:
    """Validate submitted contest-language IDs for a metadata update."""
    active_languages = await get_active_languages(session)
    active_by_id = {language.id: language for language in active_languages}
    selected_language_ids = list(dict.fromkeys(language_ids))
    current_language_ids = await get_contest_language_ids(session, contest)

    if not (contest.upcoming and contest.active):
        if selected_language_ids != current_language_ids:
            return (
                active_languages,
                current_language_ids,
                ["Allowed languages can only be changed before contest start."],
            )
        return active_languages, current_language_ids, []

    if not selected_language_ids:
        return active_languages, selected_language_ids, ["At least one language must be selected."]

    invalid_language_ids = [language_id for language_id in selected_language_ids if language_id not in active_by_id]
    if invalid_language_ids:
        return active_languages, selected_language_ids, ["One or more selected languages are unavailable."]

    return active_languages, selected_language_ids, []


async def deactivate_past_contest(session: AsyncSession, contest_id: str) -> Contest | None:
    """Mark an active past contest as inactive.

    Args:
        session: Database session used to load and persist the contest.
        contest_id: Primary key of the contest to deactivate.

    Returns:
        The deactivated contest, or ``None`` when the contest is missing,
        already inactive, running, or upcoming.
    """
    contest = (
        await session.execute(
            select(Contest).where(
                Contest.id == contest_id,
                Contest.active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if contest is None or not contest.is_past:
        return None

    contest.active = False
    await session.commit()
    await session.refresh(contest)
    return contest


async def sync_contest_languages(
    session: AsyncSession,
    contest: Contest,
    *,
    language_ids: list[str],
) -> list[str]:
    """Rewrite contest languages and delete stale per-problem overrides for removed languages."""
    current_language_ids = set(await get_contest_language_ids(session, contest))
    selected_language_ids = list(dict.fromkeys(language_ids))
    removed_language_ids = current_language_ids - set(selected_language_ids)

    if removed_language_ids:
        await session.execute(
            delete(ProblemLanguageLimit).where(
                ProblemLanguageLimit.language_id.in_(tuple(removed_language_ids)),
                ProblemLanguageLimit.problem_id.in_(select(Problem.id).where(Problem.contest_id == contest.id)),
            )
        )

    await session.execute(delete(contest_languages_table).where(contest_languages_table.c.contest_id == contest.id))
    await session.execute(
        insert(contest_languages_table),
        [{"contest_id": contest.id, "language_id": language_id} for language_id in selected_language_ids],
    )
    await session.flush()
    return selected_language_ids


async def update_contest_metadata(
    session: AsyncSession,
    contest: Contest,
    actor: User | UberAdmin,
    *,
    metadata: ContestMetadataInput,
    site_names: list[str],
    language_ids: list[str],
) -> ContestMetadataResult:
    """Validate, authorize, and persist a contest metadata update."""
    ensure_contest_admin_or_uberadmin(actor)
    _, selected_language_ids, language_errors = await validate_contest_language_selection(
        session,
        contest=contest,
        language_ids=language_ids,
    )
    result = validate_contest_metadata_update(
        contest,
        metadata=metadata,
        site_names=site_names,
        selected_language_ids=selected_language_ids,
    )
    if not result.success or language_errors:
        return ContestMetadataResult(
            success=False,
            errors=result.errors + language_errors,
            view=result.view,
        )

    try:
        persisted_site_names = await sync_contest_sites(session, contest, site_names)
    except ValueError as exc:
        return ContestMetadataResult(
            success=False,
            errors=[str(exc)],
            view=build_contest_metadata_view(
                contest,
                site_names=site_names,
                selected_language_ids=selected_language_ids,
            ),
        )

    persisted_language_ids = selected_language_ids
    if contest.upcoming and contest.active:
        persisted_language_ids = await sync_contest_languages(
            session,
            contest,
            language_ids=selected_language_ids,
        )

    await session.commit()
    await session.refresh(contest)
    return ContestMetadataResult(
        success=True,
        errors=[],
        view=build_contest_metadata_view(
            contest,
            site_names=persisted_site_names,
            selected_language_ids=persisted_language_ids,
        ),
    )
