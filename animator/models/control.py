#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Typed request bodies for the authenticated reveal control API.

Only ``start-reveal`` accepts a scope, and even there the value is not trusted:
the route compares it for exact equality with the scope the operator's bearer
token resolved to, ``None`` included. Every later command derives its scope from
the credential and the stored session, so no request body carries one.

The operator token itself is **never** a field here. It travels only in the
``Authorization: Bearer`` header, so it can never reach a query string, an access
log, a validation-error echo, or an OpenAPI example.

Both models use ``extra="forbid"`` so a caller that misspells a field — or tries
to smuggle a ``site_id`` into a post-start command — gets a ``422`` instead of a
silently ignored value.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EmptyCommandRequest(BaseModel):
    """Body of the scope-free commands (``step``, ``back``, ``reset``).

    These commands take no input at all: their scope comes from the credential
    and the stored session. Declaring an explicit empty model — rather than no
    body parameter — is what makes that refusal real: without it FastAPI would
    silently discard any JSON sent, so a caller passing ``{"site_id": …}`` would
    get a ``200`` and reasonably conclude the field had been honored. With it,
    the request models are uniform and an unexpected field is a ``422``.

    Sending no body at all remains correct; the routes default the parameter to
    ``None``.
    """

    model_config = ConfigDict(extra="forbid")


class StartRevealRequest(BaseModel):
    """Body of ``POST /start-reveal``.

    Attributes:
        site_id: The site ceremony to open, or ``None`` for the contest-global
            ceremony. Must match the bearer token's own scope exactly.
        restart: Explicit opt-in to discard an active (``revealing`` or ``done``)
            session and rebuild it from scratch. Without it, starting over an
            active session is refused with ``409``; the ordinary route back to a
            startable state is ``reset``.
    """

    model_config = ConfigDict(extra="forbid")

    site_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Site scope to reveal; omit or null for the global ceremony.",
    )
    restart: bool = Field(
        default=False,
        description="Discard an active session and rebuild it from the current data.",
    )


class JumpTeamRequest(BaseModel):
    """Body of ``POST /jump-team``.

    Attributes:
        team_id: The team to bring into focus by replaying ordinary steps.
    """

    model_config = ConfigDict(extra="forbid")

    team_id: str = Field(
        min_length=1,
        max_length=64,
        description="Identifier of the team to bring into focus.",
    )
