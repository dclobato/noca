#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for _load_test_cases() CRLF normalization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autojudge.submission_job import _load_test_cases


def _write_tc(base: Path, ordinal: int, in_data: bytes, out_data: bytes) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{ordinal:03d}.in").write_bytes(in_data)
    (base / f"{ordinal:03d}.out").write_bytes(out_data)


@pytest.fixture()
def tc_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_load_test_cases_normalizes_crlf(tc_dir: Path) -> None:
    _write_tc(tc_dir / "prob-1", 1, b"P\r\n", b"YES\r\n")

    with patch("autojudge.submission_job.settings") as mock_settings:
        mock_settings.contest_testcase_dir = str(tc_dir)
        cases = _load_test_cases("prob-1")

    assert cases == [(b"P\n", b"YES\n")]


def test_load_test_cases_normalizes_lone_cr(tc_dir: Path) -> None:
    _write_tc(tc_dir / "prob-1", 1, b"A\rB\r", b"C\r")

    with patch("autojudge.submission_job.settings") as mock_settings:
        mock_settings.contest_testcase_dir = str(tc_dir)
        cases = _load_test_cases("prob-1")

    assert cases == [(b"A\nB\n", b"C\n")]


def test_load_test_cases_preserves_empty_line(tc_dir: Path) -> None:
    _write_tc(tc_dir / "prob-1", 1, b"a\r\n\r\nb\r\n", b"1\r\n")

    with patch("autojudge.submission_job.settings") as mock_settings:
        mock_settings.contest_testcase_dir = str(tc_dir)
        cases = _load_test_cases("prob-1")

    assert cases[0][0] == b"a\n\nb\n"


def test_load_test_cases_already_lf_unchanged(tc_dir: Path) -> None:
    _write_tc(tc_dir / "prob-1", 1, b"clean\n", b"ok\n")

    with patch("autojudge.submission_job.settings") as mock_settings:
        mock_settings.contest_testcase_dir = str(tc_dir)
        cases = _load_test_cases("prob-1")

    assert cases == [(b"clean\n", b"ok\n")]
