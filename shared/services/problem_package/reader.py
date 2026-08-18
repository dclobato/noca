#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Read a problem package ZIP from disk into the frozen :class:`ProblemPackage`.

The archive is opened **once**, scanned for safety before anything is written,
then streamed into a staging area. What both domain importers are left with is
category resolution, target-language availability, ORM row construction, and
lifecycle queueing — every format decision has already been made here.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from shared.enumerations import ProblemValidatorType
from shared.services.custom_validator import (
    PackagedValidator,
    ValidatorUploadError,
    packaged_validator_member,
    parse_packaged_validator,
)
from shared.services.problem_package.constants import EDITORIAL_MD_MEMBER, LEGACY_FORMAT_VERSION
from shared.services.problem_package.content import (
    decode_test_case,
    image_mime_for,
    normalize_in_place,
    read_explanation,
    read_markdown_editorial,
    read_markdown_statement,
    read_pdf_statement,
)
from shared.services.problem_package.errors import (
    WARN_IGNORED_INTERACTIVE_OUTPUT,
    WARN_INTERACTIONS_DROPPED,
    WARN_ORPHAN_EXPLANATION,
    WARN_UNDECLARED_EDITORIAL,
    WARN_VALIDATOR_EXTENSION_MISMATCH,
    PackageError,
    PackageWarning,
)
from shared.services.problem_package.extraction import ExtractedMember, extract_members, verify_manifest
from shared.services.problem_package.metadata import decode_problem_json, parse_metadata
from shared.services.problem_package.model import (
    PackageImage,
    PackageMetadata,
    PackageStatement,
    PackageTestCase,
    ProblemPackage,
    StagedPackage,
)
from shared.services.problem_package.preflight import ArchivePlan, MemberKind, scan_archive
from shared.services.problem_package.staging import PackageStagingArea
from shared.services.problem_package.testcase_archive import index_testcase_members, paired_testcase_ordinals
from shared.services.sample_interactions import PackagedInteraction, parse_packaged_interactions


def open_problem_package(zip_path: Path, staging_parent: Path | None = None) -> StagedPackage:
    """Read and validate a package, returning it with its live staging area.

    This is **blocking**: it scans, extracts, hashes, decodes, and validates.
    An async caller must run it in a worker thread (see the import routes) and is
    then responsible for calling ``staged.staging.close()``. Prefer
    :func:`read_problem_package` in synchronous code, which owns that cleanup.

    Args:
        zip_path: Path of the spooled upload.
        staging_parent: Directory to create the staging area in.

    Returns:
        StagedPackage: The immutable package and the area holding its payloads.

    Raises:
        PackageError: On any violation of the format contract. The staging area
            is removed before the error propagates.
    """
    staging = PackageStagingArea.create(parent=staging_parent)
    try:
        with _open_archive(zip_path) as archive:
            plan = scan_archive(archive)
            extracted = extract_members(archive, plan, staging)
        return StagedPackage(package=_assemble(plan, extracted), staging=staging)
    except BaseException:
        staging.close()
        raise


@contextmanager
def read_problem_package(zip_path: Path, *, staging_parent: Path | None = None) -> Iterator[StagedPackage]:
    """Read a package and release its staging area when the context ends.

    Leaving the context removes every staged path, so a caller that promotes
    artifacts must do so before returning.

    Yields:
        StagedPackage: The immutable package and the area holding its payloads.

    Raises:
        PackageError: On any violation of the format contract.
    """
    staged = open_problem_package(zip_path, staging_parent)
    try:
        yield staged
    finally:
        staged.staging.close()


@contextmanager
def _open_archive(zip_path: Path) -> Iterator[zipfile.ZipFile]:
    """Open the upload as a ZIP archive exactly once."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageError("Invalid ZIP file.") from exc
    try:
        yield archive
    finally:
        archive.close()


def _assemble(plan: ArchivePlan, extracted: dict[str, ExtractedMember]) -> ProblemPackage:
    """Turn extracted members into a validated package."""
    problem_json = plan.first(MemberKind.PROBLEM_JSON)
    if problem_json is None:
        raise PackageError("problem.json not found in ZIP.")
    metadata = parse_metadata(decode_problem_json(extracted[problem_json.name].path.read_bytes()))

    warnings = list(plan.warnings)
    warnings.extend(
        verify_manifest(
            metadata.sha256,
            extracted,
            excluded_members=frozenset({EDITORIAL_MD_MEMBER}),
        )
    )

    validator, validator_warnings = _read_validator(metadata, extracted)
    warnings.extend(validator_warnings)

    # Every downstream decision reads the normalized strategy, never validator
    # presence: version 2 states it and version 1 has it derived, so the reader
    # has one answer to "what kind of problem is this" in both cases.
    interactive = metadata.validator_type is ProblemValidatorType.INTERACTIVE
    interactions = _read_interactions(plan, extracted, interactive=interactive, warnings=warnings)
    statement = _read_statement(plan, extracted)
    editorial = _read_editorial(plan, extracted, metadata, warnings)
    test_cases, case_warnings = _read_test_cases(plan, extracted, metadata, interactive=interactive)
    warnings.extend(case_warnings)
    image = _read_image(plan, extracted, metadata)

    return ProblemPackage(
        metadata=metadata,
        statement=statement,
        editorial=editorial,
        test_cases=test_cases,
        image=image,
        validator=validator,
        interactions=tuple(interactions),
        warnings=tuple(warnings),
    )


def _read_statement(plan: ArchivePlan, extracted: dict[str, ExtractedMember]) -> PackageStatement:
    """Read whichever statement the package carries, preferring Markdown."""
    markdown = plan.first(MemberKind.STATEMENT_MD)
    if markdown is not None:
        return read_markdown_statement(extracted[markdown.name].path)
    pdf = plan.first(MemberKind.STATEMENT_PDF)
    if pdf is not None:
        return read_pdf_statement(extracted[pdf.name].path)
    raise PackageError("statement.md or statement.pdf is required in the ZIP.")


def _read_editorial(
    plan: ArchivePlan,
    extracted: dict[str, ExtractedMember],
    metadata: PackageMetadata,
    warnings: list[PackageWarning],
) -> str | None:
    """Read and independently verify the declared optional editorial."""
    member = plan.first(MemberKind.EDITORIAL)
    declaration = metadata.editorial
    if declaration is None:
        if member is not None:
            warnings.append(
                PackageWarning(
                    WARN_UNDECLARED_EDITORIAL,
                    "The package carries editorial.md without an 'editorial' declaration in "
                    "problem.json; the undeclared file was ignored.",
                )
            )
        return None
    if member is None:
        raise PackageError("problem.json declares editorial.md, but the member is missing from the ZIP.")
    extracted_member = extracted[member.name]
    if extracted_member.digest != declaration.sha256:
        raise PackageError(
            "Integrity check failed for 'editorial.md': problem.json says "
            f"{declaration.sha256}, contents hash to {extracted_member.digest}."
        )
    editorial = read_markdown_editorial(extracted_member.path)
    return editorial if editorial.strip() else None


def _read_validator(
    metadata: PackageMetadata,
    extracted: dict[str, ExtractedMember],
) -> tuple[PackagedValidator | None, list[PackageWarning]]:
    """Load the declared validator source, if any.

    Validator members that no ``custom_validator`` declaration accounts for are a
    hard error: shipping a validator that silently does not get configured is
    exactly the kind of quiet data loss the format must refuse. The same applies
    to a ``standard`` problem shipping them, which the metadata parser cannot
    see -- only the reader holds the extracted archive index.
    """
    staged_validators = [name for name in extracted if name.startswith("validator/")]
    if metadata.custom_validator is None:
        if staged_validators:
            # A version-2 package states its strategy, so name that as the reason;
            # a version-1 one states nothing, and the missing declaration is all
            # there is to report.
            if metadata.format_version > LEGACY_FORMAT_VERSION:
                raise PackageError(
                    "The package ships validator/ members but problem.json declares "
                    f"'validator_type': {metadata.validator_type.value!r}, which uses no validator."
                )
            raise PackageError("The package ships validator/ members but problem.json declares no 'custom_validator'.")
        return None, []

    spec = metadata.custom_validator
    # Reuse the existing safe-member parser rather than trusting source_file to
    # name any extracted member: without this, "statement.md" could be declared
    # as the validator source.
    try:
        packaged = parse_packaged_validator(
            {"language_id": spec.language_id, "source_file": spec.source_file},
            read_file=lambda name: extracted[name].path.read_bytes(),
            archive_names=set(extracted),
        )
    except ValidatorUploadError as exc:
        raise PackageError(str(exc)) from exc
    if packaged is None:  # pragma: no cover - metadata is not None here
        return None, []

    warnings: list[PackageWarning] = []
    # The language ID is authoritative; a disagreeing extension is worth saying
    # out loud but is not a reason to refuse an otherwise valid package.
    expected = packaged_validator_member(spec.language_id)
    if spec.source_file != expected:
        warnings.append(
            PackageWarning(
                WARN_VALIDATOR_EXTENSION_MISMATCH,
                f"Validator source is {spec.source_file!r} but language {spec.language_id!r} expects {expected!r}.",
            )
        )
    return packaged, warnings


def _read_interactions(
    plan: ArchivePlan,
    extracted: dict[str, ExtractedMember],
    *,
    interactive: bool,
    warnings: list[PackageWarning],
) -> list[PackagedInteraction]:
    """Read ``interaction/`` members, which only mean anything for an interactive problem."""
    names = {
        member.name
        for member in plan.members
        if member.kind in (MemberKind.INTERACTION, MemberKind.INTERACTION_EXPLAIN)
    }
    if not interactive:
        if names:
            warnings.append(
                PackageWarning(
                    WARN_INTERACTIONS_DROPPED,
                    "The problem is not interactive, so its sample interactions were dropped.",
                )
            )
        return []
    try:
        return parse_packaged_interactions(
            archive_names=names,
            read_file=lambda name: extracted[name].path.read_bytes(),
        )
    except ValueError as exc:
        raise PackageError(str(exc)) from exc


def _read_test_cases(
    plan: ArchivePlan,
    extracted: dict[str, ExtractedMember],
    metadata: PackageMetadata,
    *,
    interactive: bool,
) -> tuple[tuple[PackageTestCase, ...], list[PackageWarning]]:
    """Pair, validate, and remap the package's test cases."""
    warnings: list[PackageWarning] = []
    try:
        index = index_testcase_members(member.name for member in plan.members)
        kept = paired_testcase_ordinals(index, require_output=not interactive)
    except ValueError as exc:
        raise PackageError(str(exc)) from exc
    inputs = index.inputs
    outputs = index.outputs
    explanations = index.explanations

    if interactive and outputs:
        warnings.append(
            PackageWarning(
                WARN_IGNORED_INTERACTIVE_OUTPUT,
                "The problem is interactive, so the package's expected-output files were ignored.",
            )
        )

    orphans = sorted(set(explanations) - set(kept))
    if orphans:
        warnings.append(
            PackageWarning(
                WARN_ORPHAN_EXPLANATION,
                f"Explanations for ordinals {orphans} have no matching test case and were ignored.",
            )
        )

    samples = _resolve_samples(metadata, kept, interactive=interactive)
    cases: list[PackageTestCase] = []
    for new_ordinal, source_ordinal in enumerate(kept, start=1):
        input_path = extracted[inputs[source_ordinal]].path
        decode_test_case(input_path, ordinal=new_ordinal, stream="input")
        normalize_in_place(input_path)
        output_path = None
        if not interactive:
            output_path = extracted[outputs[source_ordinal]].path
            decode_test_case(output_path, ordinal=new_ordinal, stream="output")
            normalize_in_place(output_path)
        explanation_name = explanations.get(source_ordinal)
        cases.append(
            PackageTestCase(
                ordinal=new_ordinal,
                is_sample=source_ordinal in samples,
                input_path=input_path,
                output_path=output_path,
                explanation=(
                    read_explanation(extracted[explanation_name].path, new_ordinal)
                    if explanation_name is not None
                    else None
                ),
            )
        )
    return tuple(cases), warnings


def _resolve_samples(metadata: PackageMetadata, kept: list[int], *, interactive: bool) -> set[int]:
    """Validate ``sample_testcases`` against the *source* ordinals it names.

    Validation happens before the contiguous remap, because that is the ordinal
    space the package author wrote. An interactive problem must declare none: it
    presents sample interactions instead, and the zero-public-cases invariant is
    enforced here rather than left to each domain to remember.
    """
    declared = metadata.sample_testcases
    if interactive and declared:
        raise PackageError(
            "problem.json: 'sample_testcases' must be empty for an interactive problem, "
            "which presents sample interactions instead of sample test cases."
        )
    unknown = sorted(set(declared) - set(kept))
    if unknown:
        raise PackageError(
            f"problem.json: 'sample_testcases' names ordinals the package has no test case for: {unknown}."
        )
    return set(declared)


def _read_image(
    plan: ArchivePlan,
    extracted: dict[str, ExtractedMember],
    metadata: PackageMetadata,
) -> PackageImage | None:
    """Resolve the packaged image, preferring the one ``problem.json`` names."""
    available = {member.name for member in plan.by_kind(MemberKind.IMAGE)}
    if metadata.image is not None:
        if metadata.image not in available:
            raise PackageError(f"problem.json references image {metadata.image!r} which is not present in the ZIP.")
        member = metadata.image
    elif available:
        member = sorted(available)[0]
    else:
        return None
    return PackageImage(member=member, path=extracted[member].path, mime=image_mime_for(member))
