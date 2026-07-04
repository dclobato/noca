#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Default-deny access control for the Arena application.

Registered once as a global FastAPI dependency (see ``arena/main.py``), this
gate makes every Arena route require a logged-in user with any valid role,
except a small public allowlist. Future routes are therefore protected
automatically rather than relying on each handler to opt in.

The gate is deliberately cheap: it reads only ``request.state.validated_token``
(populated by ``ArenaAuthMiddleware`` after JWT signature/expiry/action
validation — no database I/O). A present validated LOGIN token means the request
is authenticated; the full database-backed gating (token-identity match, access
gates, force-logout) still happens in routes that depend on
``get_current_arena_user``. Public paths and ``/health`` open no database
session.

When a non-public path lacks a valid session the gate raises
``HTTPException(401)``, which the Arena exception handler
(``arena/error_handlers.py``) converts to a login redirect for browsers, an
``HX-Redirect`` for HTMX requests, or a plain 401 for API clients.
"""

from fastapi import HTTPException, Request

from arena.routes.root import FAVICON_ASSETS

#: Exact-match public paths reachable without authentication.
#: ``FAVICON_ASSETS`` is keyed by filename, so iterating yields the filenames.
#: ``/problems`` is the (exact) public problem list; the ``/problems/{number}``
#: detail and sub-resources stay protected because they are different paths.
_PUBLIC_EXACT: frozenset[str] = frozenset(
    {"/", "/dashboard", "/health", "/problems"} | {f"/{name}" for name in FAVICON_ASSETS}
)

#: Public path prefixes. A path is public when it equals the prefix or begins
#: with ``"<prefix>/"`` (so ``/legalish`` is *not* matched by ``/legal``).
_PUBLIC_PREFIXES: tuple[str, ...] = ("/legal", "/help", "/auth")


def _is_public_arena_path(path: str) -> bool:
    """Return whether a request path is reachable without authentication.

    Args:
        path: The request URL path (e.g. ``request.url.path``).

    Returns:
        bool: ``True`` for allowlisted public paths, ``False`` otherwise.
    """
    if path in _PUBLIC_EXACT:
        return True
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _PUBLIC_PREFIXES)


async def enforce_arena_authentication(request: Request) -> None:
    """Require a valid Arena session for every non-public path.

    Args:
        request: The current HTTP request.

    Raises:
        HTTPException: ``401`` when the path is not public and the request has no
            valid (non-expired) session token. The Arena exception handler turns
            this into the appropriate login redirect or 401 response.
    """
    if _is_public_arena_path(request.url.path):
        return
    if getattr(request.state, "validated_token", None) is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if getattr(request.state, "token_cap_exceeded", False):
        raise HTTPException(status_code=401, detail="Session expired")
