#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The bounded expected-output reader never loads more than it is asked for."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from shared.services.testcase_files import (
    read_testcase_output_prefix,
    read_testcase_preview,
    save_testcase_files,
)


def _problem_with_case(root: Path, out_bytes: bytes) -> str:
    problem_id = str(uuid.uuid4())
    save_testcase_files(problem_id, 1, b"in\n", out_bytes, root)
    return problem_id


def test_a_short_output_is_read_whole_and_reported_complete(tmp_path: Path) -> None:
    problem_id = _problem_with_case(tmp_path, b"1\n2\n")

    text, truncated = read_testcase_output_prefix(problem_id, 1, tmp_path, 1024)

    assert (text, truncated) == ("1\n2\n", False)


def test_a_long_output_is_cut_at_the_cap_and_reported_truncated(tmp_path: Path) -> None:
    problem_id = _problem_with_case(tmp_path, b"x" * 5000 + b"\n")

    text, truncated = read_testcase_output_prefix(problem_id, 1, tmp_path, 100)

    assert len(text.encode()) == 100
    assert truncated


def test_an_output_exactly_at_the_cap_is_not_truncated(tmp_path: Path) -> None:
    problem_id = _problem_with_case(tmp_path, b"y" * 100)

    text, truncated = read_testcase_output_prefix(problem_id, 1, tmp_path, 100)

    assert (len(text), truncated) == (100, False)


def test_a_missing_output_reads_as_empty(tmp_path: Path) -> None:
    problem_id = str(uuid.uuid4())
    save_testcase_files(problem_id, 1, b"in\n", None, tmp_path)

    assert read_testcase_output_prefix(problem_id, 1, tmp_path, 10) == ("", False)


def test_a_non_positive_cap_is_refused(tmp_path: Path) -> None:
    problem_id = _problem_with_case(tmp_path, b"1\n")

    with pytest.raises(ValueError):
        read_testcase_output_prefix(problem_id, 1, tmp_path, 0)


def test_the_preview_reads_only_its_prefix(tmp_path: Path) -> None:
    problem_id = _problem_with_case(tmp_path, b"abcdef\n")

    assert read_testcase_preview(problem_id, 1, tmp_path, max_bytes=3) == ("in\n", "abc")
