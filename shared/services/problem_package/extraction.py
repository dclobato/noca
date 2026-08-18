#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Streaming extraction of recognized members, with integrity accounting.

Each member is copied to the staging area in bounded chunks while its size, CRC,
and SHA-256 digest are computed. The digest covers the **exact raw bytes stored
in the ZIP member**, before any newline normalization, so an integrity manifest
can be verified without interpreting what the member means.
"""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from shared.services.problem_package.constants import (
    MAX_MEMBER_UNCOMPRESSED_BYTES,
    PROBLEM_JSON_MEMBER,
)
from shared.services.problem_package.errors import (
    WARN_INTEGRITY_MANIFEST_MISSING,
    PackageError,
    PackageWarning,
)
from shared.services.problem_package.preflight import ArchivePlan, RecognizedMember
from shared.services.problem_package.staging import PackageStagingArea

_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ExtractedMember:
    """One member written to the staging area."""

    member: RecognizedMember
    path: Path
    digest: str
    size: int


def extract_members(
    archive: zipfile.ZipFile,
    plan: ArchivePlan,
    staging: PackageStagingArea,
) -> dict[str, ExtractedMember]:
    """Stream every recognized member to disk, keyed by member name.

    Raises:
        PackageError: If a member's real size exceeds the declared ceiling or its
            CRC does not match, which means the archive is corrupt.
    """
    extracted: dict[str, ExtractedMember] = {}
    for member in plan.members:
        extracted[member.name] = _extract_one(archive, member, staging)
    return extracted


def _extract_one(
    archive: zipfile.ZipFile,
    member: RecognizedMember,
    staging: PackageStagingArea,
) -> ExtractedMember:
    """Copy one member to staging, hashing it as it goes."""
    destination = staging.path_for(member.name)
    digest = hashlib.sha256()
    written = 0
    try:
        # ZipFile.open validates the CRC as the stream is consumed and raises
        # BadZipFile at the end when it does not match.
        with archive.open(member.name) as source, destination.open("wb") as sink:
            while chunk := source.read(_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_MEMBER_UNCOMPRESSED_BYTES:
                    raise PackageError(
                        f"Member {member.name!r} expands beyond its declared size; refusing to extract it."
                    )
                digest.update(chunk)
                sink.write(chunk)
    except zipfile.BadZipFile as exc:
        raise PackageError(f"Member {member.name!r} is corrupt: {exc}") from exc
    return ExtractedMember(member=member, path=destination, digest=digest.hexdigest(), size=written)


def verify_manifest(
    manifest: Mapping[str, str],
    extracted: Mapping[str, ExtractedMember],
    *,
    excluded_members: frozenset[str] = frozenset(),
) -> list[PackageWarning]:
    """Check a package's ``sha256`` map against what was actually extracted.

    The map covers every recognized payload member except ``problem.json`` and
    any explicitly excluded independently verified member. A present map must
    cover exactly that ordinary set and match. A redundant entry for an excluded
    member is tolerated only when it matches the extracted bytes. An absent map
    imports with a warning, since older packages predate the manifest.

    Raises:
        PackageError: If the map is present but incomplete, over-complete, or
            disagrees with the extracted bytes.
    """
    expected_members = {name for name in extracted if name != PROBLEM_JSON_MEMBER and name not in excluded_members}
    if not manifest:
        return [
            PackageWarning(
                WARN_INTEGRITY_MANIFEST_MISSING,
                "The package carries no 'sha256' integrity manifest; its contents could not be verified.",
            )
        ]

    listed = set(manifest)
    if PROBLEM_JSON_MEMBER in listed:
        raise PackageError(
            f"problem.json: 'sha256' must not cover {PROBLEM_JSON_MEMBER!r}, which contains the manifest itself."
        )
    independently_verified = listed & excluded_members
    for name in sorted(independently_verified):
        extracted_member = extracted.get(name)
        if extracted_member is None:
            raise PackageError(
                f"problem.json: 'sha256' covers independently verified member {name!r}, "
                "but the archive member is missing."
            )
        if manifest[name] != extracted_member.digest:
            raise PackageError(
                f"Integrity check failed for {name!r}: top-level 'sha256' says "
                f"{manifest[name]}, contents hash to {extracted_member.digest}."
            )

    ordinary_listed = listed - excluded_members
    missing = sorted(expected_members - ordinary_listed)
    if missing:
        raise PackageError(f"problem.json: 'sha256' does not cover {', '.join(missing)}.")
    extra = sorted(ordinary_listed - expected_members)
    if extra:
        raise PackageError(f"problem.json: 'sha256' lists members the package does not contain: {', '.join(extra)}.")

    for name in sorted(expected_members):
        actual = extracted[name].digest
        if manifest[name] != actual:
            raise PackageError(
                f"Integrity check failed for {name!r}: manifest says {manifest[name]}, contents hash to {actual}."
            )
    return []
