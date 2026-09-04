#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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

**One invariant governs every write here: a file is never written in place.**
Content goes to a temporary name in the same directory and is then renamed over
its target. That is what lets :func:`copy_testcase_files_into` seed a staging
directory with *hardlinks* instead of byte copies -- writing through a link would
modify the live file the editor is trying to protect, while renaming over one only
replaces a directory entry. Linking makes an editor action cost O(number of cases)
rather than O(total bytes), which matters because a problem may hold gigabytes of
test data and every action stages the whole directory.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

from shared.services.durable_fs import copy_file_durably, write_file_durably
from shared.tc_zip import normalize_testcase_bytes

logger = logging.getLogger(__name__)

#: Subdirectory under ``NOCA_PROBLEM_TESTCASE_DIR`` for contest (Web) problems.
CONTEST_TC_SUBDIR = "contest"
#: Subdirectory under ``NOCA_PROBLEM_TESTCASE_DIR`` for Arena problems.
ARENA_TC_SUBDIR = "arena"
_PROBLEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

#: Roots already known to reject hardlinks, so the warning is logged once each.
_NO_HARDLINK_ROOTS: set[str] = set()


def get_problem_testcase_dir(problem_id: str, testcase_dir: Path) -> Path:
    """Return the guarded directory for one problem's test-case files."""
    if not _PROBLEM_ID_RE.fullmatch(problem_id):
        raise ValueError(f"Invalid problem id for testcase path: {problem_id!r}")
    root = testcase_dir.resolve()
    base = (root / problem_id).resolve(strict=False)
    if not base.is_relative_to(root):
        raise ValueError(f"Problem testcase path escapes configured root: {problem_id!r}")
    return base


def get_testcase_path(problem_id: str, ordinal: int, ext: str, testcase_dir: Path) -> Path:
    """Return the path for one test-case file (``ext`` is ``"in"`` or ``"out"``)."""
    if ext not in {"in", "out"}:
        raise ValueError(f"Invalid testcase extension: {ext!r}")
    base = get_problem_testcase_dir(problem_id, testcase_dir)
    path = (base / f"{ordinal:03d}.{ext}").resolve(strict=False)
    root = testcase_dir.resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Testcase path escapes configured root: {problem_id!r}")
    return path


def save_testcase_files(
    problem_id: str,
    ordinal: int,
    in_bytes: bytes,
    out_bytes: bytes | None,
    testcase_dir: Path,
) -> tuple[int, int | None]:
    """Write one test case to disk, directly in the problem's live directory.

    Content is normalized to Unix line endings (LF only) before writing.

    These writes are **not** atomic and are not undoable: they are the historical
    behavior of the satellite test-case routes, which commit their rows and then
    write. The editor's Save path must not use this — it writes into a staged
    directory through :func:`write_testcase_files_into` and swaps the whole
    directory in once, so a failed commit can put the previous files back.

    Args:
        out_bytes: Expected output, or ``None`` for a custom-validator case, which
            has no expected output. Any stale ``.out`` file is then removed, so a
            problem that gains a validator stops carrying a misleading one.

    Returns:
        tuple[int, int | None]: ``(input_size_bytes, output_size_bytes)`` of the
        normalized content written to disk; the output size is ``None`` when the
        case has no expected output.
    """
    base = get_problem_testcase_dir(problem_id, testcase_dir)
    return write_testcase_files_into(base, ordinal, in_bytes, out_bytes)


def write_testcase_files_into(
    base: Path,
    ordinal: int,
    in_bytes: bytes,
    out_bytes: bytes | None,
) -> tuple[int, int | None]:
    """Write one test case into an already-resolved directory.

    ``base`` is either a problem's live test-case directory or a staged copy of
    it; the caller owns which, and has already validated it against its root.

    Returns:
        tuple[int, int | None]: The normalized on-disk sizes, output ``None`` when
        the case has no expected output.
    """
    base.mkdir(parents=True, exist_ok=True)
    in_norm = normalize_testcase_bytes(in_bytes)
    _write_atomically(base / f"{ordinal:03d}.in", in_norm)
    out_path = base / f"{ordinal:03d}.out"
    if out_bytes is None:
        out_path.unlink(missing_ok=True)
        return len(in_norm), None
    out_norm = normalize_testcase_bytes(out_bytes)
    _write_atomically(out_path, out_norm)
    return len(in_norm), len(out_norm)


def _write_atomically(target: Path, content: bytes) -> None:
    """Write ``content`` to ``target`` without ever writing through its inode.

    ``target`` may be a hardlink to the problem's live file (that is how staging
    is seeded), so opening it for writing would corrupt the very data the staged
    swap exists to protect. Writing a temporary sibling and renaming it over the
    target replaces the directory entry instead, leaving any other link -- and
    therefore the live file -- untouched.

    The bytes are flushed to the device before that rename, because the swap's
    recovery compares what is on disk against a committed database generation:
    content the kernel still held in cache when the host crashed would leave a
    committed row describing a file that is not there. Flushing the *directory*
    is the caller's job, batched once per staged directory rather than paid per
    case -- see :func:`shared.services.durable_fs.fsync_directory`.
    """
    write_file_durably(target, content)


def delete_testcase_files_in(base: Path, ordinal: int) -> None:
    """Delete one test-case pair from an already-resolved directory."""
    (base / f"{ordinal:03d}.in").unlink(missing_ok=True)
    (base / f"{ordinal:03d}.out").unlink(missing_ok=True)


def read_testcase_preview(
    problem_id: str,
    ordinal: int,
    testcase_dir: Path,
    max_bytes: int = 80,
) -> tuple[str, str]:
    """Read a short preview (first ``max_bytes`` bytes) of one test-case pair.

    Only the prefix is read from disk, so a list page showing a hundred cases
    never loads a hundred whole files.
    """
    in_path = get_testcase_path(problem_id, ordinal, "in", testcase_dir)
    out_path = get_testcase_path(problem_id, ordinal, "out", testcase_dir)
    in_data, _ = _read_prefix(in_path, max_bytes)
    out_data, _ = _read_prefix(out_path, max_bytes)
    return in_data.decode("utf-8", errors="replace"), out_data.decode("utf-8", errors="replace")


def read_testcase_output_prefix(
    problem_id: str,
    ordinal: int,
    testcase_dir: Path,
    max_bytes: int,
) -> tuple[str, bool]:
    """Read at most ``max_bytes`` of one case's expected output.

    This is the read the participant-facing pages use: it bounds both the time a
    request spends on the filesystem and how much of a secret case's answer can
    ever reach a page, whatever the file's size.

    Args:
        problem_id: Owning problem id.
        ordinal: 1-based test-case ordinal.
        testcase_dir: Domain-specific test-case root.
        max_bytes: Upper bound on bytes read; must be positive.

    Returns:
        The decoded prefix (empty when the ``.out`` is missing) and whether the
        file holds more bytes than were read.
    """
    out_path = get_testcase_path(problem_id, ordinal, "out", testcase_dir)
    data, truncated = _read_prefix(out_path, max_bytes)
    return data.decode("utf-8", errors="replace"), truncated


def _read_prefix(path: Path, max_bytes: int) -> tuple[bytes, bool]:
    """Return the first ``max_bytes`` of ``path`` and whether more remained.

    A missing file reads as empty and not truncated. One extra byte is requested
    so truncation is detected without a second ``stat`` call.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except FileNotFoundError:
        return b"", False
    return data[:max_bytes], len(data) > max_bytes


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
    shutil.rmtree(get_problem_testcase_dir(problem_id, testcase_dir), ignore_errors=True)


def renumber_testcase_files(problem_id: str, old_ordinal: int, new_ordinal: int, testcase_dir: Path) -> None:
    """Rename a test-case pair from ``old_ordinal`` to ``new_ordinal``."""
    base = get_problem_testcase_dir(problem_id, testcase_dir)
    for ext in ("in", "out"):
        src = base / f"{old_ordinal:03d}.{ext}"
        dst = base / f"{new_ordinal:03d}.{ext}"
        if src.exists():
            src.rename(dst)


def copy_testcase_files_into(problem_id: str, testcase_dir: Path, destination: Path) -> int:
    """Seed ``destination`` with a problem's current test-case files.

    This is what makes the editor's staged swap complete: an action that touches
    one case still materializes the problem's *whole* desired directory in
    staging, so promotion is a single same-filesystem rename and rollback is a
    rename back.

    Files are **hardlinked** when the filesystem allows it, because every editor
    action stages the whole directory and a problem may hold gigabytes: linking
    makes that cost O(number of cases) instead of O(total bytes). It is safe only
    because nothing in this module writes a file in place -- see
    :func:`_write_atomically`. A filesystem that refuses links (FAT/exFAT, some
    network shares, some container bind mounts) falls back to a byte copy, which
    behaves identically and is merely slower.

    Args:
        problem_id: The problem whose files are seeded.
        testcase_dir: The domain's configured test-case root.
        destination: An already-created staging directory.

    Returns:
        The number of files seeded. A problem with no files on disk yet seeds
        nothing and is not an error.
    """
    base = get_problem_testcase_dir(problem_id, testcase_dir)
    if not base.is_dir():
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    seeded = 0
    for source in sorted(base.iterdir()):
        if not source.is_file() or source.suffix not in {".in", ".out"}:
            continue
        _seed_one(source, destination / source.name, testcase_dir)
        seeded += 1
    return seeded


def _seed_one(source: Path, target: Path, testcase_dir: Path) -> None:
    """Link ``source`` to ``target``, falling back to a copy once per root.

    A link publishes bytes that are already durable, since it names the live
    file's inode. A *copy* writes new ones, so it is flushed like any other write
    the swap will promote -- a directory flush alone would leave the entry present
    and its content missing after a crash.
    """
    root = str(testcase_dir)
    if root not in _NO_HARDLINK_ROOTS:
        try:
            os.link(source, target)
            return
        except OSError:
            _NO_HARDLINK_ROOTS.add(root)
            logger.warning(
                "testcase_files: %s does not support hardlinks; staging falls back to copying, "
                "so editor actions on large problems will be slower",
                root,
            )
    copy_file_durably(source, target)


def reorder_testcase_files(problem_id: str, ordinal_map: dict[int, int], testcase_dir: Path) -> None:
    """Rename test-case files through temporary paths for an arbitrary reorder."""
    reorder_testcase_files_in(get_problem_testcase_dir(problem_id, testcase_dir), ordinal_map)


def reorder_testcase_files_in(base: Path, ordinal_map: dict[int, int]) -> None:
    """Reorder test-case files inside an already-resolved directory."""
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
