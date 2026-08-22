#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Vocabulary for the declared contest access and capability matrices.

These types describe *what the rules are*. They do not enforce anything: the
enforcement layers remain the route guards, the per-domain ``permissions.py``
predicates, and the ``before_flush`` hook in ``web/models/submission.py``.
Editing a declared cell changes documentation and UI, never behaviour --
``tests/web/test_access_matrix.py`` is what keeps the two honest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from shared.enumerations import RoleEnum


class MatrixActor(StrEnum):
    """An actor a matrix can talk about.

    This is deliberately *not* ``RoleEnum``. ``CHIEF_JUDGE`` is not a role: it is
    the single ``JUDGE``-role user named by ``contests.chief_judge_id``, so it has
    a column of its own and no ``RoleEnum`` member. Every other member maps 1:1.
    """

    UBERADMIN = "UBERADMIN"
    ADMIN = "ADMIN"
    CHIEF_JUDGE = "CHIEF_JUDGE"
    JUDGE = "JUDGE"
    STAFF = "STAFF"
    TEAM = "TEAM"
    USER = "USER"

    @property
    def role(self) -> RoleEnum | None:
        """Return the role this actor holds, or ``None`` for the chief judge."""
        if self is MatrixActor.CHIEF_JUDGE:
            return None
        return RoleEnum(self.value)

    @classmethod
    def for_role(cls, role: RoleEnum) -> MatrixActor:
        """Return the actor a plain role maps to.

        A ``JUDGE`` maps to :attr:`JUDGE`, never :attr:`CHIEF_JUDGE`: that
        distinction needs the contest and cannot be made from the role alone.
        """
        return cls(role.value)


class AccessLevel(StrEnum):
    """Whether an actor reaches a contest module, and in which contest states."""

    ALWAYS = "always"
    AFTER_START = "after-start"
    NONE = "none"

    @property
    def label(self) -> str:
        """Return the short rendering, matching the historical doc legend."""
        return _ACCESS_LEVEL_LABELS[self]

    @property
    def description(self) -> str:
        """Return the one-sentence explanation used as the cell tooltip."""
        return _ACCESS_LEVEL_DESCRIPTIONS[self]


_ACCESS_LEVEL_LABELS: Mapping[AccessLevel, str] = {
    AccessLevel.ALWAYS: "always",
    AccessLevel.AFTER_START: "after-start",
    AccessLevel.NONE: "—",
}

_ACCESS_LEVEL_DESCRIPTIONS: Mapping[AccessLevel, str] = {
    AccessLevel.ALWAYS: "Accessible regardless of contest state.",
    AccessLevel.AFTER_START: "Only when contest is running or is past.",
    AccessLevel.NONE: "No access; the module is not offered in the dashboard.",
}


class Grant(StrEnum):
    """Whether an actor may perform a capability."""

    ALLOWED = "allowed"
    DENIED = "denied"
    OWN = "own"
    NOT_APPLICABLE = "not_applicable"

    @property
    def label(self) -> str:
        """Return the short rendering, matching the historical doc legend."""
        return _GRANT_LABELS[self]


_GRANT_LABELS: Mapping[Grant, str] = {
    Grant.ALLOWED: "✓",
    Grant.DENIED: "—",
    Grant.OWN: "own",
    Grant.NOT_APPLICABLE: "n/a",
}


@dataclass(frozen=True, slots=True)
class AccessRule:
    """One cell of the access matrix.

    ``note`` carries the qualifier a bare level cannot express -- the canonical
    case being a ``JUDGE`` reaching Tasks only as the chief judge.
    """

    level: AccessLevel
    note: str | None = None

    @property
    def allowed(self) -> bool:
        """Return whether the actor reaches the module in any contest state."""
        return self.level is not AccessLevel.NONE

    @property
    def label(self) -> str:
        """Return the cell as rendered, qualifier included."""
        if self.note is None:
            return self.level.label
        return f"{self.level.label} ({self.note})"


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """One cell of the capability matrix."""

    grant: Grant
    note: str | None = None

    @property
    def allowed(self) -> bool:
        """Return whether the actor may perform the capability at all."""
        return self.grant in (Grant.ALLOWED, Grant.OWN)

    @property
    def label(self) -> str:
        """Return the cell as rendered, qualifier included."""
        if self.note is None:
            return self.grant.label
        return f"{self.grant.label} ({self.note})"


@dataclass(frozen=True, slots=True)
class AccessArea:
    """One contest-module column of the access matrix."""

    key: str
    label: str
    description: str
    rules: Mapping[MatrixActor, AccessRule]

    def rule_for(self, actor: MatrixActor) -> AccessRule:
        """Return the declared rule for ``actor``."""
        return self.rules[actor]


@dataclass(frozen=True, slots=True)
class Capability:
    """One row of the capability matrix.

    ``enforced_by`` names the predicate that decides this capability at runtime,
    as ``"module.function"``. It is what lets the test suite bind a declared cell
    to the function that actually answers it. ``None`` means the capability is
    gated by route role tuples alone and can only be checked structurally.
    """

    key: str
    label: str
    grants: Mapping[MatrixActor, CapabilityGrant]
    enforced_by: str | None = None

    def grant_for(self, actor: MatrixActor) -> CapabilityGrant:
        """Return the declared grant for ``actor``."""
        return self.grants[actor]


@dataclass(frozen=True, slots=True)
class CapabilityGroup:
    """A titled block of related capabilities."""

    key: str
    label: str
    capabilities: tuple[Capability, ...]
