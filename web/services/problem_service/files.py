#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Problem statement, test-case, and ZIP file helpers."""

from __future__ import annotations

import shutil
from base64 import b64decode
from pathlib import Path

from shared.enumerations import ProblemValidatorType
from shared.problem_statement_markdown import validate_md_content as validate_md_content  # noqa: F401
from shared.services.custom_validator import PackagedValidator, current_validator_source
from shared.services.problem_image import export_image_filename
from shared.services.problem_package import (
    FORMAT_VERSION,
    PackageError,
    PackageImage,
    PackageLanguageLimit,
    PackageMetadata,
    PackageStatement,
    PackageTestCase,
    ProblemPackage,
    build_package,
)
from shared.services.problem_package.writer import PackageProfile
from shared.services.sample_interactions import PackagedInteraction
from shared.services.testcase_files import get_problem_testcase_dir as _shared_get_problem_testcase_dir
from shared.services.testcase_files import save_testcase_files as _shared_save_testcase_files
from shared.tc_zip import normalize_testcase_bytes as normalize_testcase_bytes  # noqa: F401
from shared.tc_zip import parse_testcases_zip as parse_testcases_zip  # noqa: F401
from web.models.problem import Problem, ProblemLanguageLimit


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


def _problem_is_interactive(problem: Problem) -> bool:
    """Return whether the problem's stored strategy is interactive."""
    return problem.validator_type is ProblemValidatorType.INTERACTIVE


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


def problem_to_package(
    problem: Problem,
    testcase_dir: Path,
    statement_dir: Path,
    language_limits: dict[str, ProblemLanguageLimit],
) -> ProblemPackage:
    """Project a contest problem onto the shared package contract.

    The ``problem.categories``, ``problem.test_cases``, and
    ``problem.sample_interactions`` relationships must be eagerly loaded.

    Raises:
        PackageError: If the problem has no statement file, so an export fails
            with an actionable message rather than shipping an empty member.
    """
    active_statement = get_active_statement_path(problem.id, statement_dir)
    if active_statement is None:
        raise PackageError(f"Cannot export: no statement file is stored for problem {problem.id}.")

    validator_source = current_validator_source(problem.custom_validator)
    # The export's shape follows the stored strategy, so an interactive problem
    # whose source was removed still exports as interactive (input-only cases and
    # its sample interactions) rather than silently changing kind.
    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE

    cases = tuple(
        PackageTestCase(
            ordinal=test_case.ordinal,
            is_sample=test_case.is_sample,
            input_path=get_testcase_path(problem.id, test_case.ordinal, "in", testcase_dir),
            output_path=(
                None if interactive else get_testcase_path(problem.id, test_case.ordinal, "out", testcase_dir)
            ),
            explanation=test_case.explanation,
        )
        for test_case in sorted(problem.test_cases, key=lambda item: item.ordinal)
    )

    # The image ships in both profiles: it is part of the statement a contestant
    # reads, not privileged data.
    image = None
    if problem.problem_image_base64:
        image = PackageImage(
            member=export_image_filename(problem.problem_image_mime),
            path=None,
            mime=problem.problem_image_mime or "image/png",
            data=b64decode(problem.problem_image_base64),
        )

    metadata = PackageMetadata(
        format_version=FORMAT_VERSION,
        validator_type=problem.validator_type,
        title=problem.title,
        author=problem.author,
        notes=problem.notes,
        # Contest has no source, license, statement language, or expected
        # difficulty; the keys are still written, as null.
        source=None,
        license=None,
        color=problem.color,
        hide_author_show_source=False,
        statement_language=None,
        expected_difficulty=None,
        time_limit_ms=problem.time_limit_ms,
        memory_limit_kb=problem.memory_limit_kb,
        pids_limit=problem.pids_limit,
        output_limit_in_bytes=problem.output_limit_in_bytes,
        categories=tuple(category.name for category in problem.categories),
        # The contest domain has no collections; the key is still written, as null.
        collection=None,
        sample_testcases=tuple(case.ordinal for case in cases if case.is_sample),
        image=image.member if image is not None else None,
        image_caption=problem.problem_image_caption,
        language_limits={
            language_id: PackageLanguageLimit(
                time_limit_ms=limit.time_limit_ms,
                memory_limit_kb=limit.memory_limit_kb,
                pids_limit=limit.pids_limit,
                output_limit_in_bytes=limit.output_limit_in_bytes,
                repetitions=limit.repetitions,
            )
            for language_id, limit in language_limits.items()
        },
        custom_validator=None,
        sha256={},
        editorial=None,
    )

    interactions = tuple(
        PackagedInteraction(interaction.transcript, interaction.explanation)
        for interaction in sorted(problem.sample_interactions, key=lambda item: item.ordinal)
        if interaction.hidden_at is None
    )
    return ProblemPackage(
        metadata=metadata,
        statement=PackageStatement(
            kind="md" if active_statement.suffix == ".md" else "pdf",
            path=active_statement,
            text=None,
        ),
        test_cases=cases,
        image=image,
        # Gated on the stored strategy, not merely on a row existing: a standard
        # problem carrying a stale validator row would otherwise export a
        # 'standard' declaration alongside validator/ members, which is a version-2
        # package this build's own reader refuses.
        validator=(
            PackagedValidator(validator_source.language_id, validator_source.source)
            if interactive and validator_source is not None
            else None
        ),
        interactions=interactions if interactive else (),
        warnings=(),
        editorial=problem.editorial,
    )


def build_problem_export(
    problem: Problem,
    testcase_dir: Path,
    statement_dir: Path,
    destination: Path,
    *,
    profile: PackageProfile,
    language_limits: dict[str, ProblemLanguageLimit] | None = None,
    require_importable: bool = True,
) -> Path:
    """Write a contest problem package to ``destination`` and return that path.

    Args:
        profile: ``"full"`` for an importable admin package, ``"public"`` for the
            contestant-facing statement bundle, which carries no ``problem.json``.
        language_limits: Required for the ``full`` profile, which exports them.
        require_importable: Whether a ``full`` package must be re-importable.
            Only archive exporters whose package is a convenience artifact —
            the contest backup exporter and the public problem-set exporter —
            pass ``False``.

    Raises:
        PackageError: If a required stored file is missing, or if an importable
            ``full`` package cannot be expressed in the current format version.
    """
    if profile == "full" and language_limits is None:
        raise ValueError("language_limits is required for the full export profile.")
    package = problem_to_package(problem, testcase_dir, statement_dir, language_limits or {})
    return build_package(package, destination, profile=profile, require_importable=require_importable)
