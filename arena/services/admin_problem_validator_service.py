#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Staging of Arena custom-validator candidates.

Shared by the standalone validator upload route and by the problem edit form,
whose single Save carries the validator alongside everything else.

The work is split so callers can reject a bad upload before touching the
database: :func:`parse_validator_upload` only reads and checks the upload, while
:func:`stage_candidate_revision` needs a persisted problem. The caller owns the
transaction — staging returns the queue payload to enqueue *after* the commit,
so a delayed worker never sees a token that was rolled back.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem, ArenaProblemCustomValidator
from arena.routes.admin_problem_common import validator_languages
from arena.services import admin_problem_interaction_service
from shared.queue_schema import CustomValidatorValidationJob
from shared.services.custom_validator import (
    build_validation_job,
    parse_validator_source,
    stage_candidate,
    status_view,
)

_BOTH_REQUIRED = "Choose a validator language and source file together."


@dataclass(frozen=True)
class ValidatorUpload:
    """A validator source that passed every check short of being persisted."""

    language_id: str
    source: str


async def parse_validator_upload(
    session: AsyncSession,
    *,
    language_id: str,
    source_file: UploadFile | None,
) -> ValidatorUpload | None:
    """Read and check a validator upload without touching the database.

    Returns:
        The parsed upload, or ``None`` when neither a language nor a file was
        supplied (meaning: no validator requested).

    Raises:
        ValueError: If only one of language/file is given, the language is not
            globally active, or the source is not valid UTF-8.
    """
    language_id = (language_id or "").strip()
    if source_file is None or not source_file.filename:
        if not language_id:
            return None
        raise ValueError(_BOTH_REQUIRED)
    if not language_id:
        raise ValueError(_BOTH_REQUIRED)

    active_ids = {row.id for row in await validator_languages(session)}
    if language_id not in active_ids:
        raise ValueError("Validator language is not active.")

    return ValidatorUpload(language_id=language_id, source=parse_validator_source(await source_file.read()))


async def stage_candidate_revision(
    session: AsyncSession,
    problem: ArenaProblem,
    upload: ValidatorUpload,
) -> CustomValidatorValidationJob:
    """Stage ``upload`` as ``problem``'s candidate validator revision.

    This is the one choke point every Arena staging path goes through, so it also
    settles what becoming interactive means for the problem's samples: its public
    test cases turn secret (an interactive problem shows sample interactions
    instead), and any interactions hidden by an earlier validator removal
    resurface.

    A problem may legitimately have no test cases here — Arena creation stages a
    validator on a brand-new disabled draft — so the "at least one secret case"
    rule is enforced at enablement and submission, not on this write.

    Args:
        session: Open Arena session; the candidate is staged but not committed.
        problem: Persisted problem receiving the validator.
        upload: Result of :func:`parse_validator_upload`.

    Returns:
        The validation job to enqueue after the caller commits.

    Raises:
        ValueError: If a validator is already configured.
    """
    validator = problem.custom_validator
    if status_view(validator).configured:
        raise ValueError("Remove the current validator before uploading a different one.")

    if validator is None:
        validator = ArenaProblemCustomValidator(problem_id=problem.id)
        session.add(validator)
    token = stage_candidate(validator, language_id=upload.language_id, source=upload.source)

    await admin_problem_interaction_service.convert_sample_testcases_to_secret(session, problem.id)
    await admin_problem_interaction_service.unhide_interactions(session, problem.id)

    return build_validation_job(domain="arena", problem_id=problem.id, candidate_token=token)


async def stage_validator_source(
    session: AsyncSession,
    problem: ArenaProblem,
    *,
    language_id: str,
    source_file: UploadFile | None,
) -> CustomValidatorValidationJob | None:
    """Parse and stage a validator revision on an existing problem in one step."""
    upload = await parse_validator_upload(session, language_id=language_id, source_file=source_file)
    if upload is None:
        return None
    return await stage_candidate_revision(session, problem, upload)
