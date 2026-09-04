#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The single rule for who may open an Arena user's public profile.

Both the public-profile route (which answers 404) and every surface that
decides whether to *link* to a profile consult this predicate, so a link can
never lead where the route refuses, and a policy change lands in one place.

The age shield is applied here rather than in the route, so that one change
retroactively hides every already-public minor profile and every link to one.
The refusal stays a 404, never a 403, so a shielded profile is indistinguishable
from a user that does not exist and cannot be enumerated.
"""

from __future__ import annotations

from datetime import date

from arena.services.user_visibility_service import is_shielded
from shared.enumerations import ArenaRole

__all__ = ["can_view_public_profile"]


def can_view_public_profile(
    *,
    ativo: bool,
    public_profile: bool,
    ranking_visible: bool,
    date_of_birth: date | None,
    viewer_role: ArenaRole,
) -> bool:
    """Return whether a viewer with ``viewer_role`` may open a profile with these flags.

    Args:
        ativo: Whether the profile's account is active.
        public_profile: Whether the user opted into a public profile.
        ranking_visible: Whether the user is visible on rankings.
        date_of_birth: The profile owner's date of birth, or None when unknown.
            Required -- and keyword-only -- precisely so a call site added later
            cannot skip the age shield by omitting it: leaving it out is an
            error at type-check time rather than a silent hole.
        viewer_role: Role of the authenticated viewer.

    Returns:
        bool: True when an ``ARENA_ADMIN`` is viewing (moderation bypass), or
        when the profile is active, opted in, ranking-visible, and not
        age-shielded.
    """
    if viewer_role == ArenaRole.ARENA_ADMIN:
        return True
    return bool(ativo and public_profile and ranking_visible and not is_shielded(date_of_birth))
