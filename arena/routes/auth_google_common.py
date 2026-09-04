#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for the Arena Google sign-in routes.

Not a route module -- contains no router.

The ``/auth`` prefix is already in the Arena public allowlist
(``arena.dependencies.access_control._PUBLIC_PREFIXES``), so these routes are
reachable without a session by construction. That is what the flow needs, and it
is also why every handler must gate itself: :func:`require_google_client` is the
first thing each one calls.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.services import google_oauth_service
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)

# Session keys owned by the Google flow. Both are short-lived markers consumed by
# the handler that reads them, following the pending_tos_uid / pending_age_uid
# precedent rather than minting a JWT for state that never leaves the session.
PENDING_LINK_UID = "pending_google_link_uid"
PENDING_SIGNUP_UID = "pending_google_signup_uid"
# Distinct from PENDING_SIGNUP_UID: that one means "resume the completion form";
# this one means "the form is done, a guardian has not yet consented." Keeping
# them separate keys is what lets the completion GET route and the waiting-page
# GET route each trust their own marker without inferring intent from account state.
PENDING_CONSENT_UID = "pending_google_consent_uid"
PENDING_NEXT_URL = "pending_google_next"
# "I already have an account": the never-completed Google-first signup whose
# identity the visitor wants moved to the account they are about to sign in to
# with a password. Distinct from PENDING_SIGNUP_UID, which it replaces, because
# the two mean opposite things -- "finish creating this account" versus "this
# account should not exist". It is read only by an authenticated confirmation
# page, after the whole ordinary login chain has run, and it is time-bounded
# (PENDING_EXISTING_SET_AT) so a marker planted on a shared browser cannot wait
# indefinitely for whoever logs in next.
PENDING_EXISTING_UID = "pending_google_existing_uid"
PENDING_EXISTING_SET_AT = "pending_google_existing_set_at"
EXISTING_ACCOUNT_MARKER_MAX_AGE_SECONDS = 30 * 60

# Deliberately generic. A failed Google sign-in must not distinguish an unverified
# address from a rejected token from an unknown account, since the callback is
# anonymous and publicly reachable.
GENERIC_FAILURE_MESSAGE = "We could not complete the Google sign-in. Please try again."


def require_google_client(request: Request) -> Any:
    """Return the Google OAuth client, or raise 404 when the feature is off.

    A deployment that has not configured Google sign-in should be
    indistinguishable from one where these routes do not exist, so a disabled
    feature answers the same 404 an unknown path does rather than a 503 that
    advertises the feature's existence.

    Args:
        request: The active request.

    Returns:
        Any: The registered Authlib client.

    Raises:
        HTTPException: 404 when Google sign-in is disabled or unconfigured.
    """
    client = getattr(request.app.state, "google_oauth", None)
    if client is None or not settings.GOOGLE_OAUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not found.")
    return client


def public_base_url(request: Request) -> str:
    """Return the deployment's public base URL, without a trailing slash."""
    return settings.ARENA_URL_BASE or str(request.base_url).rstrip("/")


def callback_redirect_uri(request: Request) -> str:
    """Return the absolute redirect URI Google is configured with."""
    return google_oauth_service.google_redirect_uri(request, public_base_url(request))


def take_session_value(request: Request, key: str) -> str | None:
    """Pop a session marker, returning it only when it is a non-empty string.

    Consumed rather than read: a marker left behind by an abandoned flow must not
    be able to change the meaning of a later, unrelated callback.

    Args:
        request: The active request.
        key: Session key to consume.

    Returns:
        str | None: The stored value, or None when absent or malformed.
    """
    value = request.session.pop(key, None)
    return value if isinstance(value, str) and value else None


def peek_session_value(request: Request, key: str) -> str | None:
    """Read a session marker without consuming it, for markers a page revisits.

    Unlike :func:`take_session_value`, this is for state a GET page and its own
    POST actions (a resend, a refresh) both need to see again -- the marker is
    cleared explicitly once its flow actually resolves, not on every read.

    Args:
        request: The active request.
        key: Session key to read.

    Returns:
        str | None: The stored value, or None when absent or malformed.
    """
    value = request.session.get(key)
    return value if isinstance(value, str) and value else None


async def record_google_event(
    session: AsyncSession,
    request: Request,
    *,
    event_type: str,
    severity: str = "info",
    user_id: str | None = None,
    actor_label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record a Google-flow security event.

    Never pass the ID token, the access token, or the Google subject identifier
    in ``metadata``: the subject is a stable cross-site identifier for a real
    person and the security-events log is read by administrators.

    Args:
        session: Active async database session; the caller commits.
        request: The active request.
        event_type: One of the ``google_*`` event types.
        severity: Event severity.
        user_id: Acting Arena user, when known.
        actor_label: Human-readable actor label, when known.
        metadata: Extra structured detail, free of any Google token or subject.
    """
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type=event_type,
        severity=severity,
        actor_user_id=user_id,
        actor_label=actor_label,
        metadata=metadata,
    )


def set_existing_account_marker(request: Request, orphan_uid: str) -> None:
    """Record that a Google-first signup should be folded into an existing account.

    Args:
        request: The active request.
        orphan_uid: Id of the never-completed Google-first account holding the
            Google identity to move.
    """
    request.session[PENDING_EXISTING_UID] = orphan_uid
    request.session[PENDING_EXISTING_SET_AT] = int(time.time())


def clear_existing_account_marker(request: Request) -> None:
    """Drop the "I already have an account" marker and its timestamp."""
    request.session.pop(PENDING_EXISTING_UID, None)
    request.session.pop(PENDING_EXISTING_SET_AT, None)


def peek_existing_account_marker(request: Request) -> str | None:
    """Return the pending orphan id while the marker is live, clearing it once stale.

    Read rather than consumed: the confirmation page and its own POST both
    need it, and it is cleared explicitly once the decision is made. A marker
    older than ``EXISTING_ACCOUNT_MARKER_MAX_AGE_SECONDS`` is discarded here,
    so the confirmation can only follow a login that happened soon after the
    visitor asked for it.

    Args:
        request: The active request.

    Returns:
        str | None: The orphan account id, or None when absent, malformed, or expired.
    """
    uid = peek_session_value(request, PENDING_EXISTING_UID)
    set_at = request.session.get(PENDING_EXISTING_SET_AT)
    if uid is None or not isinstance(set_at, int):
        clear_existing_account_marker(request)
        return None
    if time.time() - set_at > EXISTING_ACCOUNT_MARKER_MAX_AGE_SECONDS:
        clear_existing_account_marker(request)
        return None
    return uid


def existing_account_next_path(request: Request) -> str | None:
    """Return the confirmation page path when an "existing account" link is pending.

    The login page uses this as its default ``next`` so the ordinary
    post-authentication chain -- 2FA and forced password change included --
    delivers the user to the confirmation page without any of those gates
    having to know about Google.

    Args:
        request: The active request.

    Returns:
        str | None: The same-origin path of the confirmation page, or None.
    """
    if peek_existing_account_marker(request) is None:
        return None
    return request.url_for("arena_google_link_existing").path
