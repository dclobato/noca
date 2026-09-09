#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena problem package import/export.

Every format decision — coercion, defaults, lengths, UTF-8, archive safety,
export field sets — belongs to ``shared.services.problem_package``. What is left
here is exactly what only Arena knows: which categories exist, how to resolve a
statement language, how to build Arena rows, and what to queue afterwards.

Import rules:
  - the problem owner is always set to the importing user (``caller_id``);
  - a non-empty ``author`` field is preserved as free-text authorship; when it
    is missing, the importing owner is treated as the author;
  - ``sample_testcases`` from the package decides which cases are public;
  - categories are matched to existing ones by slug/name; unknown ones are
    dropped with a warning (Arena never auto-creates categories on import).
"""

from __future__ import annotations

import uuid
from base64 import b64decode
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import (
    ArenaCategory,
    ArenaCollection,
    ArenaProblem,
    ArenaProblemCustomValidator,
    ArenaSampleInteraction,
    ArenaTestCase,
)
from arena.routes.admin_problem_common import validator_languages
from arena.services import admin_problem_service
from arena.services.admin_category_service import normalize_slug
from arena.services.statement_language_service import (
    detect_statement_language_async,
    parse_statement_language,
)
from shared.enumerations import ArenaEditorialReleasePolicy, ProblemValidatorType, StatementLanguage
from shared.services.custom_validator import PackagedValidator, current_validator_source, stage_candidate
from shared.services.imageprocessing_service import ImageProcessingService
from shared.services.problem_image import export_image_filename, load_staged_image
from shared.services.problem_package import (
    PackageError,
    PackageWarning,
    ProblemPackage,
    build_package,
)
from shared.services.problem_package.constants import FORMAT_VERSION
from shared.services.problem_package.errors import WARN_UNKNOWN_CATEGORIES, WARN_UNKNOWN_COLLECTION
from shared.services.problem_package.journal import journal_root_for
from shared.services.problem_package.model import (
    PackageImage,
    PackageMetadata,
    PackageStatement,
    PackageTestCase,
)
from shared.services.problem_package.promotion import ArtifactPromoter, commit_with_promotion
from shared.services.problem_package.reconcile import reconcile_import_journals
from shared.services.problem_package.writer import PackageProfile
from shared.services.sample_interactions import PackagedInteraction
from shared.services.testcase_files import get_testcase_path

LanguageSource = Literal["package", "detected", "undetermined"]


@dataclass(frozen=True)
class ArenaProblemImportResult:
    """What an imported package produced, so the route can report it.

    Attributes:
        problem: The newly created, committed problem.
        is_interactive: Whether the imported problem's stored strategy is interactive.
        imported_interaction_count: Sample interactions read from ``interaction/``.
        statement_language: The language stored on the problem, if any.
        language_source: Where that language came from — ``"package"`` when the
            package stated it, ``"detected"`` when detection supplied it, and
            ``"undetermined"`` when neither did.
        warnings: Structured, non-fatal observations for the route to flash.
    """

    problem: ArenaProblem
    is_interactive: bool
    imported_interaction_count: int
    statement_language: StatementLanguage | None
    language_source: LanguageSource
    warnings: tuple[PackageWarning, ...]


async def import_problem_package(
    session: AsyncSession,
    package: ProblemPackage,
    *,
    caller_id: str,
    image_service: ImageProcessingService,
    testcase_dir: Path,
) -> ArenaProblemImportResult:
    """Persist an already-validated package as a new Arena problem.

    Artifacts are promoted before the commit and removed again if it fails, so
    neither orphaned files nor rows pointing at missing files can survive.

    Raises:
        PackageError: When the package cannot become an Arena problem.
        ValueError: On any Arena-side validation failure.
    """
    await reconcile_import_journals(session, domain="arena", testcase_dir=testcase_dir)

    metadata = package.metadata
    if package.statement.text is None:
        raise PackageError("Arena problems require a Markdown statement; this package carries a PDF.")

    warnings = list(package.warnings)
    category_ids = await _resolve_category_ids(session, metadata.categories, warnings)
    collection_id = await _resolve_collection_id(session, metadata.collection, warnings)
    language, language_source = await _resolve_packaged_language(metadata, statement=package.statement.text)
    image_b64, image_mime = load_staged_image(package.image, image_service)

    problem = await admin_problem_service.create_problem(
        session,
        caller_id=caller_id,
        title=metadata.title,
        author=metadata.author,
        author_is_owner=metadata.author is None,
        source=metadata.source,
        hide_author_show_source=metadata.hide_author_show_source,
        time_limit_ms=metadata.time_limit_ms,
        memory_limit_kb=metadata.memory_limit_kb,
        pids_limit=metadata.pids_limit,
        output_limit_in_bytes=metadata.output_limit_in_bytes,
        problem_statement=package.statement.text,
        editorial=package.editorial,
        # An older package carries no policy at all, so it lands on the column
        # default rather than on a guess about what its author intended.
        editorial_release_policy=(metadata.editorial_release_policy or ArenaEditorialReleasePolicy.NEVER),
        image_b64=image_b64,
        image_mime=image_mime,
        image_caption=metadata.image_caption,
        notes=metadata.notes,
        license=metadata.license,
        category_ids=category_ids,
        collection_id=collection_id,
        statement_language=language,
        expected_difficulty=metadata.expected_difficulty,
        # The package's normalized strategy: version 2 states it, version 1 has it
        # derived by the shared parser. Never re-derived from validator presence.
        validator_type=package.metadata.validator_type,
    )

    _add_test_cases(session, problem.id, package)
    if package.validator is not None:
        await _require_active_validator_language(session, package.validator.language_id)
        _stage_validator(session, problem.id, package.validator)
    _add_interactions(session, problem.id, package.interactions)

    promoter = ArtifactPromoter(domain="arena", journal_root=journal_root_for(testcase_dir), testcase_dir=testcase_dir)
    await commit_with_promotion(session, promoter, package, problem.id)

    return ArenaProblemImportResult(
        problem=problem,
        is_interactive=problem.validator_type is ProblemValidatorType.INTERACTIVE,
        imported_interaction_count=len(package.interactions),
        statement_language=language,
        language_source=language_source,
        warnings=tuple(warnings),
    )


def export_problem_package(
    problem: ArenaProblem,
    owner_name: str,
    testcase_dir: Path,
    destination: Path,
    *,
    profile: PackageProfile = "full",
) -> Path:
    """Write an Arena problem to ``destination`` as a package ZIP.

    The ``problem.categories``, ``problem.test_cases``, and
    ``problem.sample_interactions`` relationships must be eagerly loaded.

    Raises:
        PackageError: If a stored test-case file is missing, so the export fails
            loudly instead of shipping an empty member that would re-import with
            silently different semantics.
    """
    return build_package(_to_package(problem, owner_name, testcase_dir), destination, profile=profile)


def _to_package(problem: ArenaProblem, owner_name: str, testcase_dir: Path) -> ProblemPackage:
    """Project an Arena problem onto the shared package contract."""
    validator_source = current_validator_source(problem.custom_validator)
    # The export's shape follows the stored strategy, so an interactive problem
    # whose source was removed still exports as interactive rather than silently
    # changing kind.
    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE

    cases: list[PackageTestCase] = []
    for test_case in sorted(problem.test_cases, key=lambda item: item.ordinal):
        cases.append(
            PackageTestCase(
                ordinal=test_case.ordinal,
                is_sample=test_case.is_sample,
                input_path=get_testcase_path(problem.id, test_case.ordinal, "in", testcase_dir),
                output_path=(
                    None if interactive else get_testcase_path(problem.id, test_case.ordinal, "out", testcase_dir)
                ),
                explanation=test_case.explanation,
            )
        )

    # Arena keeps both the statement and the image in the database, so the
    # package carries their content directly rather than a path to a file that
    # does not exist on this side.
    image = None
    if problem.problem_image_base64:
        image = PackageImage(
            member=export_image_filename(problem.problem_image_mime),
            path=None,
            mime=problem.problem_image_mime or "image/png",
            data=b64decode(problem.problem_image_base64),
        )

    metadata = PackageMetadata(
        format_version=FORMAT_VERSION,
        validator_type=problem.validator_type,
        title=problem.title,
        author=owner_name if problem.author_is_owner else problem.author,
        notes=problem.notes,
        source=problem.source,
        license=problem.license,
        # Arena has no balloon colors; the key is still written, as null.
        color=None,
        hide_author_show_source=problem.hide_author_show_source,
        statement_language=(problem.statement_language.value if problem.statement_language else None),
        expected_difficulty=problem.expected_difficulty,
        time_limit_ms=problem.time_limit_ms,
        memory_limit_kb=problem.memory_limit_kb,
        pids_limit=problem.pids_limit,
        output_limit_in_bytes=problem.output_limit_in_bytes,
        categories=tuple(category.name for category in problem.categories),
        collection=problem.collection.slug if problem.collection is not None else None,
        sample_testcases=tuple(case.ordinal for case in cases if case.is_sample),
        image=image.member if image is not None else None,
        image_caption=problem.problem_image_caption,
        # Arena has no per-language overrides; the key is still written, as {}.
        language_limits={},
        custom_validator=None,
        sha256={},
        editorial=None,
        editorial_release_policy=problem.editorial_release_policy,
    )
    interactions = tuple(
        PackagedInteraction(interaction.transcript, interaction.explanation)
        for interaction in sorted(problem.sample_interactions, key=lambda item: item.ordinal)
        if interaction.hidden_at is None
    )
    return ProblemPackage(
        metadata=metadata,
        statement=PackageStatement(kind="md", path=None, text=problem.problem_statement or ""),
        test_cases=tuple(cases),
        image=image,
        # Gated on the stored strategy, not merely on a row existing: a standard
        # problem carrying a stale validator row would otherwise export a
        # 'standard' declaration alongside validator/ members, which is a version-2
        # package this build's own reader refuses.
        validator=(
            PackagedValidator(validator_source.language_id, validator_source.source)
            if interactive and validator_source is not None
            else None
        ),
        interactions=interactions if interactive else (),
        warnings=(),
        editorial=problem.editorial,
    )


def _add_test_cases(session: AsyncSession, problem_id: str, package: ProblemPackage) -> None:
    """Add the package's test-case rows; files are promoted separately."""
    now = datetime.now(UTC)
    for case in package.test_cases:
        session.add(
            ArenaTestCase(
                id=str(uuid.uuid4()),
                problem_id=problem_id,
                ordinal=case.ordinal,
                is_sample=case.is_sample,
                input_size_bytes=case.input_path.stat().st_size,
                output_size_bytes=(case.output_path.stat().st_size if case.output_path is not None else None),
                explanation=case.explanation,
                created_at=now,
                updated_at=now,
            )
        )


async def _require_active_validator_language(session: AsyncSession, language_id: str) -> None:
    """Refuse a package whose validator language this Arena does not run.

    The reader validates the source's syntax and safety; whether the language is
    actually available is a target-specific question only the importing side can
    answer, so it is asked here — before anything is committed.
    """
    active_ids = {row.id for row in await validator_languages(session)}
    if language_id not in active_ids:
        raise PackageError(f"The package's custom validator language {language_id!r} is not active on this platform.")


def _stage_validator(session: AsyncSession, problem_id: str, packaged: PackagedValidator) -> None:
    """Stage the package's validator as a pending candidate revision."""
    validator = ArenaProblemCustomValidator(problem_id=problem_id)
    stage_candidate(validator, language_id=packaged.language_id, source=packaged.source)
    session.add(validator)


def _add_interactions(session: AsyncSession, problem_id: str, interactions: tuple[PackagedInteraction, ...]) -> None:
    """Add the package's sample-interaction rows."""
    now = datetime.now(UTC)
    for ordinal, packaged in enumerate(interactions, start=1):
        session.add(
            ArenaSampleInteraction(
                id=str(uuid.uuid4()),
                problem_id=problem_id,
                ordinal=ordinal,
                transcript=packaged.transcript,
                explanation=packaged.explanation,
                created_at=now,
                updated_at=now,
            )
        )


async def _resolve_packaged_language(
    metadata: PackageMetadata,
    *,
    statement: str,
) -> tuple[StatementLanguage | None, LanguageSource]:
    """Resolve the statement language, detecting it when the package stated none.

    A stated language is authoritative; otherwise the statement is auto-detected
    so the importer only has to verify the result. Detection may still come up
    empty, which is its own reportable outcome.

    Raises:
        PackageError: When the stated language is not one Arena supports.
    """
    try:
        stated = parse_statement_language(metadata.statement_language)
    except ValueError as exc:
        raise PackageError(f"problem.json: 'statement_language' is invalid. {exc}") from exc
    if stated is not None:
        return stated, "package"
    detected = await detect_statement_language_async(statement, title=metadata.title)
    return detected, ("detected" if detected is not None else "undetermined")


async def _resolve_category_ids(
    session: AsyncSession,
    names: tuple[str, ...],
    warnings: list[PackageWarning],
) -> list[str]:
    """Resolve category names to existing IDs, reporting the ones dropped."""
    if not names:
        return []
    slugs = {normalize_slug(name) for name in names}
    lowered = {name.lower() for name in names}
    result = await session.execute(
        select(ArenaCategory.id, ArenaCategory.slug, func.lower(ArenaCategory.name).label("lowered")).where(
            or_(ArenaCategory.slug.in_(slugs), func.lower(ArenaCategory.name).in_(lowered))
        )
    )
    rows = result.all()
    matched = {row.slug for row in rows} | {row.lowered for row in rows}
    dropped = sorted(name for name in names if normalize_slug(name) not in matched and name.lower() not in matched)
    if dropped:
        warnings.append(
            PackageWarning(
                WARN_UNKNOWN_CATEGORIES,
                f"Categories not defined in this Arena were dropped: {', '.join(dropped)}.",
            )
        )
    return [row.id for row in rows]


async def _resolve_collection_id(
    session: AsyncSession,
    collection: str | None,
    warnings: list[PackageWarning],
) -> str | None:
    """Resolve a package's collection slug to an existing ID.

    Matches by slug or by lowercased name, exactly as categories do, and never
    creates a collection: an import must not invent a taxonomy entry the Arena
    admin has not defined. An unresolved value leaves the problem unfiled and is
    reported, rather than failing the whole import.

    Args:
        session: Active async database session.
        collection: The package's ``collection`` value, or ``None`` when absent
            (which is every package written before format version 4).
        warnings: Accumulator the dropped value is reported through.

    Returns:
        str | None: The matching collection ID, or ``None`` when unset or unknown.
    """
    if collection is None or not collection.strip():
        return None
    wanted = collection.strip()
    result = await session.execute(
        select(ArenaCollection.id).where(
            or_(ArenaCollection.slug == normalize_slug(wanted), func.lower(ArenaCollection.name) == wanted.lower())
        )
    )
    resolved = result.scalars().first()
    if resolved is None:
        warnings.append(
            PackageWarning(
                WARN_UNKNOWN_COLLECTION,
                f"Collection not defined in this Arena was dropped: {wanted}.",
            )
        )
        return None
    return str(resolved)
