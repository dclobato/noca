#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-request lookup of the mandatory announcement a user still has to acknowledge.

A required announcement must pop up on every page until the user acknowledges
it, so this cannot live in the login redirect chain (terms, 2FA, forced
password change), whose redirects fire once. It is instead an app-level
dependency, registered beside the authentication gate, that runs on **HTML page
loads of authenticated users** and leaves the answer in
``request.state.pending_required_announcement`` for ``_base.html`` to render.

The gate comes first and the session is opened only past it: JSON polls, SSE
streams, presence heartbeats, HTMX fragments, ``POST`` re-renders and the
``/auth`` pages neither run the query nor borrow a connection for it. A ``POST``
that re-renders a page therefore shows no modal for that one render; the next
``GET`` restores it.

Past the gate, a second short-circuit answers the common case without a
session: ``arena.services.required_announcement_cache`` remembers per process,
for a short TTL, whether any required Arena announcement exists at all. Only
when one does is the per-user query run.
"""

from __future__ import annotations

import logging

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from arena.services.required_announcement_cache import (
    required_announcements_exist,
    required_announcements_known_absent,
)
from shared.services.announcement_acknowledgment_service import pending_required_announcement

logger = logging.getLogger(__name__)

_STATE_ATTRIBUTE = "pending_required_announcement"


def _is_html_page_load(request: Request) -> bool:
    """Return whether the request is a browser navigation that renders ``_base.html``."""
    if request.method != "GET":
        return False
    if "text/html" not in request.headers.get("accept", ""):
        return False
    if request.headers.get("hx-request"):
        return False
    path = request.url.path
    return not (path == "/auth" or path.startswith("/auth/"))


async def load_pending_required_announcement(request: Request) -> None:
    """Store the user's oldest pending required announcement on ``request.state``.

    Reads only ``request.state.validated_token`` (populated by
    ``ArenaAuthMiddleware``) to learn the user id, so an anonymous request costs
    nothing. When this process already knows that no required announcement
    exists, no session is opened either. A database failure is logged and
    treated as "nothing pending": the pop-up is not worth failing the page, and
    the page's own queries will surface a real outage.

    Args:
        request: Incoming request.
    """
    setattr(request.state, _STATE_ATTRIBUTE, None)
    if not _is_html_page_load(request):
        return
    validation = getattr(request.state, "validated_token", None)
    user_id = getattr(validation, "sub", None) if validation is not None else None
    if not user_id:
        return
    if required_announcements_known_absent():
        return
    session_factory = request.app.state.arena_db_session
    try:
        async with session_factory() as session:
            if not await required_announcements_exist(session):
                return
            pending = await pending_required_announcement(session, user_id=str(user_id))
    except SQLAlchemyError:
        logger.warning("Could not look up pending required announcements for user %s", user_id, exc_info=True)
        return
    setattr(request.state, _STATE_ATTRIBUTE, pending)
