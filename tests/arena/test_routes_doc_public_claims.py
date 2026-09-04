#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Guard: ``arena/docs/ROUTES.md`` may only call a route public when the gate agrees.

Arena is default-deny: ``enforce_arena_authentication`` requires a session for
every path outside the allowlist in ``arena/dependencies/access_control.py``,
regardless of what a handler's own dependencies say. Issue #160 found the route
document contradicting that in several places (``/live*``, the sample-testcases
ZIP, user media, affiliation logos, the ranking and public-profile sections),
and the rate-limit audit inherited the errors. This test reads every route row
whose section preamble or description claims anonymous access and checks the
path against the real allowlist, so the two cannot drift apart again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

import arena.main
from arena.dependencies.access_control import _is_public_arena_path

_DOC = Path(__file__).resolve().parents[2] / "arena" / "docs" / "ROUTES.md"

#: Wording that asserts a route needs no session. Case-insensitive.
_PUBLIC_CLAIM = re.compile(
    r"no (?:authentication|login) required|requires no authentication|without (?:a )?login|public, no-login",
    re.IGNORECASE,
)
_ROW = re.compile(r"^\|\s*`(?P<method>[A-Z]+)`\s*\|\s*`(?P<path>/[^`]*)`\s*\|(?P<rest>.*)$")
_HEADING = re.compile(r"^#{2,3} ")


def _claimed_public_rows() -> list[tuple[str, str]]:
    """Return ``(path, where)`` for every route row the document presents as anonymous.

    A row is a claim when its own description says so, or when the preamble of
    the section it sits in says so (the preamble applies to every row below it).
    """
    claims: list[tuple[str, str]] = []
    section = ""
    section_claims = False
    preamble: list[str] = []
    for number, line in enumerate(_DOC.read_text(encoding="utf-8").splitlines(), start=1):
        if _HEADING.match(line):
            section = line.strip("# ").strip()
            preamble = []
            section_claims = False
            continue
        row = _ROW.match(line)
        if row is None:
            if line.strip() and not line.startswith("|"):
                preamble.append(line)
                section_claims = bool(_PUBLIC_CLAIM.search(" ".join(preamble)))
            continue
        if section_claims or _PUBLIC_CLAIM.search(row.group("rest")):
            for path in row.group("path").split("`, `"):
                claims.append((path.strip("` "), f"{section} (line {number})"))
    return claims


def _sample_path(template: str) -> str:
    """Substitute a plausible value for each ``{param}`` so the allowlist can be asked."""
    return re.sub(r"\{[^}]+\}", "1", template)


def test_document_makes_public_claims_at_all() -> None:
    """The parser must find the sections that really are public, or it guards nothing."""
    paths = {path for path, _ in _claimed_public_rows()}
    assert "/dashboard" in paths
    assert len(paths) >= 3


def test_every_public_claim_in_routes_md_is_on_the_allowlist() -> None:
    offenders = [
        f"{path} -- {where}" for path, where in _claimed_public_rows() if not _is_public_arena_path(_sample_path(path))
    ]
    assert not offenders, (
        "arena/docs/ROUTES.md calls these routes public, but enforce_arena_authentication "
        "gates them by login (only arena/dependencies/access_control.py decides):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", ["/", "/dashboard", "/health", "/problems", "/legal/terms", "/help/rating"])
def test_allowlisted_paths_are_real_registered_routes(path: str) -> None:
    """The allowlist must name routes that exist, or the document's public section is fiction."""
    registered = {route.path for route in arena.main.app.routes if isinstance(route, APIRoute)}
    assert path in registered
