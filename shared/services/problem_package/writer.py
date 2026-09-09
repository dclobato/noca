#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Write a :class:`ProblemPackage` to a ZIP file on disk.

Exports never build an in-memory buffer: the archive is written to an owned
temporary path and the route streams it with a ``FileResponse``, the same
no-RAM rule imports follow.

Two profiles exist. ``full`` is an importable package carrying **every**
current-version key, including keys the exporting domain cannot store — written as
``null`` / ``false`` / ``{}`` rather than omitted, so a round trip through the
other domain is describable. ``public`` is a contestant-facing statement bundle
and deliberately **not** importable: no ``problem.json``, no secret cases, no
limits, no notes, no validator source — and therefore no strategy metadata —
or editorial, which remains editor-only.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Literal

from shared.enumerations import ProblemValidatorType
from shared.profiling_limits import ceil_div
from shared.services.custom_validator import packaged_validator_member
from shared.services.problem_package.constants import (
    EDITORIAL_MD_MEMBER,
    FORMAT_VERSION,
    PER_RUN_TIME_LIMIT_VERSION,
    PROBLEM_JSON_MEMBER,
    STATEMENT_MD_MEMBER,
    STATEMENT_PDF_MEMBER,
)
from shared.services.problem_package.errors import PackageError
from shared.services.problem_package.model import PackageLanguageLimit, ProblemPackage
from shared.services.sample_interactions import build_interaction_files

PackageProfile = Literal["full", "public"]

_COPY_CHUNK_BYTES = 1024 * 1024


def build_package(
    package: ProblemPackage,
    destination: Path,
    *,
    profile: PackageProfile,
    require_importable: bool = True,
) -> Path:
    """Write ``package`` to ``destination`` and return that path.

    Args:
        package: The problem projected onto the package contract.
        destination: Path to write the archive to.
        profile: ``"full"`` for an importable package, ``"public"`` for the
            contestant-facing statement bundle.
        require_importable: Whether a ``full`` package must satisfy the current
            rules that make it re-importable. Only the contest backup exporter
            and the public problem-set exporter pass ``False``; see
            :func:`_check_importable`.

    Raises:
        PackageError: If a required stored file is missing, or if an importable
            ``full`` package cannot be expressed in the current format. Both previous
            exporters wrote ``b""`` for a missing file, producing a package that
            re-imports with silently different semantics; the routes turn this
            into an actionable 409 instead.
    """
    if profile == "full" and require_importable:
        _check_importable(package)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_statement(archive, package, digests)
        _write_image(archive, package, digests)
        _write_test_cases(archive, package, digests, profile=profile)
        _write_interactions(archive, package, digests)
        if profile == "full":
            editorial_digest = _write_editorial(archive, package)
            _write_validator(archive, package, digests)
            archive.writestr(
                PROBLEM_JSON_MEMBER,
                json.dumps(_problem_json(package, digests, editorial_digest=editorial_digest), indent=2),
            )
    return destination


def _check_importable(package: ProblemPackage) -> None:
    """Refuse a full export the current format cannot express as importable.

    The current format requires an ``interactive`` problem to declare a validator source,
    so an incomplete interactive draft has no valid representation: writing one
    anyway would produce an archive this build's own reader rejects. ``checker``
    has no representation at all in this build.

    Raises:
        PackageError: If the package cannot be written as an importable current package.
    """
    strategy = package.metadata.validator_type
    if strategy is ProblemValidatorType.OUTPUT_CHECKER:
        raise PackageError("Output checker validation is not available in this build.")
    if strategy is ProblemValidatorType.INTERACTIVE and package.validator is None:
        raise PackageError(
            "Cannot export: this interactive problem has no validator source, so it cannot be "
            "written as an importable package. Upload a validator source and export again."
        )


def _write_statement(archive: zipfile.ZipFile, package: ProblemPackage, digests: dict[str, str]) -> None:
    """Write the statement member, failing when its stored file is gone."""
    statement = package.statement
    member = STATEMENT_MD_MEMBER if statement.kind == "md" else STATEMENT_PDF_MEMBER
    if statement.path is None:
        if statement.text is None:
            raise PackageError("Cannot export: the problem has no statement.")
        _write_bytes(archive, member, statement.text.encode("utf-8"), digests)
        return
    _write_file(archive, member, statement.path, digests, what="statement")


def _write_editorial(archive: zipfile.ZipFile, package: ProblemPackage) -> str | None:
    """Write the optional editorial and return its independent digest."""
    if package.editorial is None:
        return None
    editorial_digests: dict[str, str] = {}
    _write_bytes(
        archive,
        EDITORIAL_MD_MEMBER,
        package.editorial.encode("utf-8"),
        editorial_digests,
    )
    return editorial_digests[EDITORIAL_MD_MEMBER]


def _write_image(archive: zipfile.ZipFile, package: ProblemPackage, digests: dict[str, str]) -> None:
    """Write the illustration image, which ships in both profiles.

    The image is part of the statement a contestant reads, not privileged data.
    """
    image = package.image
    if image is None:
        return
    if image.path is None:
        if image.data is None:
            raise PackageError("Cannot export: the problem image has no content.")
        _write_bytes(archive, image.member, image.data, digests)
        return
    _write_file(archive, image.member, image.path, digests, what="problem image")


def _write_test_cases(
    archive: zipfile.ZipFile,
    package: ProblemPackage,
    digests: dict[str, str],
    *,
    profile: PackageProfile,
) -> None:
    """Write test-case members, restricted to public cases for a public bundle."""
    for case in package.test_cases:
        if profile == "public" and not case.is_sample:
            continue
        _write_file(
            archive,
            f"in/{case.ordinal:03d}.in",
            case.input_path,
            digests,
            what=f"test case {case.ordinal} input",
        )
        # An interactive problem's cases have no expected output at all, so the
        # package ships inputs only rather than a misleading empty .out.
        if case.output_path is not None:
            _write_file(
                archive,
                f"out/{case.ordinal:03d}.out",
                case.output_path,
                digests,
                what=f"test case {case.ordinal} output",
            )
        if case.explanation:
            _write_bytes(archive, f"explanation/{case.ordinal:03d}.txt", case.explanation.encode("utf-8"), digests)


def _write_interactions(archive: zipfile.ZipFile, package: ProblemPackage, digests: dict[str, str]) -> None:
    """Write the sample interactions, which are an interactive problem's examples."""
    if not package.interactions:
        return
    pairs = [(item.transcript, item.explanation) for item in package.interactions]
    for name, content in build_interaction_files(pairs):
        _write_bytes(archive, name, content, digests)


def _write_validator(archive: zipfile.ZipFile, package: ProblemPackage, digests: dict[str, str]) -> None:
    """Write the validator source; a public bundle never carries it."""
    if package.validator is None:
        return
    member = packaged_validator_member(package.validator.language_id)
    _write_bytes(archive, member, package.validator.source.encode("utf-8"), digests)


def _problem_json(
    package: ProblemPackage,
    digests: dict[str, str],
    *,
    editorial_digest: str | None,
) -> dict[str, Any]:
    """Build the complete current-version metadata object.

    Every key is present. A domain that cannot store a field writes its empty
    value rather than omitting the key, so a consumer never has to guess whether
    absence means "unset" or "unsupported by the producer".

    ``checker_semantics`` is deliberately absent: it belongs to the output-checker
    strategy, which this build reserves but does not implement.
    """
    metadata = package.metadata
    return {
        "format_version": FORMAT_VERSION,
        "validator_type": metadata.validator_type.value,
        # The release policy is nested here because it only means anything when
        # an editorial exists. A domain with no such column writes it as null,
        # like every other key it cannot store.
        "editorial": (
            {
                "member": EDITORIAL_MD_MEMBER,
                "sha256": editorial_digest,
                "release_policy": (
                    metadata.editorial_release_policy.value if metadata.editorial_release_policy is not None else None
                ),
            }
            if editorial_digest is not None
            else None
        ),
        "title": metadata.title,
        "author": metadata.author,
        "notes": metadata.notes,
        "source": metadata.source,
        "license": metadata.license,
        "color": metadata.color,
        "hide_author_show_source": metadata.hide_author_show_source,
        "statement_language": metadata.statement_language,
        "expected_difficulty": metadata.expected_difficulty,
        "time_limit_ms": metadata.time_limit_ms,
        "memory_limit_kb": metadata.memory_limit_kb,
        "pids_limit": metadata.pids_limit,
        "output_limit_in_bytes": metadata.output_limit_in_bytes,
        "categories": list(metadata.categories),
        "collection": metadata.collection,
        "sample_testcases": [case.ordinal for case in package.test_cases if case.is_sample],
        "image": package.image.member if package.image is not None else None,
        "image_caption": metadata.image_caption,
        "language_limits": {
            language_id: {
                "time_limit_ms": _current_time_limit_ms(
                    source_version=metadata.format_version,
                    language_id=language_id,
                    limit=limit,
                ),
                "memory_limit_kb": limit.memory_limit_kb,
                "pids_limit": limit.pids_limit,
                "output_limit_in_bytes": limit.output_limit_in_bytes,
                "repetitions": limit.repetitions,
            }
            for language_id, limit in metadata.language_limits.items()
        },
        "custom_validator": (
            {
                "language_id": package.validator.language_id,
                "source_file": packaged_validator_member(package.validator.language_id),
            }
            if package.validator is not None
            else None
        ),
        # The legacy manifest covers every ordinary payload member except
        # problem.json. Editorial integrity lives in its additive nested object.
        "sha256": dict(sorted(digests.items())),
    }


def _current_time_limit_ms(
    *,
    source_version: int,
    language_id: str,
    limit: PackageLanguageLimit,
) -> int:
    """Return a package limit expressed with the current per-run semantics.

    The reader intentionally preserves legacy totals because an omitted
    repetition count can only be resolved by the importing domain. The writer
    can still upgrade a legacy value when its divisor is explicit; otherwise it
    refuses to label an unresolved total as a current-version per-run limit.

    Args:
        source_version: Version whose semantics ``limit`` currently follows.
        language_id: Language key used in an actionable error message.
        limit: Parsed language limit to normalize.

    Returns:
        The time limit for one repetition.

    Raises:
        PackageError: If a legacy total has no declared repetition count.
    """
    if source_version >= PER_RUN_TIME_LIMIT_VERSION:
        return limit.time_limit_ms
    if limit.repetitions is None:
        raise PackageError(
            "Cannot rewrite this legacy package at the current format version: "
            f"language_limits[{language_id!r}] has no repetition count, so its total time budget "
            "cannot be converted safely. Import it into a contest first, then export it again."
        )
    return ceil_div(limit.time_limit_ms, limit.repetitions)


def _write_file(
    archive: zipfile.ZipFile,
    member: str,
    source: Path,
    digests: dict[str, str],
    *,
    what: str,
) -> None:
    """Stream a stored file into the archive, hashing exactly what is written.

    The copy is chunked for the same reason imports are: a problem's test data
    can be far larger than anything worth holding in memory.
    """
    if not source.is_file():
        raise PackageError(f"Cannot export: the stored {what} is missing ({source}).")
    digest = hashlib.sha256()
    with source.open("rb") as handle, archive.open(member, "w") as sink:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
            sink.write(chunk)
    digests[member] = digest.hexdigest()


def _write_bytes(archive: zipfile.ZipFile, member: str, data: bytes, digests: dict[str, str]) -> None:
    """Write one member and record its digest over the exact bytes stored."""
    archive.writestr(member, data)
    digests[member] = hashlib.sha256(data).hexdigest()
