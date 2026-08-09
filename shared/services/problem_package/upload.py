#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route-side helpers: spooling an upload to disk and serving a built package.

Neither direction of the package format is allowed to hold an archive in RAM, so
an import spools its upload in bounded chunks and an export writes to an owned
temporary path the route streams and then deletes.
"""

from __future__ import annotations

import os
import tempfile
import unicodedata
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import UploadFile

from shared.services.problem_package.constants import MAX_UPLOAD_BYTES
from shared.services.problem_package.errors import PackageError
from shared.services.problem_package.staging import STAGING_PREFIX

_CHUNK_BYTES = 64 * 1024
_MAX_FILENAME_CHARS = 96
_FILENAME_FALLBACK = "problem"


@asynccontextmanager
async def spool_upload(
    upload: UploadFile,
    *,
    directory: Path | None = None,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> AsyncIterator[Path]:
    """Copy an upload to an owned temporary file, enforcing its ceiling live.

    The ceiling is checked *while* streaming, so an oversized upload is refused
    without ever having been buffered whole. The upload is closed and the
    temporary file removed on every exit path.

    Raises:
        PackageError: If the upload exceeds ``max_bytes``.
    """
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f"{STAGING_PREFIX}upload-", suffix=".zip", dir=directory)
    path = Path(name)
    written = 0
    try:
        with os.fdopen(handle, "wb") as sink:
            while chunk := await upload.read(_CHUNK_BYTES):
                written += len(chunk)
                if written > max_bytes:
                    raise PackageError(
                        f"The uploaded package is larger than the {max_bytes // (1024 * 1024)} MiB limit."
                    )
                sink.write(chunk)
        yield path
    finally:
        await upload.close()
        path.unlink(missing_ok=True)


@contextmanager
def temporary_package_path(*, directory: Path | None = None) -> Iterator[Path]:
    """Yield an owned temporary path for a package the caller is about to build.

    A successful build leaves the path for a ``FileResponse`` background task.
    If building the archive raises or is cancelled, the context removes the
    partial file because no response will ever take ownership of it.
    """
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f"{STAGING_PREFIX}export-", suffix=".zip", dir=directory)
    os.close(handle)
    path = Path(name)
    try:
        yield path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def safe_package_filename(title: str, *, suffix: str = ".zip") -> str:
    """Return a platform-safe, bounded download filename derived from ``title``.

    The title is Unicode-normalized (so a decomposed accent does not survive as a
    bare combining mark), reduced to characters every filesystem accepts, and
    bounded in length. A title that leaves nothing usable falls back to a stable
    name rather than producing an empty or dot-only filename.
    """
    decomposed = unicodedata.normalize("NFKD", title)
    # Drop the combining marks NFKD split off, so "á" becomes "a" rather than an
    # "a" followed by a stray underscore standing in for the accent.
    normalized = "".join(character for character in decomposed if not unicodedata.combining(character))
    kept = [character if character.isalnum() or character in "-_" else "_" for character in normalized]
    collapsed = "".join(kept).strip("_.")
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    if not collapsed or collapsed in {".", ".."}:
        collapsed = _FILENAME_FALLBACK
    return f"{collapsed[:_MAX_FILENAME_CHARS].rstrip('_.') or _FILENAME_FALLBACK}{suffix}"
