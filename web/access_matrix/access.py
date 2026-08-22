#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Which contest modules each actor reaches, and in which contest states.

This is the declared form of what ``web/docs/ROUTES.md`` used to carry as a
markdown table under "Access Matrix". The rules answer *can this actor open the
page at all*; what the actor may then do inside it is
:mod:`web.access_matrix.capabilities`.

The two axes are orthogonal on purpose, and clarifications are the case that
proves it: every authorized role may read the clarification list in any contest
state, yet a team may only *ask* while the contest is running.

The areas here are the contest modules a role can navigate to, and the set is
meant to be **complete** -- the Add User card presents it as the answer to "which
pages does this role reach", so an area missing from it reads as a page with no
restrictions rather than as an omission. Adding a contest module means adding it
here and binding its gate in ``tests/web/test_access_matrix.py``.
"""

from __future__ import annotations

from typing import Final

from web.access_matrix.models import AccessArea, AccessLevel, AccessRule, MatrixActor

#: The actors the access matrix covers, in row order.
#:
#: The chief judge gets a row of its own, which the historical markdown table
#: did not give it: there it was a parenthetical on the JUDGE/Tasks cell reading
#: "chief judge only, after-start". That phrasing is ambiguous in the one way
#: that matters -- it can be read as "a judge reaches Tasks, restricted" when the
#: truth is that a plain judge does not reach Tasks at all. Splitting the row
#: makes both cells say exactly one thing, and lets the test suite check them
#: against `can_view_tasks` instead of parsing a note.
ACCESS_ACTORS: Final[tuple[MatrixActor, ...]] = (
    MatrixActor.UBERADMIN,
    MatrixActor.ADMIN,
    MatrixActor.CHIEF_JUDGE,
    MatrixActor.JUDGE,
    MatrixActor.STAFF,
    MatrixActor.TEAM,
    MatrixActor.USER,
)

_ALWAYS = AccessRule(AccessLevel.ALWAYS)
_AFTER_START = AccessRule(AccessLevel.AFTER_START)
_NONE = AccessRule(AccessLevel.NONE)


ACCESS_AREAS: Final[tuple[AccessArea, ...]] = (
    AccessArea(
        key="scoreboard",
        label="Scoreboard",
        description="ICPC-style standings for the contest.",
        rules={
            MatrixActor.UBERADMIN: _ALWAYS,
            MatrixActor.ADMIN: _ALWAYS,
            MatrixActor.CHIEF_JUDGE: _ALWAYS,
            MatrixActor.JUDGE: _ALWAYS,
            MatrixActor.STAFF: _AFTER_START,
            MatrixActor.TEAM: _AFTER_START,
            MatrixActor.USER: _AFTER_START,
        },
    ),
    AccessArea(
        key="problems",
        label="Problems",
        description="Problem list, statements, printing and export.",
        rules={
            MatrixActor.UBERADMIN: _ALWAYS,
            MatrixActor.ADMIN: _ALWAYS,
            MatrixActor.CHIEF_JUDGE: _ALWAYS,
            MatrixActor.JUDGE: _ALWAYS,
            MatrixActor.STAFF: _AFTER_START,
            MatrixActor.TEAM: _AFTER_START,
            MatrixActor.USER: _NONE,
        },
    ),
    AccessArea(
        key="clarifications",
        label="Clarifications",
        description=(
            "Clarification list and announcements. Viewing is open in every contest state; asking is gated separately."
        ),
        rules={
            MatrixActor.UBERADMIN: _ALWAYS,
            MatrixActor.ADMIN: _ALWAYS,
            MatrixActor.CHIEF_JUDGE: _ALWAYS,
            MatrixActor.JUDGE: _ALWAYS,
            MatrixActor.STAFF: _NONE,
            MatrixActor.TEAM: _ALWAYS,
            MatrixActor.USER: _NONE,
        },
    ),
    AccessArea(
        key="runs",
        label="Runs",
        description="Submission stream and review.",
        rules={
            MatrixActor.UBERADMIN: _ALWAYS,
            MatrixActor.ADMIN: _ALWAYS,
            MatrixActor.CHIEF_JUDGE: _AFTER_START,
            MatrixActor.JUDGE: _AFTER_START,
            MatrixActor.STAFF: _NONE,
            MatrixActor.TEAM: _AFTER_START,
            MatrixActor.USER: _NONE,
        },
    ),
    AccessArea(
        key="tasks",
        label="Tasks",
        description="Balloon, print and SOS queue.",
        rules={
            MatrixActor.UBERADMIN: _ALWAYS,
            MatrixActor.ADMIN: _ALWAYS,
            MatrixActor.CHIEF_JUDGE: _AFTER_START,
            MatrixActor.JUDGE: _NONE,
            MatrixActor.STAFF: _AFTER_START,
            MatrixActor.TEAM: _AFTER_START,
            MatrixActor.USER: _NONE,
        },
    ),
    AccessArea(
        key="solution_tests",
        label="Solution tests",
        description="Run a candidate solution against a problem without touching standings.",
        rules={
            MatrixActor.UBERADMIN: _ALWAYS,
            MatrixActor.ADMIN: _ALWAYS,
            MatrixActor.CHIEF_JUDGE: _ALWAYS,
            MatrixActor.JUDGE: _ALWAYS,
            MatrixActor.STAFF: _NONE,
            MatrixActor.TEAM: _NONE,
            MatrixActor.USER: _NONE,
        },
    ),
    AccessArea(
        key="reports",
        label="Reports",
        description="Contest reports.",
        rules={
            MatrixActor.UBERADMIN: _ALWAYS,
            MatrixActor.ADMIN: _ALWAYS,
            MatrixActor.CHIEF_JUDGE: _ALWAYS,
            MatrixActor.JUDGE: _ALWAYS,
            MatrixActor.STAFF: _NONE,
            MatrixActor.TEAM: _NONE,
            MatrixActor.USER: _NONE,
        },
    ),
    AccessArea(
        key="administration",
        label="Administration",
        description="Contest, problem and user administration.",
        rules={
            MatrixActor.UBERADMIN: _ALWAYS,
            MatrixActor.ADMIN: _ALWAYS,
            MatrixActor.CHIEF_JUDGE: _NONE,
            MatrixActor.JUDGE: _NONE,
            MatrixActor.STAFF: _NONE,
            MatrixActor.TEAM: _NONE,
            MatrixActor.USER: _NONE,
        },
    ),
)


def area_by_key(key: str) -> AccessArea:
    """Return the declared area named ``key``.

    Raises:
        KeyError: If no area carries that key.
    """
    for area in ACCESS_AREAS:
        if area.key == key:
            return area
    raise KeyError(key)


def areas_for_actor(actor: MatrixActor) -> tuple[AccessArea, ...]:
    """Return every area ``actor`` reaches in at least one contest state."""
    return tuple(area for area in ACCESS_AREAS if area.rule_for(actor).allowed)
