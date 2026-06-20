#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem ZIP import orchestration."""

from __future__ import annotations

import io
import json
import random
import zipfile
from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from web.models.contest import Contest
from web.models.problem import Problem, ProblemTestCase
from web.services.category_service import get_or_create_categories, replace_problem_categories

from .files import (
    parse_testcases_zip,
    save_md_statement,
    save_problem_statement,
    save_testcase_files,
    validate_md_content,
)
from .language_limits import upsert_language_limits
from .models import LanguageLimitInput, ProblemImportResult, problem_meta
from .ordering import append_problem, append_test_case
from .queries import get_contest_languages

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


async def import_problem_from_zip(
    session: AsyncSession,
    contest: Contest,
    zip_bytes: bytes,
    testcase_dir: Path,
    statement_dir: Path,
) -> ProblemImportResult:
    """Import a problem from a ZIP archive."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid ZIP file.") from exc

    if "problem.json" not in archive.namelist():
        raise ValueError("problem.json not found in ZIP.")
    try:
        meta = problem_meta(json.loads(archive.read("problem.json").decode("utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid problem.json: {exc}") from exc

    title = str(meta.get("title", "")).strip()
    if not title:
        raise ValueError("problem.json: 'title' is required.")

    for field in ("time_limit_ms", "memory_limit_kb", "pids_limit"):
        value = meta.get(field)
        if value is None:
            raise ValueError(f"problem.json: '{field}' is required.")
        try:
            if int(str(value)) < 1:
                raise ValueError(f"problem.json: '{field}' must be >= 1.")
        except (TypeError, ValueError) as err:
            raise ValueError(f"problem.json: '{field}' must be a positive integer.") from err

    statement_type: str
    statement_bytes: bytes
    if "statement.pdf" in archive.namelist():
        statement_type = "pdf"
        statement_bytes = archive.read("statement.pdf")
    elif "statement.md" in archive.namelist():
        statement_type = "md"
        raw_md = archive.read("statement.md").decode("utf-8")
        md_errors = validate_md_content(raw_md)
        if md_errors:
            raise ValueError(f"statement.md inválido: {'; '.join(md_errors)}")
        statement_bytes = raw_md.encode("utf-8")
    else:
        raise ValueError("statement.pdf or statement.md is required in the ZIP.")

    parsed = parse_testcases_zip(zip_bytes)

    used_colors = set(await session.scalars(select(Problem.color).where(Problem.contest_id == contest.id)))
    color = _pick_balloon_color(used_colors)

    problem = Problem(
        title=title,
        author=meta.get("author"),
        notes=meta.get("notes"),
        color=color,
        time_limit_ms=int(str(meta["time_limit_ms"])),
        memory_limit_kb=int(str(meta["memory_limit_kb"])),
        pids_limit=int(str(meta["pids_limit"])),
        output_limit_in_bytes=int(str(meta["output_limit_in_bytes"])) if meta.get("output_limit_in_bytes") else None,
    )
    await append_problem(session, contest, problem)

    if statement_type == "pdf":
        await anyio.to_thread.run_sync(lambda: save_problem_statement(problem.id, statement_bytes, statement_dir))
    else:
        statement_text = statement_bytes.decode("utf-8")
        await anyio.to_thread.run_sync(lambda: save_md_statement(problem.id, statement_text, statement_dir))

    def write_imported_test_case(in_bytes: bytes, out_bytes: bytes, ordinal: int) -> tuple[int, int]:
        return save_testcase_files(problem.id, ordinal, in_bytes, out_bytes, testcase_dir)

    for source_ordinal, (in_bytes, out_bytes) in sorted(parsed.pairs.items()):
        test_case = ProblemTestCase(
            is_sample=False,
            explanation=parsed.explanations.get(source_ordinal),
        )
        await append_test_case(session, problem, test_case)
        input_size_bytes, output_size_bytes = await anyio.to_thread.run_sync(
            write_imported_test_case, in_bytes, out_bytes, test_case.ordinal
        )
        test_case.input_size_bytes = input_size_bytes
        test_case.output_size_bytes = output_size_bytes

    raw_categories = meta.get("categories")
    category_names: list[str] = raw_categories if isinstance(raw_categories, list) else []
    if category_names:
        categories = await get_or_create_categories(session, category_names)
        await replace_problem_categories(session, problem, categories)

    raw_language_limits = meta.get("language_limits")
    language_limits: dict[str, LanguageLimitInput] = (
        raw_language_limits if isinstance(raw_language_limits, dict) else {}
    )
    contest_language_ids = {language.id for language in await get_contest_languages(session, contest)}
    skipped_language_ids: list[str] = []
    if language_limits:
        filtered_language_limits: dict[str, LanguageLimitInput] = {}
        for language_id, limit_fields in language_limits.items():
            if language_id in contest_language_ids:
                filtered_language_limits[language_id] = limit_fields
                continue
            skipped_language_ids.append(language_id)
        if filtered_language_limits:
            await upsert_language_limits(session, problem, filtered_language_limits)

    await session.commit()
    return ProblemImportResult(problem=problem, skipped_language_ids=sorted(skipped_language_ids))
