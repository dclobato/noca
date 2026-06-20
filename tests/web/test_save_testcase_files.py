#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for save_testcase_files() CRLF normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from web.services.problem_service.files import save_testcase_files


@pytest.fixture()
def tc_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_save_testcase_files_normalizes_crlf(tc_dir: Path) -> None:
    save_testcase_files("prob-1", 1, b"x\r\ny\r\n", b"1\r\n2\r\n", tc_dir)

    assert (tc_dir / "prob-1" / "001.in").read_bytes() == b"x\ny\n"
    assert (tc_dir / "prob-1" / "001.out").read_bytes() == b"1\n2\n"


def test_save_testcase_files_normalizes_lone_cr(tc_dir: Path) -> None:
    save_testcase_files("prob-1", 1, b"x\ry\r", b"1\r", tc_dir)

    assert (tc_dir / "prob-1" / "001.in").read_bytes() == b"x\ny\n"
    assert (tc_dir / "prob-1" / "001.out").read_bytes() == b"1\n"


def test_save_testcase_files_preserves_empty_line(tc_dir: Path) -> None:
    save_testcase_files("prob-1", 1, b"a\r\n\r\nb\r\n", b"1\r\n", tc_dir)

    assert (tc_dir / "prob-1" / "001.in").read_bytes() == b"a\n\nb\n"


def test_save_testcase_files_already_lf_unchanged(tc_dir: Path) -> None:
    save_testcase_files("prob-1", 1, b"clean\n", b"ok\n", tc_dir)

    assert (tc_dir / "prob-1" / "001.in").read_bytes() == b"clean\n"
    assert (tc_dir / "prob-1" / "001.out").read_bytes() == b"ok\n"
