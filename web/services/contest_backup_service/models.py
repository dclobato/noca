#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""DTOs and constants for the full contest backup/restore feature."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Backup ZIP format version. Bump on any breaking layout change.
FORMAT_VERSION = 4

#: The original archive version this server still restores.
LEGACY_FORMAT_VERSION = 1

#: The archive format that carries strategy fields but predates editorial.
PREVIOUS_FORMAT_VERSION = 2

#: The archive format that carries the problem editorial but predates the stored
#: announcement flag on clarification rows.
EDITORIAL_FORMAT_VERSION = 3

#: Every archive version this server restores.
SUPPORTED_FORMAT_VERSIONS: tuple[int, ...] = (
    LEGACY_FORMAT_VERSION,
    PREVIOUS_FORMAT_VERSION,
    EDITORIAL_FORMAT_VERSION,
    FORMAT_VERSION,
)

#: Names of the JSON members that must be present in a valid backup archive.
MANIFEST_MEMBER = "manifest.json"
PROBLEMS_MEMBER = "problems.json"
USERS_MEMBER = "users.json"
MEDIA_MEMBER = "media.json"
SUBMISSIONS_MEMBER = "submissions.json"
JUDGMENTS_MEMBER = "judgments.json"
CLARIFICATIONS_MEMBER = "clarifications.json"
TASKS_MEMBER = "tasks.json"

REQUIRED_MEMBERS: tuple[str, ...] = (
    MANIFEST_MEMBER,
    PROBLEMS_MEMBER,
    USERS_MEMBER,
    SUBMISSIONS_MEMBER,
    JUDGMENTS_MEMBER,
    CLARIFICATIONS_MEMBER,
    TASKS_MEMBER,
)

#: Per-member uncompressed size cap (ZIP-bomb guard), in bytes.
MAX_MEMBER_BYTES = 256 * 1024 * 1024
#: Total uncompressed size cap across all members, in bytes.
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
#: Maximum compressed upload size accepted by the HTTP route.
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
#: Maximum uncompressed size of one JSON metadata member.
MAX_JSON_MEMBER_BYTES = 64 * 1024 * 1024
#: Maximum combined uncompressed size of JSON metadata members.
MAX_JSON_TOTAL_BYTES = 256 * 1024 * 1024
#: Maximum number of files in one backup archive.
MAX_ARCHIVE_MEMBERS = 100_000


@dataclass(frozen=True, slots=True)
class BackupIncludes:
    """Optional, sensitivity-gated payloads chosen at export time."""

    include_password_hashes: bool
    include_media: bool


@dataclass(frozen=True, slots=True)
class ContestBackupResult:
    """Outcome of building a contest backup archive."""

    filename: str
    include_password_hashes: bool
    include_media: bool


@dataclass(slots=True)
class ContestImportResult:
    """Outcome of a successful contest restore."""

    contest_id: str
    login_slug: str
    contest_name: str
    problem_count: int
    user_count: int
    submission_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RestoreState:
    """Old-to-new identifier maps and created filesystem namespaces."""

    user_map: dict[str, str] = field(default_factory=dict)
    site_map: dict[str, str] = field(default_factory=dict)
    problem_map: dict[str, str] = field(default_factory=dict)
    test_case_map: dict[str, str] = field(default_factory=dict)
    submission_map: dict[str, str] = field(default_factory=dict)
    judgment_map: dict[str, str] = field(default_factory=dict)
    created_problem_ids: list[str] = field(default_factory=list)


class ContestBackupError(ValueError):
    """Raised when a backup archive is malformed or cannot be restored."""


def remap_optional(mapping: Mapping[str, str], key: Any) -> str | None:
    """Remap an optional old identifier through a restore id map.

    Returns the new identifier for ``key`` when it is present, ``None`` when the
    old value was ``None`` (a genuinely absent reference).
    """
    return mapping.get(key) if key is not None else None
