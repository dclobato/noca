#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""No package under `scripts/` may share a name with a workspace package.

Running a script by path -- the way every script in `scripts/` documents itself
and the way the containers invoke them -- puts `scripts/` first on `sys.path`. A
directory there with an `__init__.py` is a regular package, so it wins the import
over the real top-level one of the same name and every `from <name>.x import y`
in that process resolves against the wrong tree.

`scripts/web/` did exactly that: it shadowed `web`, so
`scripts/validate_email_templates.py web ...` died on `ModuleNotFoundError` while
the same command for `arena` worked, because `scripts/arena/` has no
`__init__.py` and is therefore only a namespace portion. Deleting the marker
fixed it, and this test is what keeps it deleted -- reintroducing one would break
a script nobody is going to run until an operator does.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _workspace_packages() -> set[str]:
    """Return the top-level import names the uv workspace provides."""
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    members = manifest["tool"]["uv"]["workspace"]["members"]
    return {Path(member).name for member in members}


def test_no_script_package_shadows_a_workspace_package() -> None:
    """A directory under `scripts/` may share a module name only as a namespace."""
    workspace = _workspace_packages()
    shadowing = sorted(
        directory.name
        for directory in SCRIPTS.iterdir()
        if directory.is_dir() and directory.name in workspace and (directory / "__init__.py").exists()
    )

    assert shadowing == [], (
        f"scripts/{{{','.join(shadowing)}}}/__init__.py makes these regular packages, which shadow the "
        "workspace packages of the same name whenever a script is run by path. Delete the "
        "__init__.py: a namespace directory imports the same way and does not shadow."
    )
