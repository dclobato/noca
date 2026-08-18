#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Archive safety scan and member classification, run before any extraction.

Nothing is written to disk until this pass has accepted the whole archive: an
unsafe member must never reach the filesystem, and a package that would silently
lose a test case must be refused rather than half-imported.
"""

from __future__ import annotations

import stat
import unicodedata
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum

from shared.services.custom_validator import VALIDATOR_PACKAGE_DIR
from shared.services.problem_image import EXT_TO_MIME
from shared.services.problem_package.constants import (
    EDITORIAL_MD_MEMBER,
    INTERACTION_EXPLAIN_RE,
    INTERACTION_RE,
    MACOS_JUNK_NAMES,
    MACOS_JUNK_PREFIXES,
    MAX_ARCHIVE_MEMBERS,
    MAX_MEMBER_UNCOMPRESSED_BYTES,
    MAX_TEST_CASE_ORDINAL,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    MIN_TEST_CASE_ORDINAL,
    PROBLEM_JSON_MEMBER,
    STATEMENT_MD_MEMBER,
    STATEMENT_PDF_MEMBER,
)
from shared.services.problem_package.errors import WARN_MACOS_METADATA, PackageError, PackageWarning
from shared.services.problem_package.testcase_archive import classify_testcase_member, index_testcase_members

_ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_ENCRYPTED_FLAG = 0x1


class MemberKind(StrEnum):
    """What a recognized archive member is."""

    PROBLEM_JSON = "problem_json"
    STATEMENT_MD = "statement_md"
    STATEMENT_PDF = "statement_pdf"
    EDITORIAL = "editorial"
    TESTCASE_INPUT = "testcase_input"
    TESTCASE_OUTPUT = "testcase_output"
    EXPLANATION = "explanation"
    INTERACTION = "interaction"
    INTERACTION_EXPLAIN = "interaction_explain"
    VALIDATOR = "validator"
    IMAGE = "image"


@dataclass(frozen=True, slots=True)
class RecognizedMember:
    """One archive member the reader will extract."""

    name: str
    kind: MemberKind
    ordinal: int | None
    size: int


@dataclass(slots=True)
class ArchivePlan:
    """The accepted, classified contents of an archive."""

    members: list[RecognizedMember] = field(default_factory=list)
    warnings: list[PackageWarning] = field(default_factory=list)

    def by_kind(self, kind: MemberKind) -> list[RecognizedMember]:
        """Return every recognized member of one kind, in archive order."""
        return [member for member in self.members if member.kind is kind]

    def first(self, kind: MemberKind) -> RecognizedMember | None:
        """Return the single member of one kind, or ``None``."""
        found = self.by_kind(kind)
        return found[0] if found else None


def scan_archive(archive: zipfile.ZipFile) -> ArchivePlan:
    """Validate archive safety and classify its members.

    Raises:
        PackageError: On any unsafe entry, collision, ceiling breach, or layout
            the format cannot represent without losing data.
    """
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise PackageError(f"Archive holds {len(infos)} members; the limit is {MAX_ARCHIVE_MEMBERS}.")

    plan = ArchivePlan()
    seen_exact: set[str] = set()
    seen_normalized: set[str] = set()
    total_uncompressed = 0
    dropped_junk = False
    logical_members: dict[tuple[MemberKind, int], str] = {}

    for info in infos:
        name = info.filename
        if _is_junk(name):
            dropped_junk = True
            continue
        if info.is_dir():
            continue

        _check_entry_safety(info)
        if name in seen_exact:
            raise PackageError(f"Archive contains duplicate member {name!r}.")
        seen_exact.add(name)
        normalized = unicodedata.normalize("NFC", name).casefold()
        if normalized in seen_normalized:
            raise PackageError(f"Archive contains members that collide case- or Unicode-insensitively: {name!r}.")
        seen_normalized.add(normalized)

        if info.file_size > MAX_MEMBER_UNCOMPRESSED_BYTES:
            raise PackageError(
                f"Member {name!r} expands to {info.file_size} bytes; the per-member limit is "
                f"{MAX_MEMBER_UNCOMPRESSED_BYTES}."
            )
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise PackageError(
                f"Archive expands to more than {MAX_TOTAL_UNCOMPRESSED_BYTES} bytes in total; refusing to extract it."
            )

        try:
            classified = _classify(name, info.file_size)
        except ValueError as exc:
            raise PackageError(str(exc)) from exc
        if classified is None:
            # Unrecognized but safe: ignored, so a newer producer's extra members
            # do not make a package unreadable here.
            continue
        if classified.ordinal is not None:
            _record_logical_member(logical_members, classified, name)
        plan.members.append(classified)

    try:
        index_testcase_members(member.name for member in plan.members)
    except ValueError as exc:
        raise PackageError(str(exc)) from exc
    if dropped_junk:
        plan.warnings.append(
            PackageWarning(WARN_MACOS_METADATA, "macOS metadata entries (__MACOSX/, .DS_Store) were ignored.")
        )
    return plan


def _record_logical_member(
    logical_members: dict[tuple[MemberKind, int], str],
    member: RecognizedMember,
    name: str,
) -> None:
    """Reject a second member claiming the same kind and logical ordinal.

    ``out/001.out`` and ``out/001.sol`` name the same stream, as do ``001.in``
    and ``in/001.in``. Keeping the last silently, as the previous parser did,
    means the package the author shipped is not the package that got imported.
    """
    ordinal = member.ordinal or 0
    previous = logical_members.get((member.kind, ordinal))
    if previous is not None:
        if member.kind in (MemberKind.TESTCASE_INPUT, MemberKind.TESTCASE_OUTPUT):
            side = "in" if member.kind is MemberKind.TESTCASE_INPUT else "out"
            raise PackageError(
                f"Members {previous!r} and {name!r} both provide the {side} stream of test case {ordinal}."
            )
        label = member.kind.value.replace("_", " ")
        raise PackageError(f"Members {previous!r} and {name!r} both provide {label} ordinal {ordinal}.")
    logical_members[(member.kind, ordinal)] = name


def _check_entry_safety(info: zipfile.ZipInfo) -> None:
    """Reject an entry that must never be extracted.

    Raises:
        PackageError: On traversal, an absolute path, a backslash, duplicate
            separators, a symlink, encryption, or unsupported compression.
    """
    name = info.filename
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise PackageError(f"Archive member {name!r} uses an absolute path.")
    if "\\" in name:
        raise PackageError(f"Archive member {name!r} uses a backslash path separator.")
    if "//" in name:
        raise PackageError(f"Archive member {name!r} uses duplicate path separators.")
    if ".." in name.split("/"):
        raise PackageError(f"Archive member {name!r} escapes the archive root.")
    if info.flag_bits & _ENCRYPTED_FLAG:
        raise PackageError(f"Archive member {name!r} is encrypted; encrypted packages are not supported.")
    if info.compress_type not in _ALLOWED_COMPRESSION:
        raise PackageError(f"Archive member {name!r} uses an unsupported compression method.")
    if info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16):
        raise PackageError(f"Archive member {name!r} is a symbolic link.")


def _classify(name: str, size: int) -> RecognizedMember | None:
    """Return the recognized member for ``name``, or ``None`` when unknown."""
    if name == PROBLEM_JSON_MEMBER:
        return RecognizedMember(name, MemberKind.PROBLEM_JSON, None, size)
    if name == STATEMENT_MD_MEMBER:
        return RecognizedMember(name, MemberKind.STATEMENT_MD, None, size)
    if name == STATEMENT_PDF_MEMBER:
        return RecognizedMember(name, MemberKind.STATEMENT_PDF, None, size)
    if name == EDITORIAL_MD_MEMBER:
        return RecognizedMember(name, MemberKind.EDITORIAL, None, size)

    testcase = classify_testcase_member(name)
    if testcase is not None:
        kind = {
            "input": MemberKind.TESTCASE_INPUT,
            "output": MemberKind.TESTCASE_OUTPUT,
            "explanation": MemberKind.EXPLANATION,
        }[testcase.stream]
        return RecognizedMember(name, kind, testcase.ordinal, size)

    for pattern, kind in (
        (INTERACTION_RE, MemberKind.INTERACTION),
        (INTERACTION_EXPLAIN_RE, MemberKind.INTERACTION_EXPLAIN),
    ):
        match = pattern.match(name)
        if match:
            return RecognizedMember(name, kind, _ordinal(match.group(1), name), size)

    if name.startswith(f"{VALIDATOR_PACKAGE_DIR}/") and "/" not in name[len(VALIDATOR_PACKAGE_DIR) + 1 :]:
        return RecognizedMember(name, MemberKind.VALIDATOR, None, size)
    if "/" not in name and name.rsplit(".", 1)[-1].lower() in EXT_TO_MIME and "." in name:
        return RecognizedMember(name, MemberKind.IMAGE, None, size)
    return None


def _ordinal(raw: str, name: str) -> int:
    """Parse and range-check an ordinal read from a member name.

    The pattern accepts up to four digits so a package holding ordinals 1 and
    5000 fails loudly here rather than quietly importing only the two the old
    three-digit pattern happened to match.
    """
    value = int(raw)
    if not MIN_TEST_CASE_ORDINAL <= value <= MAX_TEST_CASE_ORDINAL:
        raise PackageError(
            f"Archive member {name!r} uses ordinal {value}, outside the supported range "
            f"{MIN_TEST_CASE_ORDINAL}..{MAX_TEST_CASE_ORDINAL}."
        )
    return value


def _is_junk(name: str) -> bool:
    """Return whether a member is macOS packaging metadata."""
    return name.startswith(MACOS_JUNK_PREFIXES) or name.rsplit("/", 1)[-1] in MACOS_JUNK_NAMES
