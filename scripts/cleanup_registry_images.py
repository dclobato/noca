#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Delete out-of-retention NOCA container images from Docker Hub and GHCR.

``containers/build.sh`` publishes every release to both registries: Docker Hub
with flat names (``dclobato/noca-webapp:v15.0.1``) and GHCR with path names
(``ghcr.io/dclobato/noca/webapp:v15.0.1``), plus the floating ``latest`` and the
judge language images with their ``compile`` / ``run`` slot tags. Nothing ever
removes the old releases, so both registries grow without bound.

This script keeps the newest ``--keep-majors`` **major series of each
repository, whole** — every patch of a retained major stays available for
rollback — and deletes older majors. Floating tags (``latest``, ``compile``,
``run``) are never deleted, and any tag that does not match a known pattern is
reported and left alone.

It is a dry run unless ``--execute`` is given.

Credentials come from the environment:

- ``DOCKERHUB_USERNAME`` / ``DOCKERHUB_TOKEN`` (personal access token)
- ``GITHUB_TOKEN`` or ``GH_TOKEN`` with ``read:packages`` and ``delete:packages``

Run with:

    uv run python scripts/cleanup_registry_images.py --keep-majors 2
    uv run python scripts/cleanup_registry_images.py --keep-majors 2 --execute
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.registry_cleanup.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
