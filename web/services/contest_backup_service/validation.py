#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Bounded archive parsing and top-level backup validation."""

from __future__ import annotations

import json
import posixpath
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import contests as contests_t
from shared.db_schema import languages as languages_t
from web.services.contest_service import slug_format_is_valid

from .models import (
    MAX_ARCHIVE_MEMBERS,
    MAX_JSON_MEMBER_BYTES,
    MAX_JSON_TOTAL_BYTES,
    MAX_MEMBER_BYTES,
    MAX_TOTAL_BYTES,
    REQUIRED_MEMBERS,
    SUPPORTED_FORMAT_VERSIONS,
    ContestBackupError,
)

ArchiveIndex = dict[str, int]
_ROW_LIST_ADAPTER = TypeAdapter(list[dict[str, Any]])


class _IncludesModel(BaseModel):
    """Sensitivity flags stored in the manifest."""

    model_config = ConfigDict(extra="forbid", strict=True)

    include_password_hashes: bool
    include_media: bool


class _ProblemReferenceModel(BaseModel):
    """Manifest reference to one problem payload directory."""

    model_config = ConfigDict(extra="forbid", strict=True)

    original_id: str
    ordinal: int
    dir: str


class _ManifestModel(BaseModel):
    """Strict manifest shape, identical across both supported versions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    format_version: int
    exported_at: str
    includes: _IncludesModel
    contest: dict[str, Any]
    sites: list[dict[str, Any]]
    language_ids: list[str]
    problems: list[_ProblemReferenceModel]


def inspect_archive(zip_path: Path) -> ArchiveIndex:
    """Validate ZIP names and declared sizes without loading payload bytes."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            files = [info for info in archive.infolist() if not info.is_dir()]
    except (OSError, zipfile.BadZipFile) as exc:
        raise ContestBackupError("The uploaded file is not a valid ZIP archive.") from exc

    if len(files) > MAX_ARCHIVE_MEMBERS:
        raise ContestBackupError("Archive contains too many files.")

    index: ArchiveIndex = {}
    total = 0
    json_total = 0
    for info in files:
        name = info.filename
        _reject_unsafe_name(name)
        if name in index:
            raise ContestBackupError(f"Duplicate archive member: {name!r}.")
        if info.file_size > MAX_MEMBER_BYTES:
            raise ContestBackupError(f"Archive member {name!r} exceeds the per-file size limit.")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise ContestBackupError("Archive exceeds the total uncompressed size limit.")
        if name.endswith(".json"):
            json_total += info.file_size
            if json_total > MAX_JSON_TOTAL_BYTES:
                raise ContestBackupError("Archive JSON metadata exceeds the combined size limit.")
        index[name] = info.file_size
    return index


def _reject_unsafe_name(name: str) -> None:
    """Reject absolute paths, drive letters, and traversal components."""
    if not name or name.startswith(("/", "\\")) or ":" in name:
        raise ContestBackupError(f"Unsafe archive member name: {name!r}.")
    normalized = posixpath.normpath(name)
    if normalized.startswith("..") or normalized.startswith("/") or normalized != name.replace("\\", "/"):
        raise ContestBackupError(f"Unsafe archive member name: {name!r}.")


def read_member_bytes(
    zip_path: Path,
    index: ArchiveIndex,
    name: str,
    *,
    max_bytes: int = MAX_MEMBER_BYTES,
) -> bytes:
    """Read one member with declared and observed byte ceilings."""
    declared_size = index.get(name)
    if declared_size is None:
        raise ContestBackupError(f"Missing required archive member: {name}.")
    if declared_size > max_bytes:
        raise ContestBackupError(f"Archive member {name!r} exceeds its allowed size.")

    chunks: list[bytes] = []
    observed = 0
    try:
        with zipfile.ZipFile(zip_path) as archive, archive.open(name) as member:
            while chunk := member.read(min(1024 * 1024, max_bytes + 1 - observed)):
                observed += len(chunk)
                if observed > max_bytes or observed > declared_size:
                    raise ContestBackupError(f"Archive member {name!r} expands beyond its declared size.")
                chunks.append(chunk)
    except ContestBackupError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ContestBackupError(f"Could not read archive member {name!r}.") from exc

    if observed != declared_size:
        raise ContestBackupError(f"Archive member {name!r} has an invalid uncompressed size.")
    return b"".join(chunks)


def parse_member_json(zip_path: Path, index: ArchiveIndex, name: str) -> Any:
    """Decode one bounded JSON metadata member."""
    try:
        raw = read_member_bytes(zip_path, index, name, max_bytes=MAX_JSON_MEMBER_BYTES)
        return json.loads(raw.decode("utf-8"))
    except ContestBackupError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContestBackupError(f"Invalid JSON in {name}: {exc}") from exc


def parse_row_list(zip_path: Path, index: ArchiveIndex, name: str) -> list[dict[str, Any]]:
    """Parse a JSON member as a strict array of row objects."""
    value = parse_member_json(zip_path, index, name)
    try:
        return _ROW_LIST_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise ContestBackupError(f"{name} must be a JSON array of objects: {exc.errors()[0]['msg']}.") from exc


def validate_manifest(manifest: Any, index: ArchiveIndex) -> dict[str, Any]:
    """Validate the strict manifest shape and required member presence."""
    if not isinstance(manifest, dict):
        raise ContestBackupError("manifest.json must be a JSON object.")
    if manifest.get("format_version") not in SUPPORTED_FORMAT_VERSIONS:
        supported = ", ".join(str(version) for version in SUPPORTED_FORMAT_VERSIONS)
        noun = "version" if len(SUPPORTED_FORMAT_VERSIONS) == 1 else "versions"
        raise ContestBackupError(
            f"Unsupported backup format version {manifest.get('format_version')!r}; "
            f"this server restores {noun} {supported}."
        )
    try:
        validated = _ManifestModel.model_validate(manifest, strict=True)
    except ValidationError as exc:
        raise ContestBackupError(f"Invalid manifest.json: {exc.errors()[0]['msg']}.") from exc

    for required in REQUIRED_MEMBERS:
        if required not in index:
            raise ContestBackupError(f"Missing required archive member: {required}.")
    if validated.includes.include_media and "media.json" not in index:
        raise ContestBackupError("Manifest requests user media, but media.json is missing.")
    if not validated.includes.include_media and "media.json" in index:
        raise ContestBackupError("media.json is present while the manifest says media is excluded.")

    for problem in validated.problems:
        if not any(name.startswith(f"{problem.dir}/") for name in index):
            raise ContestBackupError(f"Backup references problem folder {problem.dir!r} that is absent.")
    return validated.model_dump()


async def validate_slug(session: AsyncSession, new_slug: str) -> str:
    """Validate slug format, length, and availability."""
    cleaned = new_slug.strip()
    if not cleaned:
        raise ContestBackupError("A new contest slug is required.")
    if len(cleaned) > 80:
        raise ContestBackupError("Contest slug must be 80 characters or fewer.")
    if not slug_format_is_valid(cleaned):
        raise ContestBackupError("Slug must contain only lowercase letters, numbers, and hyphens.")
    existing = (await session.execute(select(contests_t.c.id).where(contests_t.c.login_slug == cleaned))).first()
    if existing is not None:
        raise ContestBackupError("A contest with this slug already exists.")
    return cleaned


async def validate_languages_fail_closed(session: AsyncSession, language_ids: set[str]) -> None:
    """Reject the import if any referenced language is unavailable."""
    if not language_ids:
        return
    known = set((await session.execute(select(languages_t.c.id).where(languages_t.c.id.in_(language_ids)))).scalars())
    missing = sorted(language_ids - known)
    if missing:
        raise ContestBackupError(
            "Cannot restore: these languages are not registered on this server: " + ", ".join(missing)
        )
