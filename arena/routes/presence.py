#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Authenticated Arena online-presence endpoints.

Two JSON endpoints back the green-dot avatar indicator:

* ``POST /arena/presence/heartbeat`` refreshes the current user's live marker.
* ``POST /arena/presence/status`` returns the online state for a batch of ids.

Both require an authenticated Arena user.  Presence information is never
disclosed to anonymous visitors: a guest receives ``401`` and no presence data,
and the authentication check runs before the request body is parsed or Valkey is
touched.  Presence lives entirely in Valkey (best-effort, no database writes).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from arena.config import settings
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from shared.services.user_presence import get_users_online_map, mark_user_online

router = APIRouter(prefix="/arena/presence", tags=["arena-presence"])

CurrentArenaUser = Annotated[ArenaUser | None, Depends(get_current_arena_user)]

#: Identity domain for Arena users; namespaces presence keys away from the Web
#: (contest) identity domain so the same shared service can serve both modules.
ARENA_PRESENCE_DOMAIN = "arena"


def _require_user(current_user: ArenaUser | None) -> ArenaUser:
    """Return an authenticated user or raise 401 (no presence data leaked)."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return current_user


@router.post("/heartbeat", name="arena_presence_heartbeat")
async def arena_presence_heartbeat(
    request: Request,
    current_user: CurrentArenaUser,
) -> dict[str, Any]:
    """Mark the current Arena user online for ``PRESENCE_TTL_SECONDS``."""
    user = _require_user(current_user)
    if not settings.PRESENCE_ENABLED:
        return {"ok": False, "enabled": False}

    await mark_user_online(
        request.app.state.valkey_runtime,
        domain=ARENA_PRESENCE_DOMAIN,
        user_id=str(user.id),
        ttl_seconds=settings.PRESENCE_TTL_SECONDS,
    )
    return {"ok": True, "enabled": True}


@router.post("/status", name="arena_presence_status")
async def arena_presence_status(
    request: Request,
    current_user: CurrentArenaUser,
) -> dict[str, Any]:
    """Return ``{"online": [user_id, ...]}`` — only the online ids in the batch.

    The client already knows every id it queried, so any id absent from the list
    is offline. Returning only the online subset keeps the payload small, since
    online users are usually a minority of the avatars on a page.

    Requires an authenticated Arena user; guests receive ``401`` before the
    request body is read.
    """
    _require_user(current_user)
    if not settings.PRESENCE_ENABLED:
        return {"enabled": False, "online": []}

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    raw_ids = payload.get("ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="'ids' must be a list")

    user_ids = [str(raw_id) for raw_id in raw_ids if isinstance(raw_id, str | int)]
    online_map = await get_users_online_map(
        request.app.state.valkey_runtime,
        domain=ARENA_PRESENCE_DOMAIN,
        user_ids=user_ids,
    )
    return {"enabled": True, "online": [uid for uid, is_online in online_map.items() if is_online]}
