#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Helpers for asserting what a compose service's process actually receives.

Configuration reaches a container two ways: the layered ``.env.<layer>.full``
templates named in ``env_file:``, and the ``environment:`` block, which overrides
them. A deployment test that reads only one of the two answers the wrong
question -- before the split every variable was in ``environment:``, and after it
almost none are.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


def template_variables(path: Path) -> set[str]:
    """Variable names one ``.env.*.full`` template defines."""
    return {
        match.group(1) for line in path.read_text(encoding="utf-8").splitlines() if (match := ASSIGNMENT.match(line))
    }


def effective_env_keys(service: dict[str, Any]) -> set[str]:
    """Every variable name the service's process sees, from either source."""
    keys = set(service.get("environment") or {})
    for entry in service.get("env_file") or []:
        keys |= template_variables(REPO_ROOT / entry)
    return keys


def stack_variables(module: str) -> set[str]:
    """Every variable the layer stack declared for ``module`` supplies."""
    manifest = tomllib.loads((REPO_ROOT / "env_layers.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    for layer in manifest["stacks"][module]:
        names |= template_variables(REPO_ROOT / f".env.{layer}.full")
    return names
