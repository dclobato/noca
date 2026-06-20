#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena problem ZIP import/export service.

The package format is kept as compatible as possible with the web module's
problem package (same ``problem.json`` shared keys, same ``statement.md`` and
``in/NNN.in`` / ``out/NNN.out`` test-case layout) so packages can be moved
between the two platforms.  Arena-specific extras (``source``,
``hide_author_show_source``, ``image``, ``image_caption``, ``notes``, ``license``) are added on top;
web-only keys (``color``, ``language_limits``) are ignored on import.

Import rules:
  - the problem owner is always set to the importing user (``caller_id``);
  - a non-empty ``author`` field is preserved as free-text authorship; when it
    is missing, the importing owner is treated as the author;
  - every imported test case is stored as secret (``is_sample=False``);
  - an optional image is validated through ``ImageProcessingService`` before the
    problem is accepted;
  - categories are matched to existing ones by slug/name; unknown ones are
    dropped (arena never auto-creates categories during import).
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from base64 import b64decode, b64encode
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaCategory, ArenaProblem, ArenaTestCase
from arena.services import admin_problem_service
from arena.services.admin_category_service import normalize_slug
from shared.problem_statement_markdown import validate_md_content
from shared.services.imageprocessing_service import ImageProcessingError, ImageProcessingService
from shared.services.testcase_files import get_testcase_path, save_testcase_files
from shared.tc_zip import ParsedTestCases, normalize_testcase_bytes, parse_testcases_zip

_MAX_IMAGE_SIZE = 2 * 1024 * 1024  # 2 MB — matches the manual problem-image upload limit
_DEFAULT_OUTPUT_LIMIT_BYTES = 65536

_MIME_TO_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_EXT_TO_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}


def build_export_zip(problem: ArenaProblem, owner_name: str, testcase_dir: Path) -> bytes:
    """Build an in-memory ZIP archive holding all data for an Arena problem.

    The ``problem.categories`` and ``problem.test_cases`` relationships must be
    eagerly loaded before calling this function. Test-case content is read from
    the shared filesystem under ``<testcase_dir>/<problem_id>/NNN.in|out``.

    Args:
        problem: The problem to export.
        owner_name: Display name used when the owner is the problem author.
        testcase_dir: Arena test-case root (``<root>/arena``).

    Returns:
        bytes: The ZIP archive contents.
    """
    buffer = io.BytesIO()
    image_filename: str | None = None
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("statement.md", problem.problem_statement or "")

        for test_case in sorted(problem.test_cases, key=lambda tc: tc.ordinal):
            in_path = get_testcase_path(problem.id, test_case.ordinal, "in", testcase_dir)
            out_path = get_testcase_path(problem.id, test_case.ordinal, "out", testcase_dir)
            archive.writestr(f"in/{test_case.ordinal:03d}.in", in_path.read_bytes() if in_path.exists() else b"")
            archive.writestr(f"out/{test_case.ordinal:03d}.out", out_path.read_bytes() if out_path.exists() else b"")
            if test_case.explanation:
                archive.writestr(f"explanation/{test_case.ordinal:03d}.txt", test_case.explanation)

        if problem.problem_image_base64:
            ext = _MIME_TO_EXT.get(problem.problem_image_mime or "", "png")
            image_filename = f"image.{ext}"
            archive.writestr(image_filename, b64decode(problem.problem_image_base64))

        problem_json = {
            "title": problem.title,
            "author": owner_name if problem.author_is_owner else problem.author,
            "source": problem.source,
            "hide_author_show_source": problem.hide_author_show_source,
            "time_limit_ms": problem.time_limit_ms,
            "memory_limit_kb": problem.memory_limit_kb,
            "pids_limit": problem.pids_limit,
            "output_limit_in_bytes": problem.output_limit_in_bytes,
            "categories": [category.name for category in problem.categories],
            "image": image_filename,
            "image_caption": problem.problem_image_caption,
            "notes": problem.notes,
            "license": problem.license,
        }
        archive.writestr("problem.json", json.dumps(problem_json, indent=2))

    return buffer.getvalue()


async def import_problem_from_zip(
    session: AsyncSession,
    *,
    zip_bytes: bytes,
    caller_id: str,
    image_service: ImageProcessingService,
    testcase_dir: Path,
) -> ArenaProblem:
    """Import an Arena problem from a ZIP package and persist it.

    Args:
        session: Active async database session.
        zip_bytes: Raw bytes of the uploaded ZIP package.
        caller_id: UUID of the importing user, set as the problem owner.
        image_service: Service used to validate a packaged image, if present.

    Returns:
        ArenaProblem: The newly created, committed problem.

    Raises:
        ValueError: On any malformed package or validation failure.
        ImageProcessingError: When a packaged image fails processing.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid ZIP file.") from exc

    names = archive.namelist()
    meta = _read_problem_json(archive, names)

    title = str(meta.get("title", "")).strip()
    if not title:
        raise ValueError("problem.json: 'title' is required.")

    statement = _read_statement(archive, names)
    # parse_testcases_zip owns its own archive handling, pairing checks, and
    # contiguous ordinal remap, so it re-opens the raw bytes independently.
    parsed = parse_testcases_zip(zip_bytes)
    image_b64, image_mime = _load_image(meta, archive, names, image_service)
    category_ids = await _resolve_category_ids(session, meta.get("categories"))

    imported_author = _optional_string(meta, "author")
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=caller_id,
        title=title,
        author=imported_author,
        author_is_owner=imported_author is None,
        source=_optional_string(meta, "source"),
        hide_author_show_source=bool(meta.get("hide_author_show_source", False)),
        time_limit_ms=_int_field(meta, "time_limit_ms"),
        memory_limit_kb=_int_field(meta, "memory_limit_kb"),
        pids_limit=_int_field(meta, "pids_limit"),
        output_limit_in_bytes=_int_field(meta, "output_limit_in_bytes", default=_DEFAULT_OUTPUT_LIMIT_BYTES),
        problem_statement=statement,
        image_b64=image_b64,
        image_mime=image_mime,
        image_caption=(str(meta["image_caption"]).strip() if meta.get("image_caption") else None),
        notes=_optional_string(meta, "notes"),
        license=_optional_string(meta, "license"),
        category_ids=category_ids,
    )

    write_tc_files = _insert_secret_test_cases(session, problem.id, parsed, testcase_dir)
    await session.commit()
    write_tc_files()
    return problem


def _optional_string(meta: dict[str, Any], key: str) -> str | None:
    """Return a trimmed optional string from package metadata."""
    value = meta.get(key)
    normalized = str(value).strip() if value else ""
    return normalized or None


def _read_problem_json(archive: zipfile.ZipFile, names: list[str]) -> dict[str, Any]:
    """Read and parse ``problem.json`` from the archive."""
    if "problem.json" not in names:
        raise ValueError("problem.json not found in ZIP.")
    try:
        meta = json.loads(archive.read("problem.json").decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid problem.json: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("problem.json must contain a JSON object.")
    return meta


def _read_statement(archive: zipfile.ZipFile, names: list[str]) -> str:
    """Read and validate ``statement.md`` from the archive."""
    if "statement.md" not in names:
        raise ValueError("statement.md is required in the ZIP.")
    try:
        statement = archive.read("statement.md").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"statement.md is not valid UTF-8: {exc}") from exc
    md_errors = validate_md_content(statement)
    if md_errors:
        raise ValueError(f"Invalid statement.md: {'; '.join(md_errors)}")
    return statement


def _int_field(meta: dict[str, Any], key: str, *, default: int | None = None) -> int:
    """Extract a positive-integer field from the metadata, applying a default."""
    value = meta.get(key)
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"problem.json: '{key}' is required.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"problem.json: '{key}' must be an integer.") from exc


def _find_image_file(names: list[str]) -> str | None:
    """Return the first root-level image file in the archive, if any."""
    for name in names:
        if "/" in name:
            continue
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _EXT_TO_MIME:
            return name
    return None


def _load_image(
    meta: dict[str, Any],
    archive: zipfile.ZipFile,
    names: list[str],
    image_service: ImageProcessingService,
) -> tuple[str | None, str | None]:
    """Validate and return the packaged image as ``(base64, mime)`` or ``(None, None)``.

    When ``problem.json`` explicitly references an image filename, that file must
    exist in the archive — a missing referenced image rejects the package rather
    than silently dropping the image. Only when no image is referenced does the
    loader fall back to auto-detecting a root-level image file.
    """
    referenced = meta.get("image")
    filename: str | None
    if isinstance(referenced, str) and referenced.strip():
        filename = referenced.strip()
        if filename not in names:
            raise ValueError(f"problem.json references image '{filename}' which is not present in the ZIP.")
    else:
        filename = _find_image_file(names)
    if not filename:
        return None, None

    image_bytes = archive.read(filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    mime = _EXT_TO_MIME.get(ext, "image/png")
    data_uri = f"data:{mime};base64,{b64encode(image_bytes).decode('ascii')}"
    try:
        result = image_service.process_base64(data_uri, max_file_size=_MAX_IMAGE_SIZE)
    except (ImageProcessingError, ValueError) as exc:
        raise ValueError(f"Invalid problem image: {exc}") from exc
    return result.imagem_base64, result.mime_type


async def _resolve_category_ids(session: AsyncSession, raw_categories: Any) -> list[str]:
    """Resolve category names to existing category IDs, dropping unknown ones."""
    if not isinstance(raw_categories, list):
        return []
    names = [str(name).strip() for name in raw_categories if str(name).strip()]
    if not names:
        return []
    slugs = {normalize_slug(name) for name in names}
    lowered = {name.lower() for name in names}
    result = await session.execute(
        select(ArenaCategory.id).where(or_(ArenaCategory.slug.in_(slugs), func.lower(ArenaCategory.name).in_(lowered)))
    )
    return list(result.scalars())


def _decode_test_case(data: bytes, *, ordinal: int, stream: str) -> str:
    """Strictly decode a test-case file as UTF-8, failing loudly on binary data.

    Args:
        data: Raw file bytes from the archive.
        ordinal: 1-based test-case ordinal, used for the error message.
        stream: Either ``"input"`` or ``"output"``, used for the error message.

    Returns:
        str: The decoded UTF-8 text.

    Raises:
        ValueError: When the bytes are not valid UTF-8.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Test case {ordinal:03d} {stream} is not valid UTF-8 text; binary test cases are not supported."
        ) from exc


def _insert_secret_test_cases(
    session: AsyncSession,
    problem_id: str,
    parsed: ParsedTestCases,
    testcase_dir: Path,
) -> Callable[[], None]:
    """Bulk-add all parsed test cases as secret (``is_sample=False``).

    Sizes are computed from in-memory normalization; no files are written here.
    Returns a zero-arg callable that the caller must invoke **after** committing
    the transaction to write the test-case files to disk.  UTF-8 validity is
    still enforced before the DB rows are added.
    """
    pairs_for_disk: list[tuple[int, bytes, bytes]] = []
    now = datetime.now(UTC)
    for ordinal, (in_bytes, out_bytes) in sorted(parsed.pairs.items()):
        # Enforce UTF-8 (binary test cases are unsupported) before writing.
        _decode_test_case(in_bytes, ordinal=ordinal, stream="input")
        _decode_test_case(out_bytes, ordinal=ordinal, stream="output")
        in_norm = normalize_testcase_bytes(in_bytes)
        out_norm = normalize_testcase_bytes(out_bytes)
        session.add(
            ArenaTestCase(
                id=str(uuid.uuid4()),
                problem_id=problem_id,
                ordinal=ordinal,
                is_sample=False,
                input_size_bytes=len(in_norm),
                output_size_bytes=len(out_norm),
                explanation=parsed.explanations.get(ordinal),
                created_at=now,
                updated_at=now,
            )
        )
        pairs_for_disk.append((ordinal, in_bytes, out_bytes))

    def _write_files() -> None:
        for ordinal, in_b, out_b in pairs_for_disk:
            save_testcase_files(problem_id, ordinal, in_b, out_b, testcase_dir)

    return _write_files
