#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""How an archive's clarification rows answer "is this an announcement".

Version 4 stores ``clarifications.is_announcement`` explicitly. Versions 1 to 3 predate
the column, and an archive captured then holds exactly one signal about the question: the
role its own ``users.json`` recorded for the row's author. That inference is the rule the
application applied until this release, so replaying it reproduces exactly the
classification the archive was captured under.

The rule is therefore **explicit wins, infer only on absence**, and it is written once
here because two callers must agree on it: the integrity checker, which decides whether
the archive is well-formed, and the restorer, which decides what to write to the column.
If they disagreed, an archive would validate as one kind of row and restore as another.
An older archive that *does* carry the flag -- captured after the column landed but before
this bump -- keeps what it states rather than being re-inferred.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shared.enumerations import RoleEnum


def announcement_flag_for_backup_row(
    clarification: Mapping[str, Any],
    role_by_user_id: Mapping[str, Any],
) -> bool:
    """Return whether an archived clarification row describes an announcement.

    Args:
        clarification: The archived ``clarifications`` row.
        role_by_user_id: Archived author role keyed by the archive's own user ids.

    Returns:
        bool: The stored flag when the row states one; otherwise the legacy inference
        from the archived author's role, which is all such a row carries.
    """
    stored = clarification.get("is_announcement")
    if stored is not None:
        return bool(stored)
    team_id = clarification.get("team_id")
    if not isinstance(team_id, str):
        return False
    author_role = role_by_user_id.get(team_id)
    return author_role is not None and author_role != RoleEnum.TEAM.value
