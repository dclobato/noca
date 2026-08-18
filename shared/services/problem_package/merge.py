#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Splice one built package ZIP into a containing archive under a prefix.

Both the contest backup exporter and the public problem-set exporter build each
problem as its own version-2 package in a temporary file, then merge that
package's members into the outer archive one problem at a time. Merging one
package at a time (and deleting it right after) keeps peak disk usage at the
outer archive plus a single problem package, never the whole set twice.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

_COPY_CHUNK_BYTES = 1024 * 1024


def append_package_folder(dest_path: Path, prefix: str, package_path: Path) -> None:
    """Append every member of ``package_path`` under ``prefix`` in ``dest_path``.

    Args:
        dest_path: The outer archive, opened for appending.
        prefix: Directory prefix inside the outer archive (e.g. ``problems/001-A``).
        package_path: The built per-problem package to merge in.
    """
    with (
        zipfile.ZipFile(dest_path, "a", compression=zipfile.ZIP_DEFLATED) as archive,
        zipfile.ZipFile(package_path) as inner,
    ):
        for info in inner.infolist():
            if info.is_dir():
                continue
            with inner.open(info) as source, archive.open(f"{prefix}/{info.filename}", "w") as sink:
                shutil.copyfileobj(source, sink, length=_COPY_CHUNK_BYTES)
