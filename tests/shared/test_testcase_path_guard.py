#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for guarded testcase filesystem paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.services.testcase_files import get_testcase_path, save_testcase_files


def test_testcase_path_accepts_uuid_like_problem_id(tmp_path: Path) -> None:
    path = get_testcase_path("123e4567-e89b-12d3-a456-426614174000", 1, "in", tmp_path)

    assert path == tmp_path / "123e4567-e89b-12d3-a456-426614174000" / "001.in"


@pytest.mark.parametrize(
    "problem_id",
    [
        "../escape",
        "/absolute",
        "abc/def",
        "abc%2fdef",
        ".hidden",
        "",
    ],
)
def test_testcase_path_rejects_unsafe_problem_ids(tmp_path: Path, problem_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid problem id"):
        get_testcase_path(problem_id, 1, "in", tmp_path)


def test_testcase_path_rejects_root_escape_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-testcase-target"
    outside.mkdir(exist_ok=True)
    (tmp_path / "safe-id").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes configured root"):
        save_testcase_files("safe-id", 1, b"in", b"out", tmp_path)
