#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ZIP export helpers for Arena problem test cases.

Test case content lives on the shared filesystem under
``<root>/arena/<problem_id>/NNN.in|out``; this builder reads those files
directly.  The archive layout follows Layout A used by the web module:
``in/{ordinal:03d}.in`` and ``out/{ordinal:03d}.out``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from arena.models.arena_problems import ArenaTestCase
from shared.services.testcase_files import get_testcase_path


def build_sample_testcases_zip(
    problem_id: str,
    test_cases: list[ArenaTestCase],
    testcase_dir: Path,
) -> bytes:
    """Build an in-memory ZIP archive of sample test cases (Layout A).

    Files are written as ``in/001.in`` / ``out/001.out`` (zero-padded
    three-digit ordinal), sorted by ``ordinal`` ascending.  Content is read from
    the shared filesystem; missing files are written as empty.

    This function is synchronous and CPU-bound (zipfile + deflate).
    Callers in an async context should offload it with
    ``anyio.to_thread.run_sync``.

    Args:
        problem_id: UUID of the parent problem (used to locate files on disk).
        test_cases: List of ``ArenaTestCase`` objects to include.  Only
            pass sample test cases (``is_sample=True``) — this function
            does not filter.
        testcase_dir: Arena test-case root (``<root>/arena``).

    Returns:
        bytes: The in-memory ZIP archive contents.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for tc in sorted(test_cases, key=lambda t: t.ordinal):
            name = f"{tc.ordinal:03d}"
            in_path = get_testcase_path(problem_id, tc.ordinal, "in", testcase_dir)
            out_path = get_testcase_path(problem_id, tc.ordinal, "out", testcase_dir)
            zf.writestr(f"in/{name}.in", in_path.read_bytes() if in_path.exists() else b"")
            zf.writestr(f"out/{name}.out", out_path.read_bytes() if out_path.exists() else b"")
            if tc.explanation:
                zf.writestr(f"explanation/{name}.txt", tc.explanation.encode())
    return buf.getvalue()
