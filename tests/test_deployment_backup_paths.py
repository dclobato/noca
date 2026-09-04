#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The sample stack's host data directories are the backup script's contract.

``scripts/backup_noca.sh`` archives a fixed list of directory names relative to
the project directory, and ``scripts/restore_noca.sh`` puts them back. Nothing
connects that list to the bind mounts in ``docker-compose.yml.sample`` except
these checks: rename a mount and the backup keeps succeeding, archiving a
directory nothing writes to, and the loss only surfaces on the day someone
restores. That is the worst possible failure mode for a backup, so the two files
are held together here.

The check is on the interpolation *default* of ``NOCA_DATA_ROOT``, because the
script does not read that variable at all -- which is exactly why the default is
``.`` and why the templates say so.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml.sample"
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup_noca.sh"
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "restore_noca.sh"

#: ``${NOCA_DATA_ROOT:-.}/problem_testcases:/data/problem_test_cases:ro``
BIND_MOUNT = re.compile(r"^\$\{NOCA_DATA_ROOT:-(?P<default>[^}]*)\}/(?P<host>[^:]+):")


def _archived_data_paths() -> list[str]:
    """The directory names ``backup_noca.sh`` tars, in declaration order."""
    text = BACKUP_SCRIPT.read_text(encoding="utf-8")
    declaration = re.search(r"FILESYSTEM_DATA_PATHS=\((?P<names>[^)]*)\)", text)
    assert declaration is not None, "backup script no longer declares FILESYSTEM_DATA_PATHS"
    return declaration.group("names").split()


def _data_root_mounts(compose: dict[str, Any]) -> dict[str, str]:
    """Host directory name -> the service that mounts it, for every data mount."""
    mounts: dict[str, str] = {}
    for service, definition in compose["services"].items():
        for volume in definition.get("volumes") or []:
            match = BIND_MOUNT.match(volume)
            if match:
                mounts.setdefault(match.group("host"), service)
    return mounts


def test_every_archived_directory_is_mounted_by_the_sample_stack() -> None:
    """A name the backup tars must be a directory the stack actually writes."""
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    mounted = _data_root_mounts(compose)
    missing = [name for name in _archived_data_paths() if name not in mounted]

    assert missing == [], (
        f"backup_noca.sh archives {missing}, which no service in "
        "docker-compose.yml.sample mounts -- the backup would capture nothing"
    )


def test_the_data_root_default_keeps_those_directories_where_the_script_looks() -> None:
    """The mounts resolve under the project directory with no ``.env`` at all.

    ``backup_noca.sh`` tars relative to ``$PROJECT_DIR`` and never reads
    ``NOCA_DATA_ROOT``, so any default other than ``.`` would put the live data
    somewhere the backup does not look.
    """
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    defaults = {
        BIND_MOUNT.match(volume).group("default")  # type: ignore[union-attr]
        for definition in compose["services"].values()
        for volume in definition.get("volumes") or []
        if BIND_MOUNT.match(volume)
    }

    assert defaults == {"."}, f"NOCA_DATA_ROOT defaults must all be '.', found {sorted(defaults)}"


def test_the_shared_testcase_mount_is_writable_for_both_writers() -> None:
    """Web and Arena write test cases; only the judge reads them read-only."""
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    modes = {}
    for service in ("web", "arena", "autojudge"):
        for volume in compose["services"][service]["volumes"]:
            match = BIND_MOUNT.match(volume)
            if match and match.group("host") == "problem_testcases":
                modes[service] = volume.endswith(":ro")

    assert modes == {"web": False, "arena": False, "autojudge": True}, modes


def test_restore_puts_the_same_directories_back() -> None:
    """Whatever the backup archives, the restore must extract and require."""
    restore = RESTORE_SCRIPT.read_text(encoding="utf-8")
    missing = [name for name in _archived_data_paths() if name not in restore]

    assert missing == [], f"restore_noca.sh never mentions {missing}"
