#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The declared contest access and capability matrices.

Who reaches which contest module (:mod:`~web.access_matrix.access`) and what
they may do once there (:mod:`~web.access_matrix.capabilities`), as data rather
than as a markdown table. ``web/docs/ROUTES.md`` points here instead of
restating it, and the add-user form renders it so an admin can see what a role
will be able to do before granting it.

**This package enforces nothing.** The rules are enforced, as they always were,
in three layers that must all agree: the route guards, the per-domain
``permissions.py`` predicates, and the ``before_flush`` hook in
``web/models/submission.py``. Editing a cell here changes documentation and UI
and nothing else. ``tests/web/test_access_matrix.py`` is the mechanism that
keeps the declaration honest: it evaluates the real predicates named by each
capability's ``enforced_by`` and fails when they disagree with the cell.

So: change behaviour first, then update the declaration and let the test
confirm the two now say the same thing.
"""

from __future__ import annotations

from typing import Final

from web.access_matrix.access import (
    ACCESS_ACTORS,
    ACCESS_AREAS,
    area_by_key,
    areas_for_actor,
)
from web.access_matrix.capabilities import (
    CAPABILITY_ACTORS,
    CAPABILITY_GROUPS,
    all_capabilities,
    capabilities_for_actor,
    capability_by_key,
)
from web.access_matrix.models import (
    AccessArea,
    AccessLevel,
    AccessRule,
    Capability,
    CapabilityGrant,
    CapabilityGroup,
    Grant,
    MatrixActor,
)

#: Legend for the access matrix, as ``(symbol, meaning)`` pairs.
ACCESS_LEGEND: Final[tuple[tuple[str, str], ...]] = tuple((level.label, level.description) for level in AccessLevel)

#: Legend for the capability matrix, as ``(symbol, meaning)`` pairs.
CAPABILITY_LEGEND: Final[tuple[tuple[str, str], ...]] = (
    (Grant.ALLOWED.label, "Allowed."),
    (Grant.DENIED.label, "Denied."),
    (Grant.OWN.label, "Only on a resource the actor owns or holds the lock for."),
    (Grant.NOT_APPLICABLE.label, "The capability does not apply to this actor."),
)

#: Why an uberadmin is denied every capability that records who performed it.
UBERADMIN_ATTRIBUTION_NOTE: Final[str] = (
    "Uber Admin is designed as a system-wide oversight role, not as a contest "
    "participant. It can supervise contest work — for example, release a lock "
    "or rejudge a submission — but cannot perform actions that must be credited "
    "to a contest user. This separation is intentional."
)

#: Why the chief judge has a column but is not a selectable role.
CHIEF_JUDGE_NOTE: Final[str] = (
    "The Chief Judge is a designation given to one of the contest’s Judges, "
    "not a separate role. A contest with Judges must always have a Chief Judge: "
    "the first Judge is designated automatically, and an administrator can later "
    "choose another. Administrators cannot be designated Chief Judge, but they "
    "are explicitly granted equivalent authority where needed. This separation "
    "is intentional."
)

__all__ = [
    "ACCESS_ACTORS",
    "ACCESS_AREAS",
    "ACCESS_LEGEND",
    "CAPABILITY_ACTORS",
    "CAPABILITY_GROUPS",
    "CAPABILITY_LEGEND",
    "CHIEF_JUDGE_NOTE",
    "UBERADMIN_ATTRIBUTION_NOTE",
    "AccessArea",
    "AccessLevel",
    "AccessRule",
    "Capability",
    "CapabilityGrant",
    "CapabilityGroup",
    "Grant",
    "MatrixActor",
    "all_capabilities",
    "area_by_key",
    "areas_for_actor",
    "capabilities_for_actor",
    "capability_by_key",
]
