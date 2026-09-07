#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Which contest teams are showing signs of life right now.

The scoreboard marks the teams nobody is sitting at, and it used to answer that
from `login_history` alone: a team with no sign-in since `contests.start_time`
was reported absent. That proxy only held while signing in at the gun was
effectively required. It is not, and **Start contest now** makes the mismatch
universal -- moving the start to *now* turns every warm-up login into a login
"before the start", so a room full of working teams is marked absent at the
instant the contest opens (#219).

The missing signal is activity, and this module supplies it by reusing
`shared.services.user_presence` under the ``contest`` identity domain -- the
domain that service was written to accept, saying so in its own docstring. The
domain constant is imported from the shared reader rather than redeclared here:
the writer and the reader naming it differently would fail silently, as an
always-absent scoreboard.

**It rides the contest clock, not a stream and not the keepalive.** Every
contest page polls `GET /c/{slug}/clock` once a minute through `_base.html`, and
that request is authenticated, so presence needs no new endpoint, no new SSE
stream, and no client change at all. The session keepalive would have been the
obvious carrier and is the wrong one: its cadence is derived from the token
lifetime and fires every 15 minutes at the default, far too coarse to tell an
occupied seat from an empty one.

Marking is deliberately narrow:

* **Teams only.** No other role is ever marked absent on a scoreboard, so
  marking staff would buy nothing and put a Valkey write on every
  administrator's request.
* **``GET`` only**, as Arena does. A `POST` is just as good evidence, but the
  clock guarantees a `GET` from every open page within the minute, so counting
  writes as well would add round trips without shortening the gap.
* **Best-effort.** `mark_user_online` swallows a Valkey outage, and the read
  side degrades to "everyone offline", which lands back on exactly the
  sign-in-window behaviour this replaces rather than on a screen of false
  alarms.
* **Logout clears it.** The marker otherwise outlives the session by its TTL
  (three minutes at the default), which is the right price for a closed laptop
  lid but the wrong one for a team that pressed **Logout**: that is the one
  moment the server knows for certain the seat is released, so the logout route
  drops the marker at once instead of letting the team status map show the seat
  occupied for another three minutes.
* **The marker carries the address.** The live key's value is the address the
  request came from, so the team status map can tell staff *where* a team is
  working from right now. It is never used to decide *whether* the team is
  online -- that is the key existing -- and a request with no usable address
  stores the reader-neutral ``"1"``.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.network_utils import NetworkService
from shared.services.team_absence_status import CONTEST_PRESENCE_DOMAIN
from shared.services.user_presence import mark_user_offline, mark_user_online
from web.config import settings
from web.services.session_guard import load_request_contest_user
from web.services.session_service import get_validated_auth_token


async def mark_contest_presence(request: Request, session: AsyncSession) -> None:
    """Record that the request's team is active, if it is one.

    Args:
        request: The authenticated request. Its validated token names the user,
            and its method decides whether this is a marking opportunity.
        session: The request's database session, shared with the actor resolver
            so the user row is loaded once per request rather than twice.

    Returns:
        None. Every reason not to mark -- presence disabled, a write method, no
        Valkey runtime, a token that names no contest user, a non-team role --
        returns quietly, because presence is an optimisation of a display hint
        and must never be able to fail a request.
    """
    if not settings.PRESENCE_ENABLED or request.method != "GET":
        return
    runtime = getattr(request.app.state, "valkey_runtime", None)
    if runtime is None:
        return

    result = get_validated_auth_token(request)
    if result is None:
        return
    contest_id = (result.extra_data or {}).get("contest_id")
    if not isinstance(contest_id, str) or not contest_id:
        return

    user = await load_request_contest_user(request, session, username=result.sub, contest_id=contest_id)
    if user is None or user.role is not RoleEnum.TEAM:
        return

    await mark_user_online(
        runtime,
        domain=CONTEST_PRESENCE_DOMAIN,
        user_id=str(user.id),
        ttl_seconds=settings.PRESENCE_TTL_SECONDS,
        value=NetworkService.get_ip_from_request(request) or "1",
    )


async def clear_contest_presence(request: Request, user_id: str | None) -> None:
    """Drop a user's presence marker on explicit logout.

    Args:
        request: The logout request, for the Valkey runtime.
        user_id: The actor the token resolved to, or ``None`` when it did not
            resolve. Any role may be passed: a marker that was never written
            makes the removal a no-op, so the caller need not know the role.

    Returns:
        None. Presence disabled, no runtime, or no actor all return quietly,
        and a Valkey outage is swallowed by the shared writer -- a logout must
        never fail because a display hint could not be cleared.
    """
    if user_id is None or not settings.PRESENCE_ENABLED:
        return
    runtime = getattr(request.app.state, "valkey_runtime", None)
    if runtime is None:
        return
    await mark_user_offline(runtime, domain=CONTEST_PRESENCE_DOMAIN, user_id=user_id)
