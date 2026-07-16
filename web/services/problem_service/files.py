#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem statement, test-case, and ZIP file helpers."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from base64 import b64decode
from pathlib import Path
from typing import Any

from shared.problem_statement_markdown import validate_md_content as validate_md_content  # noqa: F401
from shared.services.custom_validator import packaged_validator_member
from shared.services.problem_image import export_image_filename
from shared.services.sample_interactions import build_interaction_files
from shared.services.testcase_files import get_problem_testcase_dir as _shared_get_problem_testcase_dir
from shared.services.testcase_files import save_testcase_files as _shared_save_testcase_files
from shared.tc_zip import normalize_testcase_bytes as normalize_testcase_bytes  # noqa: F401
from shared.tc_zip import parse_testcases_zip as parse_testcases_zip  # noqa: F401
from web.models.problem import Problem, ProblemLanguageLimit

from .models import LanguageLimitExport


def get_statement_path(problem_id: str, statement_dir: Path) -> Path:
    """Return the path for the PDF statement file."""
    return statement_dir / f"{problem_id}-statement.pdf"


def get_md_statement_path(problem_id: str, statement_dir: Path) -> Path:
    """Return the path for the Markdown statement file."""
    return statement_dir / f"{problem_id}-statement.md"


def get_active_statement_path(problem_id: str, statement_dir: Path) -> Path | None:
    """Return the path of the active statement file, preferring Markdown."""
    md_path = get_md_statement_path(problem_id, statement_dir)
    if md_path.exists():
        return md_path
    pdf_path = get_statement_path(problem_id, statement_dir)
    if pdf_path.exists():
        return pdf_path
    return None


def save_problem_statement(problem_id: str, pdf_bytes: bytes, statement_dir: Path) -> None:
    """Write PDF statement bytes to disk."""
    get_statement_path(problem_id, statement_dir).write_bytes(pdf_bytes)


def save_md_statement(problem_id: str, md_text: str, statement_dir: Path) -> None:
    """Write Markdown statement text to disk."""
    get_md_statement_path(problem_id, statement_dir).write_text(md_text, encoding="utf-8")


def delete_problem_statement(problem_id: str, statement_dir: Path) -> None:
    """Delete both PDF and Markdown statement files if they exist."""
    get_statement_path(problem_id, statement_dir).unlink(missing_ok=True)
    get_md_statement_path(problem_id, statement_dir).unlink(missing_ok=True)


def delete_md_statement(problem_id: str, statement_dir: Path) -> None:
    """Delete the Markdown statement file if it exists."""
    get_md_statement_path(problem_id, statement_dir).unlink(missing_ok=True)


def get_testcase_path(problem_id: str, ordinal: int, ext: str, testcase_dir: Path) -> Path:
    """Return the path for one test-case file."""
    if ext not in {"in", "out"}:
        raise ValueError(f"Invalid testcase extension: {ext!r}")
    base = _shared_get_problem_testcase_dir(problem_id, testcase_dir)
    path = (base / f"{ordinal:03d}.{ext}").resolve(strict=False)
    root = testcase_dir.resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Testcase path escapes configured root: {problem_id!r}")
    return path


def save_testcase_files(
    problem_id: str, ordinal: int, in_bytes: bytes, out_bytes: bytes | None, testcase_dir: Path
) -> tuple[int, int | None]:
    """Write one test case to disk.

    Content is normalized to Unix line endings (LF only) before writing.

    Args:
        out_bytes: Expected output, or ``None`` for a custom-validator case, which
            has no expected output.

    Returns:
        tuple[int, int | None]: ``(input_size_bytes, output_size_bytes)`` of the
        normalized content written to disk; the output size is ``None`` when the
        case has no expected output.
    """
    return _shared_save_testcase_files(problem_id, ordinal, in_bytes, out_bytes, testcase_dir)


def _problem_has_custom_validator(problem: Problem) -> bool:
    """Return whether a problem currently has an active or candidate validator."""
    validator = problem.custom_validator
    return bool(validator and (validator.active_source is not None or validator.candidate_source is not None))


def read_testcase_preview(problem_id: str, ordinal: int, testcase_dir: Path, max_bytes: int = 32) -> tuple[str, str]:
    """Read a short preview of one testcase pair."""
    in_path = get_testcase_path(problem_id, ordinal, "in", testcase_dir)
    out_path = get_testcase_path(problem_id, ordinal, "out", testcase_dir)
    try:
        in_data = in_path.read_bytes()[:max_bytes].decode("utf-8", errors="replace")
    except FileNotFoundError:
        in_data = ""
    try:
        out_data = out_path.read_bytes()[:max_bytes].decode("utf-8", errors="replace")
    except FileNotFoundError:
        out_data = ""
    return in_data, out_data


def read_testcase_full(problem_id: str, ordinal: int, testcase_dir: Path) -> tuple[str, str]:
    """Read the full contents of one testcase pair."""
    in_path = get_testcase_path(problem_id, ordinal, "in", testcase_dir)
    out_path = get_testcase_path(problem_id, ordinal, "out", testcase_dir)
    try:
        in_data = in_path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        in_data = ""
    try:
        out_data = out_path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        out_data = ""
    return in_data, out_data


def delete_testcase_files(problem_id: str, ordinal: int, testcase_dir: Path) -> None:
    """Delete one testcase pair if present."""
    get_testcase_path(problem_id, ordinal, "in", testcase_dir).unlink(missing_ok=True)
    get_testcase_path(problem_id, ordinal, "out", testcase_dir).unlink(missing_ok=True)


def delete_all_testcase_files(problem_id: str, testcase_dir: Path) -> None:
    """Delete all testcase files for one problem."""
    shutil.rmtree(_shared_get_problem_testcase_dir(problem_id, testcase_dir), ignore_errors=True)


def renumber_testcase_files(problem_id: str, old_ordinal: int, new_ordinal: int, testcase_dir: Path) -> None:
    """Rename test case files from old_ordinal to new_ordinal."""
    base = _shared_get_problem_testcase_dir(problem_id, testcase_dir)
    for ext in ("in", "out"):
        src = base / f"{old_ordinal:03d}.{ext}"
        dst = base / f"{new_ordinal:03d}.{ext}"
        if src.exists():
            src.rename(dst)


def reorder_testcase_files(problem_id: str, ordinal_map: dict[int, int], testcase_dir: Path) -> None:
    """Rename testcase files through temporary paths for an arbitrary reorder."""
    base = _shared_get_problem_testcase_dir(problem_id, testcase_dir)
    if not base.exists():
        return

    changed_ordinals = {old: new for old, new in ordinal_map.items() if old != new}
    temp_paths: list[tuple[Path, Path]] = []
    for old_ordinal in changed_ordinals:
        for ext in ("in", "out"):
            src = base / f"{old_ordinal:03d}.{ext}"
            if not src.exists():
                continue
            tmp = base / f".noca-reorder-{old_ordinal:03d}.{ext}.tmp"
            if tmp.exists():
                msg = f"Temporary testcase reorder file already exists: {tmp.name}"
                raise FileExistsError(msg)
            src.rename(tmp)
            temp_paths.append((tmp, base / f"{changed_ordinals[old_ordinal]:03d}.{ext}"))

    for tmp, dst in temp_paths:
        tmp.rename(dst)


def build_problem_export_zip(
    problem: Problem,
    testcase_dir: Path,
    statement_dir: Path,
    *,
    include_private_testcases: bool,
    include_problem_json: bool,
    language_limits: dict[str, ProblemLanguageLimit] | None = None,
) -> bytes:
    """Build an in-memory problem export ZIP archive."""
    active_statement = get_active_statement_path(problem.id, statement_dir)
    if active_statement is None:
        raise ValueError(f"No statement file found for problem {problem.id}")
    statement_zip_name = "statement.md" if active_statement.suffix == ".md" else "statement.pdf"

    buffer = io.BytesIO()
    image_filename: str | None = None
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(statement_zip_name, active_statement.read_bytes())

        # The image ships in the public ZIP too: it is part of the statement a
        # contestant reads, not privileged data.
        if problem.problem_image_base64:
            image_filename = export_image_filename(problem.problem_image_mime)
            archive.writestr(image_filename, b64decode(problem.problem_image_base64))

        has_custom_validator = _problem_has_custom_validator(problem)
        test_cases = (
            problem.test_cases if include_private_testcases else [tc for tc in problem.test_cases if tc.is_sample]
        )
        for test_case in sorted(test_cases, key=lambda item: item.ordinal):
            in_path = get_testcase_path(problem.id, test_case.ordinal, "in", testcase_dir)
            out_path = get_testcase_path(problem.id, test_case.ordinal, "out", testcase_dir)
            archive.writestr(f"in/{test_case.ordinal:03d}.in", in_path.read_bytes() if in_path.exists() else b"")
            # An interactive problem's cases have no expected output at all, so the
            # package ships inputs only rather than a misleading empty .out.
            if not has_custom_validator and out_path.exists():
                archive.writestr(f"out/{test_case.ordinal:03d}.out", out_path.read_bytes())
            if test_case.explanation:
                archive.writestr(
                    f"explanation/{test_case.ordinal:03d}.txt",
                    test_case.explanation.encode("utf-8"),
                )

        # An interactive problem's public examples are its sample interactions, so
        # they ship in the public ZIP too. Hidden ones (kept through a validator
        # removal) stay out, and the survivors are renumbered to close the gaps.
        if has_custom_validator:
            visible = [
                (interaction.transcript, interaction.explanation)
                for interaction in sorted(problem.sample_interactions, key=lambda item: item.ordinal)
                if interaction.hidden_at is None
            ]
            for name, content in build_interaction_files(visible):
                archive.writestr(name, content)

        if include_problem_json:
            if language_limits is None:
                raise ValueError("language_limits is required when include_problem_json is true.")

            limits_dict: dict[str, LanguageLimitExport] = {}
            for language_id, limit in language_limits.items():
                limits_dict[language_id] = {
                    "time_limit_ms": limit.time_limit_ms,
                    "memory_limit_kb": limit.memory_limit_kb,
                    "pids_limit": limit.pids_limit,
                    "output_limit_in_bytes": limit.output_limit_in_bytes,
                    "repetitions": limit.repetitions,
                }

            problem_json: dict[str, Any] = {
                "title": problem.title,
                "author": problem.author,
                "notes": problem.notes,
                "color": problem.color or "#000000",
                "time_limit_ms": problem.time_limit_ms,
                "memory_limit_kb": problem.memory_limit_kb,
                "pids_limit": problem.pids_limit,
                "output_limit_in_bytes": problem.output_limit_in_bytes,
                "categories": [category.name for category in problem.categories],
                "image": image_filename,
                "image_caption": problem.problem_image_caption,
                "language_limits": limits_dict,
            }
            validator = problem.custom_validator
            validator_source = None
            validator_language_id = None
            if validator is not None:
                # Prefer the validated active revision; fall back to a staged
                # candidate only when no active revision exists yet.
                if validator.active_source is not None:
                    validator_source = validator.active_source
                    validator_language_id = validator.active_language_id
                else:
                    validator_source = validator.candidate_source
                    validator_language_id = validator.candidate_language_id
            if validator_source is not None and validator_language_id is not None:
                validator_member = packaged_validator_member(validator_language_id)
                problem_json["custom_validator"] = {
                    "language_id": validator_language_id,
                    "source_file": validator_member,
                }
                archive.writestr(validator_member, validator_source.encode("utf-8"))
            archive.writestr("problem.json", json.dumps(problem_json, indent=2))

    return buffer.getvalue()


def build_export_zip(
    problem: Problem,
    testcase_dir: Path,
    statement_dir: Path,
    language_limits: dict[str, ProblemLanguageLimit],
) -> bytes:
    """Build an in-memory ZIP using Layout A with all testcases and problem.json."""
    return build_problem_export_zip(
        problem,
        testcase_dir,
        statement_dir,
        include_private_testcases=True,
        include_problem_json=True,
        language_limits=language_limits,
    )


def build_public_export_zip(
    problem: Problem,
    testcase_dir: Path,
    statement_dir: Path,
) -> bytes:
    """Build an in-memory ZIP with statement plus sample test cases only."""
    return build_problem_export_zip(
        problem,
        testcase_dir,
        statement_dir,
        include_private_testcases=False,
        include_problem_json=False,
    )
