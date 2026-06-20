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
from pathlib import Path

from shared.problem_statement_markdown import validate_md_content as validate_md_content  # noqa: F401
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
    return testcase_dir / problem_id / f"{ordinal:03d}.{ext}"


def save_testcase_files(
    problem_id: str, ordinal: int, in_bytes: bytes, out_bytes: bytes, testcase_dir: Path
) -> tuple[int, int]:
    """Write one pair of testcase files to disk.

    Content is normalized to Unix line endings (LF only) before writing.

    Returns:
        tuple[int, int]: ``(input_size_bytes, output_size_bytes)`` of the
        normalized content written to disk.
    """
    base = testcase_dir / problem_id
    base.mkdir(parents=True, exist_ok=True)
    in_norm = normalize_testcase_bytes(in_bytes)
    out_norm = normalize_testcase_bytes(out_bytes)
    (base / f"{ordinal:03d}.in").write_bytes(in_norm)
    (base / f"{ordinal:03d}.out").write_bytes(out_norm)
    return len(in_norm), len(out_norm)


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
    shutil.rmtree(testcase_dir / problem_id, ignore_errors=True)


def renumber_testcase_files(problem_id: str, old_ordinal: int, new_ordinal: int, testcase_dir: Path) -> None:
    """Rename test case files from old_ordinal to new_ordinal."""
    base = testcase_dir / problem_id
    for ext in ("in", "out"):
        src = base / f"{old_ordinal:03d}.{ext}"
        dst = base / f"{new_ordinal:03d}.{ext}"
        if src.exists():
            src.rename(dst)


def reorder_testcase_files(problem_id: str, ordinal_map: dict[int, int], testcase_dir: Path) -> None:
    """Rename testcase files through temporary paths for an arbitrary reorder."""
    base = testcase_dir / problem_id
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
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(statement_zip_name, active_statement.read_bytes())

        test_cases = (
            problem.test_cases if include_private_testcases else [tc for tc in problem.test_cases if tc.is_sample]
        )
        for test_case in sorted(test_cases, key=lambda item: item.ordinal):
            in_path = get_testcase_path(problem.id, test_case.ordinal, "in", testcase_dir)
            out_path = get_testcase_path(problem.id, test_case.ordinal, "out", testcase_dir)
            archive.writestr(f"in/{test_case.ordinal:03d}.in", in_path.read_bytes() if in_path.exists() else b"")
            archive.writestr(
                f"out/{test_case.ordinal:03d}.out",
                out_path.read_bytes() if out_path.exists() else b"",
            )
            if test_case.explanation:
                archive.writestr(
                    f"explanation/{test_case.ordinal:03d}.txt",
                    test_case.explanation.encode("utf-8"),
                )

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

            problem_json = {
                "title": problem.title,
                "author": problem.author,
                "notes": problem.notes,
                "color": problem.color or "#000000",
                "time_limit_ms": problem.time_limit_ms,
                "memory_limit_kb": problem.memory_limit_kb,
                "pids_limit": problem.pids_limit,
                "output_limit_in_bytes": problem.output_limit_in_bytes,
                "categories": [category.name for category in problem.categories],
                "language_limits": limits_dict,
            }
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
