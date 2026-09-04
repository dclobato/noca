#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Server-side half of the browser form-draft contract (``noca-form-draft.js``).

The shared script keeps a copy of a long form in the browser's ``localStorage``
so a Save that bounces off an expired session does not lose the author's work.
The browser cannot tell a successful Save from a rejected one on its own: a
``422`` re-renders the submitted values, and a successful create redirects to a
different page entirely. So the *server* states which drafts landed. A save
route calls :func:`confirm_form_draft` after its commit; the next page the
redirect lands on renders those keys through ``_base.html`` and the script
deletes exactly them.

Drafts are also scoped to the account that wrote them, through an opaque owner
token the base templates render. The token is a truncated digest of the login
identity: it is stable for the same account, carries no username or email into
browser storage, and lets the script purge another account's drafts when a
shared machine changes hands without a logout.
"""

from __future__ import annotations

import hashlib
from typing import Any

from starlette.requests import Request

SESSION_KEY = "noca_form_drafts_confirmed"
_MAX_PENDING = 8


def problem_definition_draft_key(
    module: str,
    *,
    contest_id: str | None = None,
    problem_id: str | None = None,
    validator_type: str | None = None,
) -> str:
    """Return the stable draft key of a problem definition editor.

    The key names the form the draft belongs to, so an edit of one problem never
    offers its draft to another, and a create form for one strategy never offers
    it to a create form for a different one (their field sets differ).

    Args:
        module: ``"arena"`` or ``"web"``.
        contest_id: The owning contest's id (Web only).
        problem_id: The edited problem's id, or ``None`` while creating.
        validator_type: The chosen strategy value while creating.

    Returns:
        The key the template binds with ``data-noca-draft`` and the save route
        confirms with :func:`confirm_form_draft`.

    Raises:
        ValueError: When creating without a strategy, or when neither a
            problem nor a strategy is given.
    """
    parts = [f"{module}-problem-definition"]
    if contest_id:
        parts.append(contest_id)
    if problem_id:
        parts.append(problem_id)
    elif validator_type:
        parts.extend(("new", validator_type))
    else:
        raise ValueError("A create draft key needs the validator type.")
    return ":".join(parts)


def draft_owner_token(*identity: str | None) -> str | None:
    """Return the opaque owner token for a login identity, or ``None`` when absent.

    Args:
        *identity: The parts that together name one account (module, audience,
            contest, subject). Any missing part yields no token, so a page with
            no live session never scopes drafts to a half-known identity.

    Returns:
        A 16-hex-digit digest, stable for the same parts.
    """
    if not identity or any(not part for part in identity):
        return None
    digest = hashlib.sha256("\x1f".join(str(part) for part in identity).encode("utf-8")).hexdigest()
    return digest[:16]


def confirm_form_draft(request: Request, key: str) -> None:
    """Record that the draft under ``key`` was saved, for the next rendered page.

    Stored in the signed session cookie both HTTP modules already run, so it
    survives the redirect a successful save answers with. The list is bounded:
    a key that is never rendered (an API client, a redirect to a page outside
    ``_base.html``) must not grow the cookie without limit.

    Args:
        request: The request whose save committed.
        key: The draft key the template bound with ``data-noca-draft``.
    """
    session = _session_or_none(request)
    if session is None:
        return
    pending = [item for item in session.get(SESSION_KEY, []) if isinstance(item, str) and item != key]
    pending.append(key)
    session[SESSION_KEY] = pending[-_MAX_PENDING:]


def pop_confirmed_form_drafts(request: Request) -> str:
    """Return the confirmed draft keys, space-separated, and forget them.

    Args:
        request: The request being rendered.

    Returns:
        The keys for the ``data-noca-draft-confirmed`` attribute, or ``""``.
    """
    session = _session_or_none(request)
    if session is None:
        return ""
    pending: Any = session.pop(SESSION_KEY, None)
    if not isinstance(pending, list):
        return ""
    return " ".join(item for item in pending if isinstance(item, str) and item)


def _session_or_none(request: Request) -> Any:
    """Return the request session, or ``None`` when no session middleware ran."""
    try:
        return request.session
    except AssertionError, AttributeError:
        return None
