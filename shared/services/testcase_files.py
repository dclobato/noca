#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Generic, model-free filesystem helpers for problem test cases.

Callers pass a domain-specific ``testcase_dir`` root (e.g. ``<root>/contest`` or
``<root>/arena``); the helpers are otherwise identical for the Web and Arena
identity domains. Files are laid out as ``<testcase_dir>/<problem_id>/<NNN>.in``
and ``<NNN>.out``, where ``NNN`` is the zero-padded 1-based ordinal.

All write paths normalize content to Unix line endings (LF only) via
``shared.tc_zip.normalize_testcase_bytes`` so the on-disk byte length matches the
size stored in the database.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shared.tc_zip import normalize_testcase_bytes

#: Subdirectory under ``NOCA_PROBLEM_TESTCASE_DIR`` for contest (Web) problems.
CONTEST_TC_SUBDIR = "contest"
#: Subdirectory under ``NOCA_PROBLEM_TESTCASE_DIR`` for Arena problems.
ARENA_TC_SUBDIR = "arena"


def get_testcase_path(problem_id: str, ordinal: int, ext: str, testcase_dir: Path) -> Path:
    """Return the path for one test-case file (``ext`` is ``"in"`` or ``"out"``)."""
    return testcase_dir / problem_id / f"{ordinal:03d}.{ext}"


def save_testcase_files(
    problem_id: str,
    ordinal: int,
    in_bytes: bytes,
    out_bytes: bytes,
    testcase_dir: Path,
) -> tuple[int, int]:
    """Write one pair of test-case files to disk.

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


def read_testcase_preview(
    problem_id: str,
    ordinal: int,
    testcase_dir: Path,
    max_bytes: int = 80,
) -> tuple[str, str]:
    """Read a short preview (first ``max_bytes`` bytes) of one test-case pair."""
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
    """Read the full UTF-8 contents of one test-case pair."""
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


def read_testcase_sizes(problem_id: str, ordinal: int, testcase_dir: Path) -> tuple[int, int]:
    """Return the on-disk byte sizes of one test-case pair via ``stat``.

    Missing files report a size of 0.
    """
    in_path = get_testcase_path(problem_id, ordinal, "in", testcase_dir)
    out_path = get_testcase_path(problem_id, ordinal, "out", testcase_dir)
    in_size = in_path.stat().st_size if in_path.exists() else 0
    out_size = out_path.stat().st_size if out_path.exists() else 0
    return in_size, out_size


def delete_testcase_files(problem_id: str, ordinal: int, testcase_dir: Path) -> None:
    """Delete one test-case pair if present."""
    get_testcase_path(problem_id, ordinal, "in", testcase_dir).unlink(missing_ok=True)
    get_testcase_path(problem_id, ordinal, "out", testcase_dir).unlink(missing_ok=True)


def delete_all_testcase_files(problem_id: str, testcase_dir: Path) -> None:
    """Delete all test-case files for one problem."""
    shutil.rmtree(testcase_dir / problem_id, ignore_errors=True)


def renumber_testcase_files(problem_id: str, old_ordinal: int, new_ordinal: int, testcase_dir: Path) -> None:
    """Rename a test-case pair from ``old_ordinal`` to ``new_ordinal``."""
    base = testcase_dir / problem_id
    for ext in ("in", "out"):
        src = base / f"{old_ordinal:03d}.{ext}"
        dst = base / f"{new_ordinal:03d}.{ext}"
        if src.exists():
            src.rename(dst)


def reorder_testcase_files(problem_id: str, ordinal_map: dict[int, int], testcase_dir: Path) -> None:
    """Rename test-case files through temporary paths for an arbitrary reorder."""
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
