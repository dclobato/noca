#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest problem package import.

Every format decision belongs to ``shared.services.problem_package``. What is
left here is what only the Contest domain knows: balloon colors, contest-scoped
language availability, ORM row construction, and the validator token the route
enqueues after commit.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.profiling_limits import ceil_div
from shared.services.custom_validator import stage_candidate
from shared.services.imageprocessing_service import ImageProcessingService
from shared.services.problem_image import load_staged_image
from shared.services.problem_package import PackageError, PackageWarning, ProblemPackage
from shared.services.problem_package.constants import PER_RUN_TIME_LIMIT_VERSION
from shared.services.problem_package.errors import WARN_DISALLOWED_LANGUAGE_LIMITS
from shared.services.problem_package.journal import journal_root_for
from shared.services.problem_package.model import PackageLanguageLimit
from shared.services.problem_package.promotion import ArtifactPromoter, commit_with_promotion
from shared.services.problem_package.reconcile import reconcile_import_journals
from web.models.contest import Contest
from web.models.problem import Problem, ProblemCustomValidator, ProblemSampleInteraction, ProblemTestCase
from web.services.category_service import get_or_create_categories, replace_problem_categories

from .language_limits import upsert_language_limits
from .models import LanguageLimitInput, ProblemImportResult
from .ordering import append_problem, append_test_case
from .queries import get_active_languages, get_contest_languages

BALLOON_COLORS: tuple[str, ...] = (
    "#FF0000",
    "#800000",
    "#FFA500",
    "#FFD700",
    "#FFFF00",
    "#808000",
    "#00FF00",
    "#008000",
    "#00FFFF",
    "#008080",
    "#0000FF",
    "#000080",
    "#FF00FF",
    "#800080",
    "#FFFFFF",
    "#C0C0C0",
    "#808080",
    "#000000",
)


def _pick_balloon_color(used: set[str]) -> str:
    """Pick a random predefined balloon color, preferring unused ones."""
    normalised_used = {c.upper() for c in used}
    available = [c for c in BALLOON_COLORS if c.upper() not in normalised_used]
    return random.choice(available if available else list(BALLOON_COLORS))


async def import_problem_package(
    session: AsyncSession,
    contest: Contest,
    package: ProblemPackage,
    testcase_dir: Path,
    statement_dir: Path,
    image_service: ImageProcessingService,
) -> ProblemImportResult:
    """Persist an already-validated package as a new contest problem.

    Statement and test-case artifacts are promoted before the commit and removed
    again if it fails, so neither orphaned files nor rows pointing at missing
    files can survive.

    Args:
        session: Active async database session.
        contest: Contest that receives the imported problem.
        package: The validated package, with payloads staged on disk.
        testcase_dir: Contest test-case root.
        statement_dir: Contest statement root.
        image_service: Service used to validate the packaged image, if present.

    Returns:
        ProblemImportResult: The imported problem, its staged validator token,
        and the structured warnings the route flashes.

    Raises:
        ValueError: On any Contest-side validation failure.
    """
    await reconcile_import_journals(session, domain="contest", testcase_dir=testcase_dir, statement_dir=statement_dir)

    metadata = package.metadata
    warnings = list(package.warnings)
    image_b64, image_mime = load_staged_image(package.image, image_service)

    # A package's own color is preserved when it has one; otherwise the contest
    # picks an unused balloon color, since Arena packages carry none.
    color = metadata.color
    if color is None:
        used_colors = set(await session.scalars(select(Problem.color).where(Problem.contest_id == contest.id)))
        color = _pick_balloon_color(used_colors)

    problem = Problem(
        title=metadata.title,
        # The package's normalized strategy: version 2 states it, version 1 has it
        # derived by the shared parser. Never re-derived from validator presence.
        validator_type=package.metadata.validator_type,
        author=metadata.author,
        notes=metadata.notes,
        editorial=package.editorial,
        color=color,
        time_limit_ms=metadata.time_limit_ms,
        memory_limit_kb=metadata.memory_limit_kb,
        pids_limit=metadata.pids_limit,
        output_limit_in_bytes=metadata.output_limit_in_bytes,
        problem_image_base64=image_b64,
        problem_image_mime=image_mime,
        problem_image_caption=metadata.image_caption,
    )
    await append_problem(session, contest, problem)

    for case in package.test_cases:
        test_case = ProblemTestCase(
            is_sample=case.is_sample,
            explanation=case.explanation,
            input_size_bytes=case.input_path.stat().st_size,
            output_size_bytes=(case.output_path.stat().st_size if case.output_path is not None else None),
        )
        await append_test_case(session, problem, test_case)

    if metadata.categories:
        categories = await get_or_create_categories(session, list(metadata.categories))
        await replace_problem_categories(session, problem, categories)

    await _apply_language_limits(session, contest, problem, package, warnings)

    validator_candidate_token: str | None = None
    if package.validator is not None:
        # Whether the validator's language is available is a target-specific
        # question the shared reader cannot answer; ask it before committing.
        active_ids = {language.id for language in await get_active_languages(session)}
        if package.validator.language_id not in active_ids:
            raise PackageError(
                f"The package's custom validator language {package.validator.language_id!r} is not active."
            )
        validator = ProblemCustomValidator(problem_id=problem.id)
        validator_candidate_token = stage_candidate(
            validator,
            language_id=package.validator.language_id,
            source=package.validator.source,
        )
        session.add(validator)

    for ordinal, packaged in enumerate(package.interactions, start=1):
        session.add(
            ProblemSampleInteraction(
                problem_id=problem.id,
                ordinal=ordinal,
                transcript=packaged.transcript,
                explanation=packaged.explanation,
            )
        )

    promoter = ArtifactPromoter(
        domain="contest",
        journal_root=journal_root_for(testcase_dir),
        testcase_dir=testcase_dir,
        statement_dir=statement_dir,
    )
    await commit_with_promotion(session, promoter, package, problem.id)

    return ProblemImportResult(
        problem=problem,
        validator_candidate_token=validator_candidate_token,
        imported_interaction_count=len(package.interactions),
        warnings=tuple(warnings),
    )


def _resolve_package_repetitions(
    database_defaults: Mapping[str, int],
    declared: Mapping[str, PackageLanguageLimit],
) -> dict[str, int]:
    """Resolve the repetition count each declared language will actually store.

    An omitted count resolves from the target database's language row, because
    that is the contest's authoritative configuration. A legacy package's time
    limit has to be divided by the very number stored beside it, not by a
    built-in registry value that may differ from the deployed database.
    """
    resolved: dict[str, int] = {}
    for language_id, limit in declared.items():
        if limit.repetitions is not None:
            resolved[language_id] = limit.repetitions
            continue
        resolved[language_id] = database_defaults.get(language_id, 1)
    return resolved


async def _apply_language_limits(
    session: AsyncSession,
    contest: Contest,
    problem: Problem,
    package: ProblemPackage,
    warnings: list[PackageWarning],
) -> None:
    """Apply the package's per-language limits the contest actually allows.

    Below format version 3 a package's ``time_limit_ms`` is the budget shared by
    all repetitions of a test case rather than the limit for one of them, so it
    is divided here. This is the layer that can do it: an omitted ``repetitions``
    means "whatever the importing side defaults to", a value the package itself
    never knew, so the parser cannot convert and importing a 1000 ms limit into
    a ten-repetition language without dividing would quietly grant a 10,000 ms
    budget. The division rounds up, matching the schema migration, so an import
    is never stricter than the package it came from.
    """
    declared = package.metadata.language_limits
    if not declared:
        return
    legacy_totals = package.metadata.format_version < PER_RUN_TIME_LIMIT_VERSION
    contest_languages = await get_contest_languages(session, contest)
    database_defaults = {language.id: language.profiling_repetitions_default for language in contest_languages}
    resolved_repetitions = _resolve_package_repetitions(database_defaults, declared)
    contest_language_ids = set(database_defaults)
    allowed: dict[str, LanguageLimitInput] = {}
    skipped: list[str] = []
    for language_id, limit in declared.items():
        if language_id not in contest_language_ids:
            skipped.append(language_id)
            continue
        if legacy_totals:
            repetitions = resolved_repetitions[language_id]
            time_limit_ms: int | None = ceil_div(limit.time_limit_ms, repetitions)
            stored_repetitions: int | None = repetitions
        else:
            time_limit_ms = limit.time_limit_ms
            stored_repetitions = resolved_repetitions[language_id]
        allowed[language_id] = {
            "time_limit_ms": time_limit_ms,
            "memory_limit_kb": limit.memory_limit_kb,
            "pids_limit": limit.pids_limit,
            "output_limit_in_bytes": limit.output_limit_in_bytes,
            "repetitions": stored_repetitions,
        }
    if skipped:
        warnings.append(
            PackageWarning(
                WARN_DISALLOWED_LANGUAGE_LIMITS,
                "Per-language limits were skipped for languages this contest does not allow: "
                f"{', '.join(sorted(skipped))}.",
            )
        )
    if allowed:
        await upsert_language_limits(session, problem, allowed)
