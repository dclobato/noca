#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for upload spooling, temporary export paths, and download filenames."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import UploadFile

from shared.services.problem_package import PackageError
from shared.services.problem_package.upload import (
    safe_package_filename,
    spool_upload,
    temporary_package_path,
)


def _upload(data: bytes, name: str = "package.zip") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data))


@pytest.mark.asyncio
async def test_spooled_upload_lands_on_disk_and_is_removed_afterwards(tmp_path: Path) -> None:
    payload = b"x" * (200 * 1024)

    async with spool_upload(_upload(payload), directory=tmp_path) as path:
        assert path.read_bytes() == payload
        spooled = path

    assert not spooled.exists()


@pytest.mark.asyncio
async def test_oversized_upload_is_refused_while_streaming(tmp_path: Path) -> None:
    payload = b"x" * (256 * 1024)

    with pytest.raises(PackageError, match="larger than the"):
        async with spool_upload(_upload(payload), directory=tmp_path, max_bytes=128 * 1024):
            pass  # pragma: no cover - the ceiling raises before the body runs

    # Nothing survives the refusal, not even the partial spool.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_the_upload_is_closed_on_every_exit_path(tmp_path: Path) -> None:
    upload = _upload(b"data")

    async with spool_upload(upload, directory=tmp_path):
        pass

    assert upload.file.closed


def test_temporary_package_path_is_left_for_the_caller(tmp_path: Path) -> None:
    with temporary_package_path(directory=tmp_path) as destination:
        destination.write_bytes(b"zip")

    # Deliberately *not* removed here: a FileResponse's background task owns it.
    assert destination.exists()
    destination.unlink()


def test_temporary_package_path_is_removed_when_the_builder_fails(tmp_path: Path) -> None:
    with (
        pytest.raises(RuntimeError, match="build failed"),
        temporary_package_path(directory=tmp_path) as destination,
    ):
        destination.write_bytes(b"partial zip")
        raise RuntimeError("build failed")

    assert not destination.exists()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("A + B", "A_B.zip"),
        ("Árvore Binária", "Arvore_Binaria.zip"),
        ("../../etc/passwd", "etc_passwd.zip"),
        ("...", "problem.zip"),
        ("", "problem.zip"),
        ("///", "problem.zip"),
        ("keep-these_ones", "keep-these_ones.zip"),
    ],
)
def test_safe_package_filename(title: str, expected: str) -> None:
    assert safe_package_filename(title) == expected


def test_safe_package_filename_is_bounded() -> None:
    name = safe_package_filename("x" * 500)

    assert len(name) <= 100
    assert name.endswith(".zip")


def test_safe_package_filename_honours_a_custom_suffix() -> None:
    assert safe_package_filename("Maze", suffix="-public.zip") == "Maze-public.zip"
