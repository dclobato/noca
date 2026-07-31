#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Cross-module reveal contracts exchanged through Valkey.

This module owns the small, animator-independent vocabulary the reveal feature
shares between the animator runtime and the Valkey transport: the ceremony
``phase`` and ``command`` literals, and the projection *nudge* published to
spectators after every durable state mutation.

The published event is deliberately a **signal, not a snapshot**. The `shared`
package cannot import the animator's derived team/problem views, and even if it
could, broadcasting them would give subscribers a second, race-prone source of
truth. :class:`RevealStateChangedEvent` therefore carries only invalidation
metadata: consumers treat every event as "the ceremony under this
``contest_id``/``scope`` changed — refetch the authoritative projection" and
must never render the event's own fields as authoritative state. Missed events
are harmless because the authoritative state is always reloadable from the store.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "REVEAL_EVENT_VERSION",
    "GLOBAL_SCOPE",
    "RevealCommand",
    "RevealPhase",
    "RevealStateChangedEvent",
]

REVEAL_EVENT_VERSION: Final = 1
"""Serialization version of the published :class:`RevealStateChangedEvent`.

Declared ``Final`` so it narrows to ``Literal[1]`` and stays assignable to the
``event_version`` field's literal type.
"""

GLOBAL_SCOPE: Final = "global"
"""Reserved scope component for a contest-global (site-less) ceremony.

A site-scoped ceremony uses the site id as its scope; the global ceremony uses
this constant. It is a valid scope component under the strict channel/key guard
in :mod:`shared.services.valkey_service.revelation`.
"""

RevealPhase = Literal["idle", "revealing", "done"]
"""The three phases of a reveal ceremony.

Single source of truth: :mod:`animator.models.reveal_session` re-imports this
rather than redeclaring it, so the persisted state and the published nudge agree
on the phase vocabulary.
"""

RevealCommand = Literal["start", "step", "back", "jump", "reset"]
"""The operator command that produced a given state mutation."""


class RevealStateChangedEvent(BaseModel):
    """An invalidation nudge published after a reveal state is durably saved.

    This is **not** a projection payload. It tells a spectator stream that the
    ceremony changed and that it should refetch the authoritative projection;
    the fields below are metadata for logging and cheap client-side filtering,
    never a substitute for the real state. See the module docstring.

    Attributes:
        event_version: Serialization version; pinned so foreign payloads reject.
        contest_id: Contest whose ceremony changed.
        scope: Ceremony scope — a site id, or ``"global"`` for the global
            ceremony. Matches the ``scope`` component of the state key and the
            channel this event is published on.
        command: The command that produced the mutation.
        phase: The ceremony phase after the mutation.
        focused_team_id: The focused team after the mutation, if any.
        revealed_count: Number of revealed submissions after the mutation.
        frozen_count: Size of the immutable frozen universe.
        published_at: UTC instant the producer built the event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_version: Literal[1] = REVEAL_EVENT_VERSION
    contest_id: str
    scope: str
    command: RevealCommand
    phase: RevealPhase
    focused_team_id: str | None
    revealed_count: int
    frozen_count: int
    published_at: datetime
