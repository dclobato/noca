#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Authenticated session keepalive for open Web pages.

Web sessions slide: ``AuthTokenRefreshMiddleware`` rotates the auth cookie once a
request arrives inside the token's half-life refresh window. A page left open
makes no such request, so a long edit -- a problem statement, a clarification
answer -- used to end at the login page, and because the expired-session
redirect turns a ``POST`` into a ``GET``, the form body went with it.

This endpoint is that missing request. Its handler is deliberately inert: no
Valkey, no request body, and no actor of its own. The rotation is a side effect
of the request being authenticated at all, since the global
``enforce_web_default_auth`` dependency calls ``mark_auth_refresh_eligible`` for
every authenticated non-public path. Keeping the path off the public allowlist is
therefore the whole of its authentication.

That inertness is exactly why the single-session policy is applied by the global
dependency rather than by the actor resolvers: this route resolves nobody, so a
rule written into the resolvers would have left the one endpoint whose whole
purpose is to extend a session as the one endpoint that never checked whether the
session still exists. A superseded or foreign-address session is bounced here
like anywhere else, and is never handed a fresh cookie.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["session"])


@router.post("/session/heartbeat", name="web_session_heartbeat")
async def web_session_heartbeat() -> dict[str, bool]:
    """Rotate the caller's sliding session and report success.

    Returns:
        A minimal acknowledgement payload. The refreshed cookie, when one is due,
        travels on the response headers rather than in this body.
    """
    return {"ok": True}
