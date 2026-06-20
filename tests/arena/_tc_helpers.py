#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Test helpers for filesystem-backed Arena test cases."""

from __future__ import annotations

from arena.config import settings
from arena.models.arena_problems import ArenaTestCase
from shared.services.testcase_files import save_testcase_files


def make_arena_test_case(
    problem_id: str,
    ordinal: int,
    *,
    input_content: str = "1\n",
    output_content: str = "1\n",
    is_sample: bool = False,
    explanation: str | None = None,
) -> ArenaTestCase:
    """Write test-case files to the Arena root and return a matching ORM row.

    Mirrors the production filesystem-backed model: content lives on disk under
    ``<root>/arena/<problem_id>/NNN.in|out`` and the DB row stores only metadata
    plus the normalized on-disk byte sizes.
    """
    in_size, out_size = save_testcase_files(
        problem_id,
        ordinal,
        input_content.encode("utf-8"),
        output_content.encode("utf-8"),
        settings.PROBLEM_TESTCASE_DIR,
    )
    return ArenaTestCase(
        problem_id=problem_id,
        ordinal=ordinal,
        is_sample=is_sample,
        input_size_bytes=in_size,
        output_size_bytes=out_size,
        explanation=explanation,
    )
